//! Capture controller: owns the audio source and the gesture machine, drives
//! one logical capture at a time and publishes sanitized state snapshots.
//!
//! Snapshots (and the `capture:state` event built from them) contain state,
//! elapsed time, the failing step and message, the adapter mode, and transcript
//! text only after the API returned it. No bearer, URL, audio or file path.
use crate::audio::{encode_wav, AudioSource, Recorder, MAX_SAMPLES, TARGET_RATE};
use crate::backend::{clip, ApiError, Backend, CreateReq};
pub use crate::machine::State;
use crate::machine::{Action, Machine};
use serde::Serialize;
use serde_json::Value;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{channel, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// Shorter than this after a deliberate hold is treated as no capture.
const MIN_SAMPLES: usize = TARGET_RATE as usize / 10;
const EMIT_EVERY_MS: u64 = 250;
const POLL_EVERY: Duration = Duration::from_millis(150);
const POLL_DEADLINE: Duration = Duration::from_secs(120);
const MAX_TRANSCRIPT_CHARS: usize = 4000;

#[derive(Clone, Debug, Default, Serialize, PartialEq, Eq)]
pub struct StepError {
    pub step: String,
    pub message: String,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    pub state: State,
    pub locked: bool,
    pub elapsed_ms: u64,
    pub error: Option<StepError>,
    /// "fixture" | "local" | "cloud" as reported by the API; None until known.
    pub adapter_mode: Option<String>,
    pub transcript: Option<String>,
    pub capture_id: Option<String>,
    pub needs_clarification: bool,
    pub truncated: bool,
    pub mic_ready: bool,
    /// Key release -> transcript first observed (includes polling granularity).
    pub release_to_transcript_ms: Option<u64>,
    /// Transcript observed -> extraction proposal observed.
    pub extraction_ms: Option<u64>,
}

impl Snapshot {
    #[cfg(test)]
    pub fn default_for_test() -> Self {
        Self::new(false)
    }

    fn new(mic_ready: bool) -> Self {
        Self {
            state: State::Idle,
            locked: false,
            elapsed_ms: 0,
            error: None,
            adapter_mode: None,
            transcript: None,
            capture_id: None,
            needs_clarification: false,
            truncated: false,
            mic_ready,
            release_to_transcript_ms: None,
            extraction_ms: None,
        }
    }
}

pub trait Emit: Send + Sync {
    fn emit(&self, snap: &Snapshot);
}

pub enum Msg {
    Down,
    Up,
    Cancel,
    /// Onboarding: open the mic once so the OS asks, and confirm real audio.
    PrepareMic,
    /// Surface a failure that happened outside the controller (e.g. shortcut).
    Fail {
        step: &'static str,
        message: String,
    },
    /// A failure of one capture's upload/poll worker; stale generations are dropped.
    WorkFailed {
        gen: u64,
        step: &'static str,
        message: String,
    },
    Progress(Progress),
    Shutdown,
}

pub struct Progress {
    gen: u64,
    capture_id: String,
    obs: Observed,
    release_to_transcript_ms: Option<u64>,
    extraction_ms: Option<u64>,
}

/// What a `GET /v1/captures/{id}` response means for the user.
#[derive(Clone, Debug, PartialEq)]
pub struct Observed {
    pub state: State,
    pub transcript: Option<String>,
    pub adapter_mode: Option<String>,
    pub clarification: bool,
    pub failure: Option<String>,
}

/// Same derivation as `stateFromCapture` in apps/web: only server state counts.
pub fn observe(d: &Value) -> Observed {
    let src = &d["source"];
    let transcript = d["transcript_text"]
        .as_str()
        .filter(|t| !t.is_empty())
        .map(|t| clip(t, MAX_TRANSCRIPT_CHARS));
    let adapter_mode = src["transcripts"]
        .as_array()
        .and_then(|t| t.last())
        .and_then(|t| t["speech"]["adapter_mode"].as_str())
        .or_else(|| {
            d["jobs"]
                .as_array()
                .and_then(|j| j.last())
                .and_then(|j| j["adapter_mode"].as_str())
        })
        .map(str::to_string);
    let proposals = d["proposals"].as_array().cloned().unwrap_or_default();
    let disp = |p: &Value| p["disposition"].as_str().unwrap_or("pending").to_string();
    let pending = proposals.iter().find(|p| disp(p) == "pending");
    let lifecycle = src["lifecycle"].as_str().unwrap_or("");
    let mut o = Observed {
        state: State::Transcribing,
        transcript: transcript.clone(),
        adapter_mode,
        clarification: false,
        failure: None,
    };
    if lifecycle == "failed" {
        o.state = State::Error;
        o.failure = Some(clip(
            src["error_message"].as_str().unwrap_or("processing failed"),
            300,
        ));
    } else if lifecycle == "tombstoned" {
        o.state = State::Error;
        o.failure = Some("the capture was deleted".into());
    } else if let Some(p) = pending {
        o.state = State::Review;
        o.clarification = p["requires_clarification"].as_bool().unwrap_or(false);
    } else if proposals.iter().any(|p| disp(p) != "pending") {
        o.state = State::Saved;
    } else if transcript.is_some() {
        o.state = State::Extracting;
    }
    o
}

fn rfc3339_utc(secs: u64) -> String {
    let (days, rem) = (secs / 86_400, secs % 86_400);
    let z = days as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

fn now_rfc3339() -> String {
    rfc3339_utc(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_secs()),
    )
}

/// IANA zone from `$TZ` or the `/etc/localtime` symlink; UTC if unknown.
fn timezone() -> String {
    if let Ok(tz) = std::env::var("TZ") {
        if !tz.is_empty() && !tz.starts_with(':') {
            return tz;
        }
    }
    std::fs::read_link("/etc/localtime")
        .ok()
        .and_then(|p| {
            let s = p.to_string_lossy().into_owned();
            s.split_once("zoneinfo/").map(|(_, z)| z.to_string())
        })
        .unwrap_or_else(|| "UTC".into())
}

fn retry<T>(
    cancel: &AtomicBool,
    mut f: impl FnMut() -> Result<T, ApiError>,
) -> Result<T, ApiError> {
    let mut i = 0u32;
    loop {
        match f() {
            Err(e) if e.retryable && i < 3 && !cancel.load(Ordering::SeqCst) => {
                thread::sleep(Duration::from_millis(200 << i));
                i += 1;
            }
            r => return r,
        }
    }
}

struct Work {
    gen: u64,
    wav: Vec<u8>,
    duration: f64,
    client_id: String,
    released: Instant,
}

/// create -> upload -> complete -> poll. Every step reuses the same idempotency
/// anchor, so a retry after an API restart is the same logical capture.
fn process(backend: &dyn Backend, w: Work, cancel: &AtomicBool, tx: &Sender<Msg>) {
    let send = |id: &str, obs: Observed, a: Option<u64>, b: Option<u64>| {
        let _ = tx.send(Msg::Progress(Progress {
            gen: w.gen,
            capture_id: id.to_string(),
            obs,
            release_to_transcript_ms: a,
            extraction_ms: b,
        }));
    };
    let fail = |step: &'static str, e: &str| {
        let _ = tx.send(Msg::WorkFailed {
            gen: w.gen,
            step,
            message: clip(e, 300),
        });
    };
    let created = match retry(cancel, || {
        backend.create(&CreateReq {
            client_capture_id: w.client_id.clone(),
            timezone: timezone(),
            captured_at: now_rfc3339(),
            expected_bytes: w.wav.len(),
            duration_seconds: w.duration,
        })
    }) {
        Ok(c) => c,
        Err(e) => return fail("create", &e.message),
    };
    let id = created.capture_id.clone();
    let abandon = |cancel: &AtomicBool| {
        if cancel.load(Ordering::SeqCst) {
            backend.delete(&id);
            true
        } else {
            false
        }
    };
    if abandon(cancel) {
        return;
    }
    let Some(target) = created.upload.clone() else {
        return fail("create", "the server returned no upload target");
    };
    if let Err(e) = retry(cancel, || backend.upload(&target, &w.wav)) {
        return fail("upload", &e.message);
    }
    if abandon(cancel) {
        return;
    }
    if let Err(e) = retry(cancel, || {
        backend.complete(&id, created.revision, w.wav.len(), &w.client_id)
    }) {
        return fail("complete", &e.message);
    }
    let started = Instant::now();
    let mut t_transcript: Option<(u64, Instant)> = None;
    loop {
        if abandon(cancel) {
            return;
        }
        let detail = match retry(cancel, || backend.get(&id)) {
            Ok(d) => d,
            Err(e) => return fail("poll", &e.message),
        };
        let obs = observe(&detail);
        if t_transcript.is_none() && obs.transcript.is_some() {
            t_transcript = Some((w.released.elapsed().as_millis() as u64, Instant::now()));
        }
        let a = t_transcript.map(|t| t.0);
        let b = match (obs.state, t_transcript) {
            (State::Review | State::Saved, Some((_, at))) => Some(at.elapsed().as_millis() as u64),
            _ => None,
        };
        match obs.state {
            State::Error => {
                let msg = obs.failure.clone().unwrap_or_default();
                return fail("processing", &msg);
            }
            State::Review | State::Saved => return send(&id, obs, a, b),
            _ => send(&id, obs, a, None),
        }
        if started.elapsed() > POLL_DEADLINE {
            return fail(
                "timeout",
                "still processing; the capture is kept on the server",
            );
        }
        thread::sleep(POLL_EVERY);
    }
}

