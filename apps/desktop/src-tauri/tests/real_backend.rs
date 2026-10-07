//! Launch -> authenticated health -> shutdown against the REAL packaged
//! `metis-backend` (built by `pnpm sidecar:prepare`). No microphone, no model
//! weights: speech must honestly report an unusable model, not "ready".
use metis_desktop_lib::stack::{http_get, Config, Kind, Phase, Stack};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::time::{Duration, Instant};

fn backend() -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries");
    let triple_suffix = "metis-backend-".to_string();
    std::fs::read_dir(&dir)
        .unwrap_or_else(|_| panic!("{} missing: run `pnpm sidecar:prepare`", dir.display()))
        .flatten()
        .map(|e| e.path())
        .find(|p| {
            let n = p.file_name().unwrap().to_string_lossy().to_string();
            n.starts_with("metis-backend-") && n.starts_with(&triple_suffix)
        })
        .expect("packaged backend missing: run `pnpm sidecar:prepare`")
}

fn open(port: u16) -> bool {
    TcpStream::connect_timeout(
        &SocketAddr::from(([127, 0, 0, 1], port)),
        Duration::from_millis(300),
    )
    .is_ok()
}

#[test]
fn packaged_backend_launch_authenticated_health_shutdown() {
    let dir = std::env::temp_dir().join(format!("metis-real-{}", std::process::id()));
    let mut stack = Stack::start(Config::production(backend(), &dir)).unwrap();

    // Cold PyInstaller onefile start-up is several seconds.
    let end = Instant::now() + Duration::from_secs(120);
    let st = loop {
        let st = stack.status();
        let s = |k: Kind| {
            st.services
                .iter()
                .find(|x| x.name == k.name())
                .unwrap()
                .phase
        };
        if s(Kind::Api) == Phase::Ready
            && s(Kind::Worker) == Phase::Ready
            && s(Kind::Speech) == Phase::Unhealthy
        {
            break st;
        }
        assert!(
            Instant::now() < end,
            "stack never came up: {st:?}\n{:#?}",
            stack.events()
        );
        std::thread::sleep(Duration::from_millis(200));
    };
    // Model weights are absent: never "ready", never recordable.
    let speech = st.services.iter().find(|s| s.name == "speech").unwrap();
    assert_eq!(speech.phase, Phase::Unhealthy, "{speech:?}");
    assert!(!st.recording_allowed);

    let conn = stack.connection().unwrap();
    let port: u16 = conn
        .api_base_url
        .rsplit(':')
        .next()
        .unwrap()
        .parse()
        .unwrap();
    let t = Duration::from_secs(5);
    assert_eq!(
        http_get(port, "/v1/health", Some(&conn.bearer), t)
            .unwrap()
            .0,
        200
    );
    assert_eq!(http_get(port, "/v1/limits", None, t).unwrap().0, 200);
    // A protected route: no/wrong bearer rejected, correct bearer accepted.
    assert_eq!(http_get(port, "/v1/tasks", None, t).unwrap().0, 401);
    assert_eq!(
        http_get(port, "/v1/tasks", Some("x".repeat(64).as_str()), t)
            .unwrap()
            .0,
        401
    );
    let (code, body) = http_get(port, "/v1/tasks", Some(&conn.bearer), t).unwrap();
    assert_eq!(code, 200, "{body}");

    // Persistent identity + data were created under the app-data dir.
    assert!(dir.join("identity/install_id").is_file());
    assert!(dir.join("metis.sqlite").is_file());
    // The secret never reached the logs or the diagnostics.
    let mut hay = format!("{:?}{:?}", stack.events(), serde_json::to_string(&st));
    for l in std::fs::read_dir(dir.join("logs")).unwrap().flatten() {
        hay += &std::fs::read_to_string(l.path()).unwrap_or_default();
    }
    assert!(
        !hay.contains(&conn.bearer),
        "bearer leaked into logs/diagnostics"
    );

    let ports: Vec<u16> = st.services.iter().filter_map(|s| s.port).collect();
    assert!(ports.len() == 2 && ports.iter().all(|p| open(*p)));
    stack.shutdown();
    assert_eq!(stack.status().state, "stopped");
    assert!(
        ports.iter().all(|p| !open(*p)),
        "services still listening after shutdown"
    );
    let _ = std::fs::remove_dir_all(&dir);
}
