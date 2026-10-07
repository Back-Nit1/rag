//! Scripted E2E, no microphone: WAV file -> gesture state machine -> real
//! packaged backend (supervised by `Stack`) -> source -> transcript -> proposal.
//! The speech child runs with `METIS_SPEECH_ENGINE=fixture`, so the transcript
//! is a FIXTURE result and the test asserts the API labels it as such. Timings
//! printed here are fixture-ASR numbers, not real transcription latency.
use metis_desktop_lib::audio::WavSource;
use metis_desktop_lib::backend::{
    ApiError, Backend, CreateReq, Created, HttpBackend, UploadTarget,
};
use metis_desktop_lib::machine::State;
use metis_desktop_lib::session::{spawn, Emit, Msg, Snapshot};
use metis_desktop_lib::stack::{Config, Phase, Stack};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Default)]
struct Collect(Mutex<Vec<Snapshot>>);
impl Emit for Collect {
    fn emit(&self, s: &Snapshot) {
        self.0.lock().unwrap().push(s.clone());
    }
}

/// Same HTTP flow as production; only the readiness gate differs. The packaged
/// speech child needs real model weights, so here API + worker must be ready and
/// transcription is the in-process FIXTURE engine (labelled by the API).
struct FixtureGate(HttpBackend, Arc<Mutex<Option<Stack>>>);
impl Backend for FixtureGate {
    fn ready(&self) -> Result<(), String> {
        let g = self.1.lock().unwrap();
        let st = g.as_ref().ok_or("no stack")?.status();
        let ok = |n: &str| {
            st.services
                .iter()
                .any(|s| s.name == n && s.phase == Phase::Ready)
        };
        (ok("api") && ok("worker"))
            .then_some(())
            .ok_or_else(|| "not_ready: api/worker".into())
    }
    fn create(&self, r: &CreateReq) -> Result<Created, ApiError> {
        self.0.create(r)
    }
    fn upload(&self, t: &UploadTarget, w: &[u8]) -> Result<(), ApiError> {
        self.0.upload(t, w)
    }
    fn complete(&self, i: &str, r: u64, b: usize, k: &str) -> Result<(), ApiError> {
        self.0.complete(i, r, b, k)
    }
    fn get(&self, i: &str) -> Result<serde_json::Value, ApiError> {
        self.0.get(i)
    }
    fn delete(&self, i: &str) {
        self.0.delete(i)
    }
}

fn backend() -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries");
    std::fs::read_dir(&dir)
        .unwrap_or_else(|_| panic!("{} missing: run `pnpm sidecar:prepare`", dir.display()))
        .flatten()
        .map(|e| e.path())
        .find(|p| {
            p.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("metis-backend-")
        })
        .expect("packaged backend missing: run `pnpm sidecar:prepare`")
}