pub struct Controller {
    m: Machine,
    src: Box<dyn AudioSource>,
    rec: Option<Arc<Mutex<Recorder>>>,
    backend: Arc<dyn Backend>,
    emit: Arc<dyn Emit>,
    shared: Arc<Mutex<Snapshot>>,
    tx: Sender<Msg>,
    cancel: Arc<AtomicBool>,
    gen: u64,
    t_down: u64,
    last_emit: u64,
    released: Option<Instant>,
    mic_ready: bool,
    mic_marker: Option<PathBuf>,
}

impl Controller {
    pub fn new(
        src: Box<dyn AudioSource>,
        backend: Arc<dyn Backend>,
        emit: Arc<dyn Emit>,
        shared: Arc<Mutex<Snapshot>>,
        tx: Sender<Msg>,
        mic_marker: Option<PathBuf>,
    ) -> Self {
        let mic_ready = !src.needs_permission() || mic_marker.as_ref().is_some_and(|p| p.is_file());
        *shared.lock().unwrap() = Snapshot::new(mic_ready);
        Self {
            m: Machine::default(),
            src,
            rec: None,
            backend,
            emit,
            shared,
            tx,
            cancel: Arc::new(AtomicBool::new(false)),
            gen: 0,
            t_down: 0,
            last_emit: 0,
            released: None,
            mic_ready,
            mic_marker,
        }
    }

