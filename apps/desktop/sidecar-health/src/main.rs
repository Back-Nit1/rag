//! Prototype sidecar. Binds 127.0.0.1:0, prints `METIS_READY port=<n>`, answers
//! any HTTP request with 200, exits when stdin closes (parent gone/shutdown).
//! Test flags: `--exit-after-ms N [--code C]` simulates exit/crash.
use std::io::{Read, Write};
use std::net::TcpListener;
use std::{env, process, thread, time::Duration};

fn main() {
    let args: Vec<String> = env::args().collect();
    let flag = |name: &str| {
        args.iter()
            .position(|a| a == name)
            .and_then(|i| args.get(i + 1))
            .and_then(|v| v.parse::<u64>().ok())
    };
    let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind loopback");
    let port = listener.local_addr().expect("addr").port();
    println!("METIS_READY port={port}");
    std::io::stdout().flush().ok();

    thread::spawn(move || {
        for mut s in listener.incoming().flatten() {
            let mut buf = [0u8; 1024];
            let _ = s.read(&mut buf);
            let body = r#"{"status":"ok"}"#;
            let _ = write!(
                s,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
        }
    });
    thread::spawn(|| {
        let mut sink = Vec::new();
        let _ = std::io::stdin().read_to_end(&mut sink);
        process::exit(0);
    });

    if let Some(ms) = flag("--exit-after-ms") {
        thread::sleep(Duration::from_millis(ms));
        process::exit(flag("--code").unwrap_or(0) as i32);
    }
    loop {
        thread::park();
    }
}