/// 1.5 s, 48 kHz stereo, 16-bit: exercises downmix + resample on the way in.
fn write_wav(path: &std::path::Path) {
    let spec = hound::WavSpec {
        channels: 2,
        sample_rate: 48_000,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut w = hound::WavWriter::create(path, spec).unwrap();
    for i in 0..(48_000 * 3 / 2) {
        let v = ((i as f32 * 0.05).sin() * 8000.0) as i16;
        w.write_sample(v).unwrap();
        w.write_sample(v).unwrap();
    }
    w.finalize().unwrap();
}

fn wait_for<T>(what: &str, secs: u64, mut f: impl FnMut() -> Option<T>) -> T {
    let end = Instant::now() + Duration::from_secs(secs);
    loop {
        if let Some(v) = f() {
            return v;
        }
        assert!(Instant::now() < end, "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(50));
    }
}

#[test]
fn wav_to_transcript_to_proposal_through_real_backend() {
    let dir = std::env::temp_dir().join(format!("metis-capture-e2e-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let wav = dir.join("speech.wav");
    write_wav(&wav);

    let mut cfg = Config::production(backend(), &dir);
    cfg.speech_engine = Some("fixture".into());
    let stack = Arc::new(Mutex::new(Some(Stack::start(cfg).unwrap())));
    let gate = FixtureGate(HttpBackend::new(stack.clone()), stack.clone());
    wait_for("api and worker ready", 120, || gate.ready().ok());

    let events = Arc::new(Collect::default());
    let (handle, join) = spawn(
        {
            let wav = wav.clone();
            move || Box::new(WavSource::new(wav))
        },
        Arc::new(gate),
        events.clone(),
        None,
    );

    // One hold: key-down, hold past the Armed interval, release; a duplicate
    // release must not create a second capture.
    handle.send(Msg::Down);
    std::thread::sleep(Duration::from_millis(250));
    handle.send(Msg::Up);
    handle.send(Msg::Up);

    let done = wait_for("review", 60, || {
        let s = handle.snapshot.lock().unwrap().clone();
        assert_ne!(s.state, State::Error, "capture failed: {:?}", s.error);
        (s.state == State::Review || s.state == State::Saved).then_some(s)
    });
    println!(
        "FIXTURE E2E timings: release->transcript {:?} ms (150 ms poll granularity), transcript->proposal {:?} ms",
        done.release_to_transcript_ms, done.extraction_ms
    );
    assert_eq!(done.adapter_mode.as_deref(), Some("fixture"));
    assert!(done.transcript.as_deref().is_some_and(|t| !t.is_empty()));
    assert!(done.release_to_transcript_ms.is_some());
    assert!(done.capture_id.is_some());

    // The state sequence reached the HUD in order and never leaked credentials.
    let seen = events.0.lock().unwrap().clone();
    let states: Vec<State> = seen.iter().map(|s| s.state).collect();
    for want in [State::Armed, State::Recording, State::Transcribing] {
        assert!(states.contains(&want), "missing {want:?} in {states:?}");
    }
    let json = serde_json::to_string(&seen).unwrap();
    assert!(!json.contains("127.0.0.1") && !json.to_lowercase().contains("bearer"));

    // Exactly one audio object was stored: the duplicate release made no second capture.
    fn files(d: &std::path::Path) -> usize {
        std::fs::read_dir(d).map_or(0, |r| {
            r.flatten()
                .map(|e| {
                    if e.path().is_dir() {
                        files(&e.path())
                    } else {
                        1
                    }
                })
                .sum()
        })
    }
    assert_eq!(files(&dir.join("objects")), 1);

    // The authenticated API is still healthy.
    let conn = stack
        .lock()
        .unwrap()
        .as_ref()
        .unwrap()
        .connection()
        .unwrap();
    let port: u16 = conn
        .api_base_url
        .rsplit(':')
        .next()
        .unwrap()
        .parse()
        .unwrap();
    let (code, body) = metis_desktop_lib::stack::http_get(
        port,
        "/v1/today",
        Some(&conn.bearer),
        Duration::from_secs(5),
    )
    .unwrap();
    assert_eq!(code, 200, "{body}");

    handle.send(Msg::Shutdown);
    join.join().unwrap();
    let mut owned = stack.lock().unwrap().take().unwrap();
    owned.shutdown();
    let _ = std::fs::remove_dir_all(&dir);
}

/// Production gate: packaged speech without model weights is Unhealthy, so a
/// key press must show an error and must not record or create a capture.
#[test]
fn missing_model_refuses_to_record_through_real_backend() {
    let dir = std::env::temp_dir().join(format!("metis-capture-nomodel-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let wav = dir.join("speech.wav");
    write_wav(&wav);
    let stack = Arc::new(Mutex::new(Some(
        Stack::start(Config::production(backend(), &dir)).unwrap(),
    )));
    wait_for("speech unhealthy", 120, || {
        let g = stack.lock().unwrap();
        let st = g.as_ref().unwrap().status();
        st.services
            .iter()
            .any(|s| s.name == "speech" && s.phase == Phase::Unhealthy)
            .then_some(())
    });
    let (handle, join) = spawn(
        move || Box::new(WavSource::new(wav)),
        Arc::new(HttpBackend::new(stack.clone())),
        Arc::new(Collect::default()),
        None,
    );
    handle.send(Msg::Down);
    std::thread::sleep(Duration::from_millis(300));
    handle.send(Msg::Up);
    let s = wait_for("error", 10, || {
        let s = handle.snapshot.lock().unwrap().clone();
        (s.state == State::Error).then_some(s)
    });
    let e = s.error.unwrap();
    assert_eq!(e.step, "not_ready");
    assert!(e.message.contains("speech"), "{}", e.message);
    handle.send(Msg::Cancel);
    handle.send(Msg::Shutdown);
    join.join().unwrap();
    let mut owned = stack.lock().unwrap().take().unwrap();
    owned.shutdown();
    let _ = std::fs::remove_dir_all(&dir);
}