    fn publish(&self, f: impl FnOnce(&mut Snapshot)) {
        let snap = {
            let mut s = self.shared.lock().unwrap();
            f(&mut s);
            s.state = self.m.state();
            s.locked = self.m.locked();
            s.mic_ready = self.mic_ready;
            s.clone()
        };
        self.emit.emit(&snap);
    }

    fn fail(&mut self, step: &str, message: &str) {
        self.src.stop();
        self.rec = None;
        self.cancel.store(true, Ordering::SeqCst);
        self.m.fail();
        let e = StepError {
            step: step.into(),
            message: clip(message, 300),
        };
        self.publish(|s| {
            s.error = Some(e);
            s.elapsed_ms = 0;
        });
    }

    fn recording_ms(&self, now: u64) -> u64 {
        now.saturating_sub(self.t_down)
    }

    fn start_mic(&mut self) -> Result<(), (&'static str, String)> {
        if let Err(why) = self.backend.ready() {
            let step = if why.starts_with("startup_failed") {
                "startup_failed"
            } else {
                "not_ready"
            };
            return Err((step, why));
        }
        if !self.mic_ready {
            return Err((
                "mic_permission",
                "microphone access is not confirmed yet; complete microphone setup first".into(),
            ));
        }
        let fmt = self.src.format().map_err(|e| ("audio_device", e))?;
        let rec = Arc::new(Mutex::new(Recorder::new(fmt, MAX_SAMPLES)));
        self.rec = Some(rec.clone());
        self.src
            .start(Box::new(move |s| {
                rec.lock().unwrap().push(s);
            }))
            .map_err(|e| ("audio_device", e))
    }

