//! TEST FIXTURE ONLY (never bundled): a fake `metis-backend` speaking the same
//! readiness/health protocols as the real services, with failure injection.
//!
//!   fake <api|worker|speech> [--crash-after-ms N --code C] [--garbage] [--silent]
//!        [--model loading|failed] [--health-fail-after-ms N]
//!
//! Env it honours: METIS_SPEECH_SECRET (speech bearer), METIS_DESKTOP_AUTH_SECRET
//! (api /v1/env bearer), METIS_SQLITE_PATH (pid/env breadcrumbs next to it).
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::{env, process, thread, time::Duration, time::Instant};

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    let svc = args.first().cloned().unwrap_or_default();
    let flag = |name: &str| args.iter().any(|a| a == name);
    let val = |name: &str| {
        args.iter()
            .position(|a| a == name)
            .and_then(|i| args.get(i + 1))
            .cloned()
    };
    let num = |name: &str| val(name).and_then(|v| v.parse::<u64>().ok());
    let stamp = |suffix: &str, body: String| {
        if let Ok(base) = env::var("METIS_SQLITE_PATH") {
            let _ = std::fs::write(format!("{base}.{svc}.{suffix}"), body);
        }
    };
    stamp("pid", process::id().to_string());
    if svc == "speech" {
        // Lets tests prove the secret never reaches status or events.
        stamp(
            "secret",
            env::var("METIS_SPEECH_SECRET").unwrap_or_default(),
        );
    }

    thread::spawn(|| {
        let mut sink = Vec::new();
        let _ = std::io::stdin().read_to_end(&mut sink);
        process::exit(0);
    });
    if let Some(ms) = num("--crash-after-ms") {
        let code = num("--code").unwrap_or(1) as i32;
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(ms));
            process::exit(code);
        });
    }
    let say = |line: &str| {
        println!("{line}");
        std::io::stdout().flush().ok();
    };

    if svc == "worker" {
        stamp("env", env::var("METIS_SPEECH_ENDPOINT").unwrap_or_default());
        if flag("--garbage") {
            say("hello");
        } else if !flag("--silent") {
            say(&format!(
                r#"{{"event":"started","service":"worker","protocol":1,"pid":{}}}"#,
                process::id()
            ));
        }
        loop {
            thread::park();
        }
    }

    let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind");
    let port = listener.local_addr().unwrap().port();
    if flag("--garbage") {
        say("hello");
    } else if !flag("--silent") {
        match svc.as_str() {
            "api" => say(&format!("METIS_API_PORT={port}")),
            _ => say(&format!(
                r#"{{"event":"listening","protocol":1,"host":"127.0.0.1","port":{port},"pid":{}}}"#,
                process::id()
            )),
        }
    }
    let started = Instant::now();
    let fail_after = num("--health-fail-after-ms");
    let model = val("--model").unwrap_or_else(|| "ready".into());
    let broken = Arc::new(AtomicBool::new(false));
    for conn in listener.incoming().flatten() {
        let broken = broken.clone();
        let (svc, model) = (svc.clone(), model.clone());
        let down = fail_after.is_some_and(|ms| started.elapsed() > Duration::from_millis(ms));
        if down {
            broken.store(true, Ordering::SeqCst);
        }
        thread::spawn(move || serve(conn, &svc, &model, broken.load(Ordering::SeqCst)));
    }
}

fn serve(mut s: TcpStream, svc: &str, model: &str, broken: bool) {
    let mut buf = [0u8; 4096];
    let n = s.read(&mut buf).unwrap_or(0);
    let req = String::from_utf8_lossy(&buf[..n]).to_string();
    let path = req.split_whitespace().nth(1).unwrap_or("");
    let bearer = req
        .lines()
        .find_map(|l| l.strip_prefix("Authorization: Bearer "))
        .unwrap_or("")
        .trim()
        .to_string();
    let reply = |s: &mut TcpStream, code: u16, body: String| {
        let _ = write!(
            s,
            "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
    };
    if broken {
        return reply(&mut s, 500, "{}".into());
    }
    match (svc, path) {
        ("speech", "/v1/ready") => {
            if bearer != env::var("METIS_SPEECH_SECRET").unwrap_or_default() {
                return reply(&mut s, 401, "{}".into());
            }
            match model {
                "loading" => reply(&mut s, 503, r#"{"state":"loading"}"#.into()),
                "failed" => reply(&mut s, 503, r#"{"state":"failed"}"#.into()),
                _ => reply(&mut s, 200, r#"{"state":"ready"}"#.into()),
            }
        }
        ("api", "/v1/health") => reply(&mut s, 200, r#"{"status":"ok"}"#.into()),
        ("api", "/v1/env") => {
            if bearer != env::var("METIS_DESKTOP_AUTH_SECRET").unwrap_or_default() {
                return reply(&mut s, 401, "{}".into());
            }
            let e = |k: &str| env::var(k).unwrap_or_default();
            let body = format!(
                r#"{{"speech_endpoint":"{}","distinct":{},"mode":"{}","cors":"{}","leak":"{}","sqlite":"{}"}}"#,
                e("METIS_SPEECH_ENDPOINT"),
                e("METIS_SPEECH_SECRET") != e("METIS_DESKTOP_AUTH_SECRET")
                    && e("METIS_SPEECH_SECRET").len() >= 32,
                e("METIS_MODE"),
                e("METIS_CORS_ORIGINS"),
                e("METIS_LEAK_PROBE"),
                e("METIS_SQLITE_PATH"),
            );
            reply(&mut s, 200, body)
        }
        _ => reply(&mut s, 404, "{}".into()),
    }
}
