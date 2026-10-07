//! Lifecycle tests against the real packaged health sidecar
//! (built by `pnpm sidecar:prepare` into src-tauri/binaries/).
use metis_desktop_lib::sidecar::{spawn, Event, Handle, Policy};
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

fn artifact() -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries");
    std::fs::read_dir(&dir)
        .unwrap_or_else(|_| panic!("{} missing: run `pnpm sidecar:prepare`", dir.display()))
        .flatten()
        .map(|e| e.path())
        .find(|p| {
            p.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("metis-health-sidecar-")
        })
        .expect("sidecar artifact missing: run `pnpm sidecar:prepare`")
}

fn fast() -> Policy {
    Policy {
        base_delay: Duration::from_millis(20),
        max_delay: Duration::from_millis(80),
        max_restarts: 2,
        stable_after: Duration::from_secs(60),
        stop_grace: Duration::from_secs(2),
    }
}

fn start(args: &[&str], policy: Policy) -> (Handle, Arc<Mutex<Vec<Event>>>) {
    let log = Arc::new(Mutex::new(vec![]));
    let l = log.clone();
    let h = spawn(
        artifact(),
        args.iter().map(|s| s.to_string()).collect(),
        policy,
        Arc::new(move |e| l.lock().unwrap().push(e)),
    )
    .unwrap();
    (h, log)
}

fn wait_for(log: &Arc<Mutex<Vec<Event>>>, f: impl Fn(&[Event]) -> bool) -> Vec<Event> {
    let end = Instant::now() + Duration::from_secs(10);
    while Instant::now() < end {
        let v = log.lock().unwrap().clone();
        if f(&v) {
            return v;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    panic!("timeout; events: {:?}", log.lock().unwrap());
}

#[test]
fn ready_port_is_loopback_and_answers_then_parent_shutdown_is_clean() {
    let (mut h, log) = start(&[], fast());
    let ev = wait_for(&log, |v| v.iter().any(|e| matches!(e, Event::Ready { .. })));
    let Event::Ready { port } = ev[0] else {
        panic!("{ev:?}")
    };
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.write_all(b"GET / HTTP/1.1\r\n\r\n").unwrap();
    let mut body = String::new();
    s.read_to_string(&mut body).unwrap();
    assert!(body.contains("200 OK") && body.contains("\"ok\""));
    h.shutdown();
    let v = log.lock().unwrap().clone();
    assert!(
        matches!(v.last(), Some(Event::Exited { expected: true, .. })),
        "{v:?}"
    );
    assert!(!v.iter().any(|e| matches!(e, Event::Restarting { .. })));
}

#[test]
fn clean_exit_without_request_is_reported_unexpected_and_restarted() {
    let (_h, log) = start(&["--exit-after-ms", "50", "--code", "0"], fast());
    let v = wait_for(&log, |v| {
        v.iter().any(|e| matches!(e, Event::Restarting { .. }))
    });
    assert!(v.contains(&Event::Exited {
        code: Some(0),
        expected: false
    }));
}

#[test]
fn crash_restarts_with_bounded_backoff_then_gives_up() {
    let (_h, log) = start(&["--exit-after-ms", "20", "--code", "3"], fast());
    let v = wait_for(&log, |v| {
        v.iter().any(|e| matches!(e, Event::GaveUp { .. }))
    });
    assert!(v.contains(&Event::Exited {
        code: Some(3),
        expected: false
    }));
    let delays: Vec<_> = v
        .iter()
        .filter_map(|e| {
            if let Event::Restarting { delay, .. } = e {
                Some(*delay)
            } else {
                None
            }
        })
        .collect();
    assert_eq!(
        delays,
        vec![Duration::from_millis(20), Duration::from_millis(40)]
    );
    assert!(v.contains(&Event::GaveUp { attempts: 2 }));
}

#[test]
fn sidecar_exits_when_parent_pipe_closes() {
    use std::process::{Command, Stdio};
    let mut c = Command::new(artifact())
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .spawn()
        .unwrap();
    drop(c.stdin.take());
    let end = Instant::now() + Duration::from_secs(5);
    while Instant::now() < end {
        if let Some(st) = c.try_wait().unwrap() {
            assert!(st.success());
            return;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let _ = c.kill();
    let _ = c.wait();
    panic!("sidecar outlived its parent pipe");
}