    fn finish(&mut self) {
        self.src.stop();
        let dev_err = self.src.failed();
        let Some(rec) = self.rec.take() else {
            return self.fail("audio_device", "no recording buffer");
        };
        let (samples, truncated) = {
            let mut r = rec.lock().unwrap();
            (r.take_samples(), r.truncated())
        };
        if let Some(e) = dev_err {
            return self.fail("audio_device", &e);
        }
        if samples.len() < MIN_SAMPLES {
            self.m.cancel();
            return self.publish(|s| *s = Snapshot::new(s.mic_ready));
        }
        let wav = match encode_wav(&samples) {
            Ok(w) => w,
            Err(e) => return self.fail("audio", &e),
        };
        let secret = crate::stack::new_secret().unwrap_or_else(|_| format!("{:032x}", self.gen));
        let client_id = format!("desk_{}", &secret[..32]);
        let released = self.released.unwrap_or_else(Instant::now);
        let work = Work {
            gen: self.gen,
            wav,
            duration: samples.len() as f64 / TARGET_RATE as f64,
            client_id,
            released,
        };
        self.cancel = Arc::new(AtomicBool::new(false));
        let (backend, cancel, tx) = (self.backend.clone(), self.cancel.clone(), self.tx.clone());
        let spawned = thread::Builder::new()
            .name("metis-capture-upload".into())
            .spawn(move || process(backend.as_ref(), work, &cancel, &tx));
        if let Err(e) = spawned {
            return self.fail("upload", &e.to_string());
        }
        self.publish(|s| {
            *s = Snapshot::new(s.mic_ready);
            s.truncated = truncated;
        });
    }

    pub fn handle(&mut self, msg: Msg, now: u64) {
        match msg {
            Msg::Down => match self.m.key_down(now) {
                Action::StartMic => {
                    self.gen += 1;
                    self.t_down = now;
                    self.released = None;
                    match self.start_mic() {
                        Ok(()) => self.publish(|s| *s = Snapshot::new(s.mic_ready)),
                        Err((step, why)) => self.fail(step, &why),
                    }
                }
                Action::Finish => {
                    self.released = Some(Instant::now());
                    self.finish();
                }
                _ => {}
            },
            Msg::Up => match self.m.key_up(now) {
                Action::Discard => {
                    self.src.stop();
                    self.rec = None;
                    self.publish(|s| *s = Snapshot::new(s.mic_ready));
                }
                Action::Promoted => self.publish(|s| s.elapsed_ms = 0),
                Action::Finish => {
                    self.released = Some(Instant::now());
                    self.finish();
                }
                _ => {}
            },
            Msg::Cancel => {
                self.gen += 1;
                match self.m.cancel() {
                    Action::Discard => {
                        self.src.stop();
                        self.rec = None;
                    }
                    Action::CancelWork => self.cancel.store(true, Ordering::SeqCst),
                    _ => {}
                }
                self.publish(|s| *s = Snapshot::new(s.mic_ready));
            }
            Msg::PrepareMic => self.prepare_mic(),
            Msg::Fail { step, message } => self.fail(step, &message),
            Msg::WorkFailed { gen, step, message } => {
                if gen == self.gen {
                    self.fail(step, &message);
                }
            }
            Msg::Progress(p) => {
                if p.gen != self.gen {
                    return;
                }
                let moved = self.m.progress(p.obs.state);
                if !moved && self.m.state() != p.obs.state {
                    return;
                }
                self.publish(|s| {
                    s.capture_id = Some(p.capture_id.clone());
                    s.transcript = p.obs.transcript.clone();
                    s.adapter_mode = p.obs.adapter_mode.clone();
                    s.needs_clarification = p.obs.clarification;
                    s.release_to_transcript_ms = p.release_to_transcript_ms;
                    s.extraction_ms = p.extraction_ms;
                });
            }
            Msg::Shutdown => {}
        }
    }

    /// Periodic work: Armed -> Recording, elapsed updates, buffer bound, device errors.
    pub fn tick(&mut self, now: u64) {
        if self.m.tick(now) == Action::Promoted {
            self.publish(|s| s.elapsed_ms = 0);
            self.last_emit = now;
        }
        if self.m.state() != State::Recording {
            return;
        }
        if let Some(e) = self.src.failed() {
            return self.fail("audio_device", &e);
        }
        if self
            .rec
            .as_ref()
            .is_some_and(|r| r.lock().unwrap().is_full())
            && self.m.force_finish() == Action::Finish
        {
            self.released = Some(Instant::now());
            return self.finish();
        }
        if now.saturating_sub(self.last_emit) >= EMIT_EVERY_MS {
            self.last_emit = now;
            let ms = self.recording_ms(now);
            self.publish(|s| s.elapsed_ms = ms);
        }
    }

