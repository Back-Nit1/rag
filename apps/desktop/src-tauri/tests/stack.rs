//! Deterministic lifecycle tests: the real supervisor against a fake backend
//! (`metis-health-sidecar`, built by `pnpm sidecar:prepare`) that speaks the
//! same readiness/health protocols and injects failures.
use metis_desktop_lib::stack::{Config, DataPaths, Kind, Phase, Policy, Stack, StackStatus};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, Instant};

static N: AtomicU32 = AtomicU32::new(0);

struct Env {
    dir: PathBuf,
    stack: Stack,
}

impl Drop for Env {
    fn drop(&mut self) {
        self.stack.shutdown();
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn fixture() -> PathBuf {
    let target = std::env::var_os("CARGO_TARGET_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../target"));
    let p = target.join("debug/metis-health-sidecar");
    assert!(
        p.is_file(),
        "{} missing: run `pnpm sidecar:prepare`",
        p.display()
    );
    p
}

fn fast() -> Policy {
    Policy {
        base_delay: Duration::from_millis(20),
        max_delay: Duration::from_millis(80),
        jitter: 0.0,
        max_restarts: 2,
        stable_after: Duration::from_secs(60),
        startup_deadline: Duration::from_secs(5),
        warm_deadline: Duration::from_secs(30),
        health_interval: Duration::from_millis(100),
        health_timeout: Duration::from_millis(500),
        health_failures: 2,
        stop_grace: Duration::from_secs(2),
    }
}

fn start(speech: &[&str], api: &[&str], worker: &[&str], policy: Policy) -> Env {
    std::env::set_var("METIS_LEAK_PROBE", "host-value");
    let dir = std::env::temp_dir().join(format!(
        "metis-stack-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::SeqCst)
    ));
    let a = |svc: &str, extra: &[&str]| {
        let mut v = vec![svc.to_string()];
        v.extend(extra.iter().map(|s| s.to_string()));
        v
    };
    let cfg = Config {
        backend: fixture(),
        data: DataPaths::under(&dir),
        policy,
        args: [a("speech", speech), a("api", api), a("worker", worker)],
        speech_engine: None,
    };
    Env {
        stack: Stack::start(cfg).unwrap(),
        dir,
    }
}

fn wait(env: &Env, what: &str, f: impl Fn(&StackStatus) -> bool) -> StackStatus {
    let end = Instant::now() + Duration::from_secs(15);
    while Instant::now() < end {
        let s = env.stack.status();
        if f(&s) {
            return s;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    panic!(
        "timeout waiting for {what}: {:?}\nevents: {:#?}",
        env.stack.status(),
        env.stack.events()
    );
}

fn svc(s: &StackStatus, k: Kind) -> &metis_desktop_lib::stack::ServiceStatus {
    s.services.iter().find(|x| x.name == k.name()).unwrap()
}

fn stamp(env: &Env, svc: &str, suffix: &str) -> String {
    std::fs::read_to_string(env.dir.join(format!("metis.sqlite.{svc}.{suffix}"))).unwrap()
}

fn alive(pid: i32) -> bool {
    unsafe { libc::kill(pid, 0) == 0 }
}

fn ready(env: &Env) -> StackStatus {
    wait(env, "ready", |s| s.state == "ready")
}

fn get(port: u16, path: &str, bearer: Option<&str>) -> (u16, String) {
    metis_desktop_lib::stack::http_get(port, path, bearer, Duration::from_secs(2)).unwrap()
}

#[test]
fn starts_in_dependency_order_with_correct_env_and_shuts_down_cleanly() {
    let mut env = start(&[], &[], &[], fast());
    let st = ready(&env);
    assert!(st.recording_allowed);
    let ev = env.stack.events();
    let pos = |needle: &str| ev.iter().position(|e| e == needle).unwrap();
    assert!(
        pos("speech spawned") < pos("api spawned") && pos("api spawned") < pos("worker spawned")
    );
    assert!(
        pos("speech ready") < pos("worker spawned"),
        "worker waits for API readiness: {ev:?}"
    );

    let conn = env.stack.connection().unwrap();
    let port = svc(&st, Kind::Api).port.unwrap();
    assert_eq!(conn.api_base_url, format!("http://127.0.0.1:{port}"));
    assert_eq!(get(port, "/v1/env", None).0, 401);
    assert_eq!(get(port, "/v1/env", Some("wrong")).0, 401);
    let (code, body) = get(port, "/v1/env", Some(&conn.bearer));
    assert_eq!(code, 200);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    let speech_port = svc(&st, Kind::Speech).port.unwrap();
    let endpoint = format!("http://127.0.0.1:{speech_port}");
    assert_eq!(v["speech_endpoint"], endpoint.as_str());
    assert_eq!(stamp(&env, "worker", "env"), endpoint);
    assert_eq!(v["distinct"], true, "speech and api secrets must differ");
    assert_eq!(v["mode"], "local");
    assert_eq!(v["cors"], "tauri://localhost,http://tauri.localhost");
    assert_eq!(v["leak"], "", "host METIS_* must not reach children");
    assert!(Path::new(v["sqlite"].as_str().unwrap()).is_absolute());

    // Secrets never appear in status or events.
    let speech_secret = stamp(&env, "speech", "secret");
    let dump = format!(
        "{}{:?}",
        serde_json::to_string(&st).unwrap(),
        env.stack.events()
    );
    assert!(!dump.contains(&conn.bearer) && !dump.contains(&speech_secret));
    assert!(speech_secret.len() >= 32 && speech_secret != conn.bearer);

    let pids: Vec<i32> = ["speech", "api", "worker"]
        .iter()
        .map(|s| stamp(&env, s, "pid").parse().unwrap())
        .collect();
    assert!(pids.iter().all(|p| alive(*p)));
    env.stack.shutdown();
    let st = env.stack.status();
    assert_eq!(st.state, "stopped");
    assert!(!st.recording_allowed);
    std::thread::sleep(Duration::from_millis(100));
    assert!(
        pids.iter().all(|p| !alive(*p)),
        "children must be gone after shutdown"
    );
    assert_eq!(env.stack.connection().err(), Some("unavailable"));
}

#[test]
fn connection_is_not_ready_until_api_is_up() {
    let env = start(
        &[],
        &["--silent"],
        &[],
        Policy {
            startup_deadline: Duration::from_secs(30),
            ..fast()
        },
    );
    assert_eq!(env.stack.connection().err(), Some("not_ready"));
}

#[test]
fn missing_backend_is_a_clear_startup_error() {
    let cfg = Config::production(
        PathBuf::from("/nonexistent/metis-backend"),
        &std::env::temp_dir(),
    );
    let e = Stack::start(cfg).err().expect("must fail");
    assert!(e.contains("not found"));
}

#[test]
fn malformed_readiness_exhausts_restarts_and_opens_the_circuit() {
    let env = start(&[], &["--garbage"], &[], fast());
    let st = wait(&env, "api failed", |s| {
        svc(s, Kind::Api).phase == Phase::Failed
    });
    assert_eq!(svc(&st, Kind::Api).restarts, 3);
    assert!(svc(&st, Kind::Api)
        .detail
        .as_ref()
        .unwrap()
        .contains("malformed readiness"));
    assert_eq!(st.state, "unhealthy");
    assert!(!st.recording_allowed);
    assert_eq!(
        svc(&st, Kind::Worker).phase,
        Phase::Pending,
        "worker never starts without API"
    );
    assert_eq!(env.stack.connection().err(), Some("unavailable"));
    assert!(env
        .stack
        .events()
        .iter()
        .any(|e| e.contains("circuit open")));
}

#[test]
fn startup_deadline_stops_a_child_that_never_reports() {
    let policy = Policy {
        startup_deadline: Duration::from_millis(300),
        ..fast()
    };
    let env = start(&["--silent"], &[], &[], policy);
    let st = wait(&env, "speech failed", |s| {
        svc(s, Kind::Speech).phase == Phase::Failed
    });
    assert!(svc(&st, Kind::Speech)
        .detail
        .as_ref()
        .unwrap()
        .contains("startup deadline"));
    assert_eq!(
        svc(&st, Kind::Api).phase,
        Phase::Pending,
        "API waits for speech"
    );
}

#[test]
fn crash_loop_uses_bounded_backoff_then_gives_up() {
    let env = start(
        &[],
        &["--crash-after-ms", "100", "--code", "3"],
        &[],
        fast(),
    );
    let st = wait(&env, "api failed", |s| {
        svc(s, Kind::Api).phase == Phase::Failed
    });
    assert_eq!(svc(&st, Kind::Api).restarts, 3);
    let ev = env.stack.events();
    let restarts: Vec<_> = ev
        .iter()
        .filter(|e| e.starts_with("api restart #"))
        .collect();
    assert_eq!(restarts.len(), 2, "{ev:?}");
    assert!(
        restarts[0].contains("in 20ms") && restarts[1].contains("in 40ms"),
        "{restarts:?}"
    );
    assert!(restarts[0].contains("code Some(3)"));
}

#[test]
fn killed_worker_is_restarted_and_killed_speech_restarts_dependents_in_order() {
    let env = start(&[], &[], &[], fast());
    ready(&env);
    let pid: i32 = stamp(&env, "worker", "pid").parse().unwrap();
    unsafe { libc::kill(pid, libc::SIGKILL) };
    let st = wait(&env, "worker restarted", |s| {
        svc(s, Kind::Worker).restarts == 1 && s.state == "ready"
    });
    assert!(st.recording_allowed);

    let before = env.stack.events().len();
    let pid: i32 = stamp(&env, "speech", "pid").parse().unwrap();
    unsafe { libc::kill(pid, libc::SIGKILL) };
    let st = wait(&env, "stack recovered", |s| {
        s.state == "ready" && svc(s, Kind::Speech).restarts == 1
    });
    let ev = env.stack.events()[before..].to_vec();
    let pos = |needle: &str| {
        ev.iter()
            .position(|e| e == needle)
            .unwrap_or_else(|| panic!("{needle} in {ev:?}"))
    };
    assert!(pos("api stopped: dependency restarting") < pos("speech spawned"));
    assert!(
        pos("speech spawned") < pos("api spawned") && pos("api spawned") < pos("worker spawned")
    );
    // The API was re-pointed at the new speech port.
    let new_port = svc(&st, Kind::Speech).port.unwrap();
    let conn = env.stack.connection().unwrap();
    let (_, body) = get(
        svc(&st, Kind::Api).port.unwrap(),
        "/v1/env",
        Some(&conn.bearer),
    );
    assert!(body.contains(&format!("127.0.0.1:{new_port}")));
}

#[test]
fn failed_speech_model_blocks_recording_but_not_the_api() {
    let env = start(&["--model", "failed"], &[], &[], fast());
    let st = wait(&env, "api ready and speech unhealthy", |s| {
        svc(s, Kind::Api).phase == Phase::Ready && svc(s, Kind::Speech).phase == Phase::Unhealthy
    });
    assert_eq!(st.state, "degraded");
    assert!(!st.recording_allowed);
    assert!(svc(&st, Kind::Speech)
        .detail
        .as_ref()
        .unwrap()
        .contains("model"));
    assert!(env.stack.connection().is_ok());
}

#[test]
fn loading_speech_model_is_warming_not_ready() {
    let env = start(&["--model", "loading"], &[], &[], fast());
    let st = wait(&env, "warming", |s| {
        svc(s, Kind::Speech).phase == Phase::Warming
    });
    assert!(!st.recording_allowed);
    assert_ne!(st.state, "ready");
}

#[test]
fn hung_api_fails_health_checks_and_is_restarted() {
    let env = start(&[], &["--health-fail-after-ms", "200"], &[], fast());
    wait(&env, "health restart", |_| {
        env.stack
            .events()
            .iter()
            .any(|e| e.contains("health check failed"))
    });
}
