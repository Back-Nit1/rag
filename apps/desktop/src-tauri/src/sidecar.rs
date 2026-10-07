//! Sidecar supervision: spawn a packaged executable, parse `METIS_READY port=N`
//! from stdout, restart on unexpected exit with bounded backoff, and stop
//! cleanly (close stdin, grace period, then kill). Sidecars exit on stdin EOF,
//! so they also die if this process dies.
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Event {
    Ready {
        port: u16,
    },
    /// `expected` is true only when we asked it to stop.
    Exited {
        code: Option<i32>,
        expected: bool,
    },
    Restarting {
        attempt: u32,
        delay: Duration,
    },
    GaveUp {
        attempts: u32,
    },
    SpawnFailed(String),
}

#[derive(Debug, Clone)]
pub struct Policy {
    pub base_delay: Duration,
    pub max_delay: Duration,
    pub max_restarts: u32,
    /// A run at least this long resets the restart counter.
    pub stable_after: Duration,
    pub stop_grace: Duration,
}

impl Default for Policy {
    fn default() -> Self {
        Self {
            base_delay: Duration::from_millis(250),
            max_delay: Duration::from_secs(8),
            max_restarts: 5,
            stable_after: Duration::from_secs(30),
            stop_grace: Duration::from_secs(2),
        }
    }
}

impl Policy {
    pub fn delay_for(&self, attempt: u32) -> Duration {
        let shift = attempt.saturating_sub(1).min(16);
        self.base_delay
            .saturating_mul(1u32 << shift)
            .min(self.max_delay)
    }
}

#[derive(Debug)]
pub enum SidecarError {
    Missing(PathBuf),
}

impl std::fmt::Display for SidecarError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SidecarError::Missing(p) => write!(
                f,
                "sidecar executable not found at {} (run `pnpm --filter @metis/desktop sidecar:prepare`, or reinstall the app)",
                p.display()
            ),
        }
    }
}

impl std::error::Error for SidecarError {}

pub type Callback = Arc<dyn Fn(Event) + Send + Sync>;

pub struct Handle {
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Handle {
    /// Stop the sidecar (no restart) and wait for it to exit.
    pub fn shutdown(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

impl Drop for Handle {
    fn drop(&mut self) {
        self.shutdown();
    }
}

/// Resolve a bundled sidecar: Tauri places `externalBin` next to the app
/// executable with the target-triple suffix stripped.
pub fn bundled_path(name: &str) -> std::io::Result<PathBuf> {
    let exe = std::env::current_exe()?;
    let dir = exe.parent().unwrap_or_else(|| Path::new("."));
    Ok(dir.join(format!("{name}{}", std::env::consts::EXE_SUFFIX)))
}

pub fn spawn(
    path: PathBuf,
    args: Vec<String>,
    policy: Policy,
    on_event: Callback,
) -> Result<Handle, SidecarError> {
    if !path.is_file() {
        return Err(SidecarError::Missing(path));
    }
    let stop = Arc::new(AtomicBool::new(false));
    let flag = stop.clone();
    let thread = thread::spawn(move || supervise(path, args, policy, on_event, flag));
    Ok(Handle {
        stop,
        thread: Some(thread),
    })
}

fn supervise(
    path: PathBuf,
    args: Vec<String>,
    policy: Policy,
    emit: Callback,
    stop: Arc<AtomicBool>,
) {
    let mut restarts = 0u32;
    while !stop.load(Ordering::SeqCst) {
        let started = Instant::now();
        let mut child = match Command::new(&path)
            .args(&args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
        {
            Ok(c) => c,
            Err(e) => {
                emit(Event::SpawnFailed(e.to_string()));
                return;
            }
        };
        let stdin = child.stdin.take();
        if let Some(out) = child.stdout.take() {
            let emit = emit.clone();
            thread::spawn(move || {
                for line in BufReader::new(out).lines().map_while(Result::ok) {
                    if let Some(port) = parse_ready(&line) {
                        emit(Event::Ready { port });
                    }
                }
            });
        }
        let expected = wait(&mut child, stdin, &stop, &policy);
        let code = child.wait().ok().and_then(|s| s.code());
        emit(Event::Exited { code, expected });
        if expected {
            return;
        }
        if started.elapsed() >= policy.stable_after {
            restarts = 0;
        }
        restarts += 1;
        if restarts > policy.max_restarts {
            emit(Event::GaveUp {
                attempts: restarts - 1,
            });
            return;
        }
        let delay = policy.delay_for(restarts);
        emit(Event::Restarting {
            attempt: restarts,
            delay,
        });
        let until = Instant::now() + delay;
        while Instant::now() < until && !stop.load(Ordering::SeqCst) {
            thread::sleep(Duration::from_millis(10));
        }
    }
}

/// Returns true if the exit was requested by us.
fn wait(
    child: &mut Child,
    stdin: Option<std::process::ChildStdin>,
    stop: &AtomicBool,
    policy: &Policy,
) -> bool {
    loop {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return stop.load(Ordering::SeqCst);
        }
        if stop.load(Ordering::SeqCst) {
            drop(stdin); // EOF asks the sidecar to exit
            let deadline = Instant::now() + policy.stop_grace;
            while Instant::now() < deadline {
                if matches!(child.try_wait(), Ok(Some(_))) {
                    return true;
                }
                thread::sleep(Duration::from_millis(10));
            }
            let _ = child.kill();
            return true;
        }
        thread::sleep(Duration::from_millis(20));
    }
}

pub fn parse_ready(line: &str) -> Option<u16> {
    line.trim().strip_prefix("METIS_READY port=")?.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ready_line() {
        assert_eq!(parse_ready("METIS_READY port=5123\n"), Some(5123));
        assert_eq!(parse_ready("METIS_READY port=x"), None);
        assert_eq!(parse_ready("hello"), None);
    }

    #[test]
    fn backoff_is_bounded() {
        let p = Policy::default();
        assert_eq!(p.delay_for(1), Duration::from_millis(250));
        assert_eq!(p.delay_for(2), Duration::from_millis(500));
        assert_eq!(p.delay_for(99), p.max_delay);
    }

    #[test]
    fn missing_artifact_is_clear_error() {
        let e = spawn(
            PathBuf::from("/nonexistent/sidecar"),
            vec![],
            Policy::default(),
            Arc::new(|_| {}),
        )
        .err()
        .expect("must fail");
        assert!(e.to_string().contains("not found"));
    }
}