    fn prepare_mic(&mut self) {
        if !matches!(
            self.m.state(),
            State::Idle | State::Error | State::Review | State::Saved
        ) {
            return;
        }
        let result = self.probe_mic();
        match result {
            Ok(()) => {
                self.mic_ready = true;
                if let Some(p) = &self.mic_marker {
                    let _ = std::fs::write(p, b"ok");
                }
                self.m.cancel();
                self.publish(|s| {
                    s.error = None;
                });
            }
            Err(e) => self.fail("mic_permission", &e),
        }
    }

    fn probe_mic(&mut self) -> Result<(), String> {
        let fmt = self.src.format()?;
        let seen = Arc::new(Mutex::new((0usize, 0f32)));
        let s2 = seen.clone();
        self.src.start(Box::new(move |s| {
            let mut g = s2.lock().unwrap();
            g.0 += s.len();
            g.1 = s.iter().fold(g.1, |m, v| m.max(v.abs()));
        }))?;
        // Wait for the OS prompt to be answered and ~0.5 s of audio, up to 30 s.
        let need = fmt.rate as usize * fmt.channels as usize / 2;
        let end = Instant::now() + Duration::from_secs(30);
        while Instant::now() < end && seen.lock().unwrap().0 < need {
            thread::sleep(Duration::from_millis(50));
        }
        self.src.stop();
        let (n, peak) = *seen.lock().unwrap();
        if n < need {
            Err("no audio received from the microphone (permission pending or device busy)".into())
        } else if peak == 0.0 {
            Err("the microphone delivered only silence: access is denied in System Settings > Privacy & Security > Microphone, or the input is muted".into())
        } else {
            Ok(())
        }
    }

    fn deadline(&self) -> Duration {
        match self.m.state() {
            State::Armed | State::Recording => Duration::from_millis(25),
            _ => Duration::from_secs(3600),
        }
    }

    pub fn shutdown(&mut self) {
        self.cancel.store(true, Ordering::SeqCst);
        self.src.stop();
        self.rec = None;
    }
}

/// Cheap, cloneable front for the controller thread.
#[derive(Clone)]
pub struct Handle {
    tx: Sender<Msg>,
    pub snapshot: Arc<Mutex<Snapshot>>,
}

impl Handle {
    pub fn send(&self, m: Msg) {
        let _ = self.tx.send(m);
    }
}

/// Run the controller on its own thread. `make_source` runs on that thread
/// because CoreAudio streams must not cross threads.
pub fn spawn(
    make_source: impl FnOnce() -> Box<dyn AudioSource> + Send + 'static,
    backend: Arc<dyn Backend>,
    emit: Arc<dyn Emit>,
    mic_marker: Option<PathBuf>,
) -> (Handle, JoinHandle<()>) {
    let (tx, rx) = channel();
    let shared = Arc::new(Mutex::new(Snapshot::new(false)));
    let handle = Handle {
        tx: tx.clone(),
        snapshot: shared.clone(),
    };
    let join = thread::Builder::new()
        .name("metis-capture".into())
        .spawn(move || {
            let mut c = Controller::new(make_source(), backend, emit, shared, tx, mic_marker);
            let t0 = Instant::now();
            let ms = || t0.elapsed().as_millis() as u64;
            loop {
                match rx.recv_timeout(c.deadline()) {
                    Ok(Msg::Shutdown) | Err(RecvTimeoutError::Disconnected) => break,
                    Ok(m) => c.handle(m, ms()),
                    Err(RecvTimeoutError::Timeout) => {}
                }
                c.tick(ms());
            }
            c.shutdown();
        })
        .expect("spawn capture thread");
    (handle, join)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::audio::{Format, Sink};
    use crate::backend::UploadTarget;
    use serde_json::json;
    use std::sync::atomic::AtomicUsize;

    #[test]
    fn rfc3339_known_instants() {
        assert_eq!(rfc3339_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(rfc3339_utc(1_709_164_800 + 3661), "2024-02-29T01:01:01Z");
        assert_eq!(rfc3339_utc(4_102_444_800), "2100-01-01T00:00:00Z");
    }

    #[test]
    fn observe_maps_server_state() {
        let o = observe(&json!({"source":{"lifecycle":"processing"},"proposals":[]}));
        assert_eq!(o.state, State::Transcribing);
        let o = observe(
            &json!({"source":{"lifecycle":"processing"},"transcript_text":"hi","proposals":[]}),
        );
        assert_eq!(o.state, State::Extracting);
        let o = observe(
            &json!({"source":{"lifecycle":"ready","transcripts":[{"speech":{"adapter_mode":"fixture"}}]},
            "transcript_text":"hi","proposals":[{"requires_clarification":true}]}),
        );
        assert_eq!((o.state, o.clarification), (State::Review, true));
        assert_eq!(o.adapter_mode.as_deref(), Some("fixture"));
        let o = observe(
            &json!({"source":{"lifecycle":"ready"},"proposals":[{"disposition":"accepted"}]}),
        );
        assert_eq!(o.state, State::Saved);
        let o = observe(&json!({"source":{"lifecycle":"failed","error_message":"boom"}}));
        assert_eq!(
            (o.state, o.failure.as_deref()),
            (State::Error, Some("boom"))
        );
    }

    struct Silent;
    impl AudioSource for Silent {
        fn format(&mut self) -> Result<Format, String> {
            Ok(Format {
                rate: 16_000,
                channels: 1,
            })
        }
        fn start(&mut self, mut sink: Sink) -> Result<(), String> {
            sink(&vec![0.2; 8_000]); // 0.5 s
            Ok(())
        }
        fn stop(&mut self) {}
    }
    impl Emit for Mutex<Vec<Snapshot>> {
        fn emit(&self, s: &Snapshot) {
            self.lock().unwrap().push(s.clone());
        }
    }

    #[derive(Default)]
    struct Fake {
        ready: Mutex<Option<String>>,
        creates: AtomicUsize,
        completes: AtomicUsize,
        uploads: AtomicUsize,
        fail_first_create: AtomicBool,
        deleted: AtomicUsize,
        last_upload_bytes: AtomicUsize,
        /// 0 = finished review, 1 = still transcribing, 2 = transcript but no proposal yet.
        get_mode: AtomicUsize,
    }
    impl Backend for Fake {
        fn ready(&self) -> Result<(), String> {
            match self.ready.lock().unwrap().clone() {
                Some(e) => Err(e),
                None => Ok(()),
            }
        }
        fn create(&self, _: &CreateReq) -> Result<crate::backend::Created, ApiError> {
            if self.fail_first_create.swap(false, Ordering::SeqCst) {
                return Err(ApiError {
                    retryable: true,
                    message: "connection failed".into(),
                });
            }
            self.creates.fetch_add(1, Ordering::SeqCst);
            Ok(crate::backend::Created {
                capture_id: "cap1".into(),
                revision: 1,
                upload: Some(UploadTarget {
                    url: "/u".into(),
                    method: "PUT".into(),
                    headers: Default::default(),
                }),
            })
        }
        fn upload(&self, _: &UploadTarget, wav: &[u8]) -> Result<(), ApiError> {
            assert!(wav.starts_with(b"RIFF"));
            self.uploads.fetch_add(1, Ordering::SeqCst);
            self.last_upload_bytes.store(wav.len(), Ordering::SeqCst);
            Ok(())
        }
        fn complete(&self, _: &str, _: u64, _: usize, _: &str) -> Result<(), ApiError> {
            self.completes.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }
        fn get(&self, _: &str) -> Result<Value, ApiError> {
            match self.get_mode.load(Ordering::SeqCst) {
                1 => return Ok(json!({"source":{"lifecycle":"processing"},"proposals":[]})),
                2 => {
                    return Ok(
                        json!({"source":{"lifecycle":"processing"},"transcript_text":"hi","proposals":[]}),
                    )
                }
                _ => {}
            }
            Ok(
                json!({"source":{"lifecycle":"ready","transcripts":[{"speech":{"adapter_mode":"fixture"}}]},
                "transcript_text":"call mom","proposals":[{"requires_clarification":false}]}),
            )
        }
        fn delete(&self, _: &str) {
            self.deleted.fetch_add(1, Ordering::SeqCst);
        }
    }

    struct Rig {
        c: Controller,
        rx: std::sync::mpsc::Receiver<Msg>,
        backend: Arc<Fake>,
        events: Arc<Mutex<Vec<Snapshot>>>,
    }
    fn rig() -> Rig {
        let backend = Arc::new(Fake::default());
        let events: Arc<Mutex<Vec<Snapshot>>> = Arc::default();
        let (tx, rx) = channel();
        let c = Controller::new(
            Box::new(Silent),
            backend.clone(),
            events.clone(),
            Arc::new(Mutex::new(Snapshot::new(true))),
            tx,
            None,
        );
        Rig {
            c,
            rx,
            backend,
            events,
        }
    }
    impl Rig {
        fn state(&self) -> State {
            self.c.m.state()
        }
        /// Deliver worker messages until the capture reaches `want`.
        fn pump_until(&mut self, want: State) {
            let end = Instant::now() + Duration::from_secs(10);
            while self.state() != want {
                assert!(Instant::now() < end, "stuck in {:?}", self.state());
                if let Ok(m) = self.rx.recv_timeout(Duration::from_millis(50)) {
                    self.c.handle(m, 0);
                }
            }
        }
    }

    #[test]
    fn hold_release_uploads_exactly_once() {
        let mut r = rig();
        r.c.handle(Msg::Down, 0);
        r.c.tick(200);
        assert_eq!(r.state(), State::Recording);
        r.c.handle(Msg::Up, 600);
        r.c.handle(Msg::Up, 601); // duplicate release
        r.pump_until(State::Review);
        assert_eq!(r.backend.creates.load(Ordering::SeqCst), 1);
        assert_eq!(r.backend.uploads.load(Ordering::SeqCst), 1);
        assert_eq!(r.backend.completes.load(Ordering::SeqCst), 1);
        let s = r.c.shared.lock().unwrap().clone();
        assert_eq!(s.transcript.as_deref(), Some("call mom"));
        assert_eq!(s.adapter_mode.as_deref(), Some("fixture"));
        assert!(s.release_to_transcript_ms.is_some() && s.extraction_ms.is_some());
    }

    #[test]
    fn armed_audio_is_kept_and_nothing_exists_before_key_down() {
        let mut r = rig();
        assert!(
            r.c.rec.is_none(),
            "no buffer, no open mic before the keypress"
        );
        r.c.handle(Msg::Down, 0);
        // Silent delivers 0.5 s while still Armed; all of it must reach the upload.
        r.c.tick(150);
        r.c.handle(Msg::Up, 400);
        r.pump_until(State::Review);
        assert_eq!(
            r.backend.last_upload_bytes.load(Ordering::SeqCst),
            44 + 8_000 * 2
        );
        assert!(r.c.rec.is_none(), "buffer released after finish");
    }

    #[test]
    fn short_tap_creates_no_capture() {
        let mut r = rig();
        r.c.handle(Msg::Down, 0);
        r.c.handle(Msg::Up, 90);
        std::thread::sleep(Duration::from_millis(100));
        assert!(r.rx.try_recv().is_err());
        assert_eq!(r.backend.creates.load(Ordering::SeqCst), 0);
        assert_eq!(r.state(), State::Idle);
    }

    #[test]
    fn double_tap_lock_then_stop_makes_one_capture() {
        let mut r = rig();
        r.c.handle(Msg::Down, 0);
        r.c.handle(Msg::Up, 60);
        r.c.handle(Msg::Down, 200);
        r.c.handle(Msg::Up, 260);
        assert!(r.c.m.locked());
        r.c.tick(3000);
        r.c.handle(Msg::Down, 4000);
        r.c.handle(Msg::Up, 4050);
        r.pump_until(State::Review);
        assert_eq!(r.backend.creates.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn not_ready_refuses_without_opening_the_mic() {
        let mut r = rig();
        *r.backend.ready.lock().unwrap() =
            Some("not_ready: speech is Unhealthy: model missing".into());
        r.c.handle(Msg::Down, 0);
        assert_eq!(r.state(), State::Error);
        assert!(r.c.rec.is_none());
        let s = r.c.shared.lock().unwrap().clone();
        let e = s.error.unwrap();
        assert_eq!(e.step, "not_ready");
        assert!(e.message.contains("model missing"));
        r.c.handle(Msg::Up, 10);
        assert_eq!(r.backend.creates.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn unconfirmed_mic_refuses_to_record() {
        let mut r = rig();
        r.c.mic_ready = false;
        r.c.handle(Msg::Down, 0);
        assert_eq!(r.state(), State::Error);
        assert_eq!(
            r.c.shared.lock().unwrap().error.as_ref().unwrap().step,
            "mic_permission"
        );
        assert!(r.c.rec.is_none());
    }

    #[test]
    fn cancel_during_work_deletes_nothing_it_did_not_create_and_recovers() {
        let mut r = rig();
        r.c.handle(Msg::Down, 0);
        r.c.tick(200);
        r.c.handle(Msg::Up, 500);
        r.c.handle(Msg::Cancel, 501);
        assert_eq!(r.state(), State::Idle);
        // stale worker output must not resurrect the capture
        std::thread::sleep(Duration::from_millis(400));
        while let Ok(m) = r.rx.try_recv() {
            r.c.handle(m, 0);
        }
        assert_eq!(r.state(), State::Idle);
    }

    /// Cancel must stop in-flight work in both processing states: the machine goes
    /// Idle, the worker deletes the capture it created, and nothing it sends later
    /// can bring the state back.
    fn cancel_in_flight(mode: usize, want: State) {
        let mut r = rig();
        r.backend.get_mode.store(mode, Ordering::SeqCst);
        r.c.handle(Msg::Down, 0);
        r.c.tick(200);
        r.c.handle(Msg::Up, 500);
        r.pump_until(want);
        let events_before = r.events.lock().unwrap().len();
        r.c.handle(Msg::Cancel, 600);
        assert_eq!(r.state(), State::Idle);
        let snap = r.c.shared.lock().unwrap().clone();
        assert_eq!(
            (snap.state, snap.capture_id, snap.transcript),
            (State::Idle, None, None)
        );
        // The worker notices the flag, deletes the capture and stops polling.
        let end = Instant::now() + Duration::from_secs(5);
        while r.backend.deleted.load(Ordering::SeqCst) == 0 {
            assert!(Instant::now() < end, "worker never abandoned the capture");
            while let Ok(m) = r.rx.try_recv() {
                r.c.handle(m, 0);
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        std::thread::sleep(Duration::from_millis(400));
        while let Ok(m) = r.rx.try_recv() {
            r.c.handle(m, 0);
        }
        assert_eq!(r.state(), State::Idle, "stale progress must be dropped");
        assert_eq!(r.backend.deleted.load(Ordering::SeqCst), 1);
        let ev = r.events.lock().unwrap();
        assert_eq!(ev.last().unwrap().state, State::Idle);
        assert!(ev.len() > events_before, "cancel was published");
    }

    #[test]
    fn cancel_is_honoured_while_transcribing() {
        cancel_in_flight(1, State::Transcribing);
    }

    #[test]
    fn cancel_is_honoured_while_extracting() {
        cancel_in_flight(2, State::Extracting);
    }

    #[test]
    fn retry_after_transient_failure_is_one_capture() {
        let mut r = rig();
        r.backend.fail_first_create.store(true, Ordering::SeqCst);
        r.c.handle(Msg::Down, 0);
        r.c.tick(200);
        r.c.handle(Msg::Up, 500);
        r.pump_until(State::Review);
        assert_eq!(r.backend.creates.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn error_recovers_on_next_press_and_cancel() {
        let mut r = rig();
        r.c.handle(
            Msg::Fail {
                step: "shortcut",
                message: "taken".into(),
            },
            0,
        );
        assert_eq!(r.state(), State::Error);
        r.c.handle(Msg::Cancel, 1);
        assert_eq!(r.state(), State::Idle);
        r.c.handle(
            Msg::Fail {
                step: "shortcut",
                message: "taken".into(),
            },
            2,
        );
        r.c.handle(Msg::Down, 3);
        assert_eq!(r.state(), State::Armed);
    }

    #[test]
    fn events_never_contain_secrets_shape() {
        let mut r = rig();
        r.c.handle(Msg::Down, 0);
        r.c.tick(200);
        r.c.handle(Msg::Up, 500);
        r.pump_until(State::Review);
        let json = serde_json::to_string(&*r.events.lock().unwrap()).unwrap();
        for bad in ["bearer", "Bearer", "http://", "127.0.0.1"] {
            assert!(!json.contains(bad), "{bad} leaked: {json}");
        }
    }
}
