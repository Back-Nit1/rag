//! Supervisor for the local Metis stack: speech -> API -> worker.
//!
//! One orchestrator thread owns all three children. It parses a structured,
//! versioned readiness record from each child's first stdout line (never
//! scrapes log lines), polls real health over loopback HTTP, restarts crashed
//! children with capped exponential backoff plus jitter, opens a circuit when
//! the restart budget is spent, and stops everything gracefully on shutdown.
//!
//! Secrets: generated here from the OS CSPRNG, handed to children through their
//! environment only, and to the webview through [`Stack::connection`]. They are
//! redacted from every status/event string and never written to logs.
use serde::Serialize;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{channel, Receiver, TryRecvError};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

const PROTOCOL: u64 = 1;
const MAX_LINE: u64 = 8192;
const MAX_EVENTS: usize = 200;
const LOG_CAP_BYTES: u64 = 5 * 1024 * 1024;
pub const CORS_ORIGINS: &str = "tauri://localhost,http://tauri.localhost";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Speech,
    Api,
    Worker,
}
const ORDER: [Kind; 3] = [Kind::Speech, Kind::Api, Kind::Worker];

impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Kind::Speech => "speech",
            Kind::Api => "api",
            Kind::Worker => "worker",
        }
    }
    fn idx(self) -> usize {
        self as usize
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    /// Waiting for a dependency.
    Pending,
    /// Spawned; waiting for the readiness record and first healthy poll.
    Starting,
    /// Speech only: listening, model still loading.
    Warming,
    Ready,
    /// Process alive but reporting failure (e.g. speech model missing).
    Unhealthy,
    Backoff,
    /// Restart budget exhausted or unrecoverable: circuit open.
    Failed,
    Stopped,
}

#[derive(Clone, Debug, Serialize)]
pub struct ServiceStatus {
    pub name: &'static str,
    pub phase: Phase,
    pub port: Option<u16>,
    pub restarts: u32,
    pub detail: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StackStatus {
    /// starting | ready | degraded | unhealthy | stopped
    pub state: &'static str,
    /// True only while every service, including the speech model, is ready.
    pub recording_allowed: bool,
    pub services: Vec<ServiceStatus>,
}

#[derive(Clone, Debug)]
pub struct Policy {
    pub base_delay: Duration,
    pub max_delay: Duration,
    /// 0..=1: the delay is scaled by `1 - jitter * u`, u uniform in [0,1).
    pub jitter: f64,
    pub max_restarts: u32,
    /// A run that stayed ready this long resets the restart counter.
    pub stable_after: Duration,
    /// Spawn -> readiness record + first healthy poll.
    pub startup_deadline: Duration,
    /// Speech model load, after it is listening.
    pub warm_deadline: Duration,
    pub health_interval: Duration,
    pub health_timeout: Duration,
    pub health_failures: u32,
    pub stop_grace: Duration,
}

impl Default for Policy {
    fn default() -> Self {
        Self {
            base_delay: Duration::from_millis(500),
            max_delay: Duration::from_secs(15),
            jitter: 0.3,
            max_restarts: 5,
            stable_after: Duration::from_secs(60),
            // A cold onefile executable unpacks and imports for several seconds.
            startup_deadline: Duration::from_secs(60),
            warm_deadline: Duration::from_secs(180),
            health_interval: Duration::from_secs(5),
            health_timeout: Duration::from_secs(2),
            health_failures: 3,
            stop_grace: Duration::from_secs(5),
        }
    }
}

impl Policy {
    pub fn delay_for(&self, attempt: u32, unit: f64) -> Duration {
        let shift = attempt.saturating_sub(1).min(16);
        let capped = self
            .base_delay
            .saturating_mul(1u32 << shift)
            .min(self.max_delay);
        capped.mul_f64(1.0 - self.jitter.clamp(0.0, 1.0) * unit.clamp(0.0, 1.0))
    }
}

/// Absolute locations under the OS app-data directory.
#[derive(Clone, Debug)]
pub struct DataPaths {
    pub sqlite: PathBuf,
    pub objects: PathBuf,
    pub identity: PathBuf,
    pub logs: PathBuf,
    pub models: PathBuf,
}

impl DataPaths {
    pub fn under(dir: &Path) -> Self {
        Self {
            sqlite: dir.join("metis.sqlite"),
            objects: dir.join("objects"),
            identity: dir.join("identity").join("install_id"),
            logs: dir.join("logs"),
            models: dir.join("models"),
        }
    }
    fn prepare(&self) -> std::io::Result<()> {
        for d in [
            self.objects.as_path(),
            self.identity.parent().unwrap(),
            self.logs.as_path(),
            self.models.as_path(),
        ] {
            create_private_dir(d)?;
        }
        Ok(())
    }
}

fn create_private_dir(d: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(d)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(d, std::fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

#[derive(Clone, Debug)]
pub struct Config {
    pub backend: PathBuf,
    pub data: DataPaths,
    pub policy: Policy,
    /// Arguments after the backend path, per service (speech, api, worker).
    pub args: [Vec<String>; 3],
}

impl Config {
    pub fn production(backend: PathBuf, data_dir: &Path) -> Self {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect();
        Self {
            backend,
            data: DataPaths::under(data_dir),
            policy: Policy::default(),
            args: [
                s(&["speech"]),
                s(&["api", "--host", "127.0.0.1", "--port", "0"]),
                s(&["worker"]),
            ],
        }
    }
}

/// What the webview receives. Never logged or serialised into status.
pub struct Connection {
    pub api_base_url: String,
    pub bearer: String,
}

struct Secrets {
    api: String,
    speech: String,
}

#[derive(Default)]
struct Snapshot {
    services: Vec<ServiceStatus>,
    events: Vec<String>,
}

struct Shared {
    snap: Mutex<Snapshot>,
    shutdown: AtomicBool,
    secrets: Secrets,
}

impl Shared {
    fn redact(&self, s: &str) -> String {
        s.replace(&self.secrets.api, "[redacted]")
            .replace(&self.secrets.speech, "[redacted]")
    }
}

pub struct Stack {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

/// 32 random bytes from the OS CSPRNG, hex encoded (64 chars).
pub fn new_secret() -> Result<String, String> {
    let mut b = [0u8; 32];
    getrandom::fill(&mut b).map_err(|e| format!("OS random source unavailable: {e}"))?;
    Ok(b.iter().map(|x| format!("{x:02x}")).collect())
}

fn unit_random() -> f64 {
    let mut b = [0u8; 8];
    if getrandom::fill(&mut b).is_err() {
        return 0.5;
    }
    (u64::from_le_bytes(b) >> 11) as f64 / (1u64 << 53) as f64
}

impl Stack {
    pub fn start(cfg: Config) -> Result<Stack, String> {
        if !cfg.backend.is_file() {
            return Err(format!(
                "backend executable not found at {} (run `pnpm --filter @metis/desktop sidecar:prepare`, or reinstall the app)",
                cfg.backend.display()
            ));
        }
        cfg.data
            .prepare()
            .map_err(|e| format!("cannot prepare app data directories: {e}"))?;
        let secrets = Secrets {
            api: new_secret()?,
            speech: new_secret()?,
        };
        let shared = Arc::new(Shared {
            snap: Mutex::new(Snapshot::default()),
            shutdown: AtomicBool::new(false),
            secrets,
        });
        let mut orch = Orchestrator::new(cfg, shared.clone());
        orch.publish();
        let thread = thread::Builder::new()
            .name("metis-stack".into())
            .spawn(move || orch.run())
            .map_err(|e| e.to_string())?;
        Ok(Stack {
            shared,
            thread: Some(thread),
        })
    }

    pub fn status(&self) -> StackStatus {
        let snap = self.shared.snap.lock().unwrap();
        aggregate(&snap.services, self.shared.shutdown.load(Ordering::SeqCst))
    }

    /// Sanitised lifecycle log (spawns, exits, restarts), newest last.
    pub fn events(&self) -> Vec<String> {
        self.shared.snap.lock().unwrap().events.clone()
    }

    /// `Err("not_ready")` while starting; `Err("unavailable")` when the API
    /// cannot come up (circuit open) or the app is shutting down.
    pub fn connection(&self) -> Result<Connection, &'static str> {
        let snap = self.shared.snap.lock().unwrap();
        let api = snap.services.get(Kind::Api.idx());
        match api {
            Some(s) if s.phase == Phase::Ready => Ok(Connection {
                api_base_url: format!("http://127.0.0.1:{}", s.port.unwrap_or(0)),
                bearer: self.shared.secrets.api.clone(),
            }),
            Some(s) if s.phase == Phase::Failed => Err("unavailable"),
            _ if self.shared.shutdown.load(Ordering::SeqCst) => Err("unavailable"),
            _ => Err("not_ready"),
        }
    }

    pub fn shutdown(&mut self) {
        self.shared.shutdown.store(true, Ordering::SeqCst);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

impl Drop for Stack {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn aggregate(services: &[ServiceStatus], shutdown: bool) -> StackStatus {
    let all = |p: Phase| services.iter().all(|s| s.phase == p);
    let any = |p: Phase| services.iter().any(|s| s.phase == p);
    let state = if shutdown && all(Phase::Stopped) {
        "stopped"
    } else if any(Phase::Failed) {
        "unhealthy"
    } else if all(Phase::Ready) {
        "ready"
    } else if any(Phase::Unhealthy) || any(Phase::Backoff) {
        "degraded"
    } else {
        "starting"
    };
    StackStatus {
        state,
        recording_allowed: !services.is_empty() && all(Phase::Ready) && !shutdown,
        services: services.to_vec(),
    }
}

struct Svc {
    kind: Kind,
    phase: Phase,
    detail: Option<String>,
    port: Option<u16>,
    restarts: u32,
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    lines: Option<Receiver<String>>,
    spawned: Instant,
    ready_since: Option<Instant>,
    record_seen: bool,
    retry_at: Instant,
    next_poll: Instant,
    poll_fails: u32,
    warm_since: Option<Instant>,
}

struct Orchestrator {
    cfg: Config,
    shared: Arc<Shared>,
    svc: Vec<Svc>,
}

enum Probe {
    Ready,
    Loading,
    ModelFailed,
    Down(String),
}

impl Orchestrator {
    fn new(cfg: Config, shared: Arc<Shared>) -> Self {
        let now = Instant::now();
        let svc = ORDER
            .iter()
            .map(|&kind| Svc {
                kind,
                phase: Phase::Pending,
                detail: None,
                port: None,
                restarts: 0,
                child: None,
                stdin: None,
                lines: None,
                spawned: now,
                ready_since: None,
                record_seen: false,
                retry_at: now,
                next_poll: now,
                poll_fails: 0,
                warm_since: None,
            })
            .collect();
        Self { cfg, shared, svc }
    }

    fn event(&self, msg: String) {
        let msg = self.shared.redact(&msg);
        let mut snap = self.shared.snap.lock().unwrap();
        if snap.events.len() >= MAX_EVENTS {
            snap.events.remove(0);
        }
        snap.events.push(msg);
    }

    fn publish(&self) {
        let services = self
            .svc
            .iter()
            .map(|s| ServiceStatus {
                name: s.kind.name(),
                phase: s.phase,
                port: s.port,
                restarts: s.restarts,
                detail: s.detail.as_deref().map(|d| self.shared.redact(d)),
            })
            .collect();
        self.shared.snap.lock().unwrap().services = services;
    }

    fn run(&mut self) {
        while !self.shared.shutdown.load(Ordering::SeqCst) {
            for i in 0..self.svc.len() {
                self.step(i);
            }
            self.publish();
            thread::sleep(Duration::from_millis(20));
        }
        for i in (0..self.svc.len()).rev() {
            self.stop_child(i);
            self.svc[i].phase = Phase::Stopped;
            self.svc[i].port = None;
            self.svc[i].detail = None;
            self.event(format!("{} stopped", self.svc[i].kind.name()));
        }
        self.publish();
    }

    /// Process alive and its port known.
    fn up(&self, k: Kind) -> bool {
        let s = &self.svc[k.idx()];
        s.child.is_some() && s.port.is_some()
    }

    fn can_spawn(&self, k: Kind) -> Result<(), String> {
        let need = |dep: Kind| {
            if self.up(dep) {
                Ok(())
            } else {
                Err(format!("waiting for {}", dep.name()))
            }
        };
        match k {
            Kind::Speech => Ok(()),
            Kind::Api => need(Kind::Speech),
            Kind::Worker => {
                need(Kind::Speech)?;
                if self.svc[Kind::Api.idx()].phase == Phase::Ready {
                    Ok(())
                } else {
                    Err("waiting for api".into())
                }
            }
        }
    }

    fn deps_alive(&self, k: Kind) -> bool {
        match k {
            Kind::Speech => true,
            Kind::Api => self.up(Kind::Speech),
            Kind::Worker => self.up(Kind::Speech) && self.up(Kind::Api),
        }
    }

    fn step(&mut self, i: usize) {
        let kind = self.svc[i].kind;
        if self.svc[i].child.is_none() {
            match self.svc[i].phase {
                Phase::Failed | Phase::Stopped => {}
                Phase::Backoff if Instant::now() < self.svc[i].retry_at => {}
                _ => match self.can_spawn(kind) {
                    Ok(()) => self.spawn(i),
                    Err(why) => {
                        self.svc[i].phase = if self.svc[i].phase == Phase::Backoff {
                            Phase::Backoff
                        } else {
                            Phase::Pending
                        };
                        self.svc[i].detail = Some(why);
                    }
                },
            }
            return;
        }
        if let Ok(Some(status)) = self.svc[i].child.as_mut().unwrap().try_wait() {
            self.reap(i);
            self.fail_run(i, format!("exited unexpectedly (code {:?})", status.code()));
            return;
        }
        if !self.deps_alive(kind) {
            self.stop_child(i);
            self.svc[i].phase = Phase::Pending;
            self.svc[i].detail = Some("dependency restarting".into());
            self.event(format!("{} stopped: dependency restarting", kind.name()));
            return;
        }
        self.progress(i);
    }

    fn progress(&mut self, i: usize) {
        let kind = self.svc[i].kind;
        let now = Instant::now();
        if !self.svc[i].record_seen {
            match self.svc[i].lines.as_ref().unwrap().try_recv() {
                Ok(line) => match parse_record(kind, &line) {
                    Ok(port) => {
                        self.svc[i].record_seen = true;
                        self.svc[i].port = port;
                        self.event(format!("{} readiness record accepted", kind.name()));
                        if kind == Kind::Worker {
                            self.mark_ready(i);
                            return;
                        }
                    }
                    Err(why) => return self.fail_start(i, why),
                },
                Err(TryRecvError::Empty | TryRecvError::Disconnected) => {}
            }
        }
        let s = &self.svc[i];
        if s.phase == Phase::Starting
            && now.duration_since(s.spawned) > self.cfg.policy.startup_deadline
        {
            return self.fail_start(i, "startup deadline exceeded".into());
        }
        if !self.svc[i].record_seen || kind == Kind::Worker || now < self.svc[i].next_poll {
            return;
        }
        self.svc[i].next_poll = now
            + if self.svc[i].phase == Phase::Starting {
                Duration::from_millis(100)
            } else {
                self.cfg.policy.health_interval
            };
        let probe = self.probe(kind, self.svc[i].port.unwrap_or(0));
        match probe {
            Probe::Ready => {
                self.svc[i].poll_fails = 0;
                self.svc[i].warm_since = None;
                if self.svc[i].phase != Phase::Ready {
                    self.mark_ready(i);
                }
            }
            Probe::Loading => {
                self.svc[i].poll_fails = 0;
                let warm = *self.svc[i].warm_since.get_or_insert(now);
                if now.duration_since(warm) > self.cfg.policy.warm_deadline {
                    self.svc[i].phase = Phase::Unhealthy;
                    self.svc[i].detail = Some("speech model load timed out".into());
                } else if self.svc[i].phase != Phase::Warming {
                    self.svc[i].phase = Phase::Warming;
                    self.svc[i].detail = Some("speech model is loading".into());
                }
            }
            Probe::ModelFailed => {
                self.svc[i].poll_fails = 0;
                if self.svc[i].phase != Phase::Unhealthy {
                    self.event("speech model failed to load".into());
                }
                self.svc[i].phase = Phase::Unhealthy;
                self.svc[i].detail =
                    Some("speech model failed to load (model files missing or unreadable)".into());
            }
            Probe::Down(why) => {
                self.svc[i].poll_fails += 1;
                if self.svc[i].phase != Phase::Starting
                    && self.svc[i].poll_fails >= self.cfg.policy.health_failures
                {
                    self.kill_child(i);
                    self.fail_run(i, format!("health check failed: {why}"));
                }
            }
        }
    }

    fn mark_ready(&mut self, i: usize) {
        let s = &mut self.svc[i];
        s.phase = Phase::Ready;
        s.detail = None;
        s.ready_since = Some(Instant::now());
        let name = s.kind.name();
        self.event(format!("{name} ready"));
    }

    fn probe(&self, kind: Kind, port: u16) -> Probe {
        let p = &self.cfg.policy;
        let r = match kind {
            Kind::Api => http_get(port, "/v1/health", None, p.health_timeout),
            Kind::Speech => http_get(
                port,
                "/v1/ready",
                Some(&self.shared.secrets.speech),
                p.health_timeout,
            ),
            Kind::Worker => return Probe::Ready,
        };
        match (kind, r) {
            (_, Err(e)) => Probe::Down(e),
            (Kind::Api, Ok((200, _))) => Probe::Ready,
            (Kind::Speech, Ok((200, _))) => Probe::Ready,
            (Kind::Speech, Ok((503, body))) => {
                match serde_json::from_str::<serde_json::Value>(&body)
                    .ok()
                    .and_then(|v| v.get("state").and_then(|s| s.as_str().map(String::from)))
                    .as_deref()
                {
                    Some("loading") => Probe::Loading,
                    Some("failed") => Probe::ModelFailed,
                    _ => Probe::Down("unrecognised 503 body".into()),
                }
            }
            (_, Ok((code, _))) => Probe::Down(format!("HTTP {code}")),
        }
    }

    fn env_for(&self, kind: Kind) -> Vec<(String, String)> {
        let d = &self.cfg.data;
        let p = |x: &Path| x.to_string_lossy().into_owned();
        let sec = &self.shared.secrets;
        let mut e = vec![
            ("METIS_MODE".into(), "local".into()),
            ("METIS_SQLITE_PATH".into(), p(&d.sqlite)),
            ("METIS_LOCAL_OBJECTS_DIR".into(), p(&d.objects)),
        ];
        let speech_remote = |e: &mut Vec<(String, String)>| {
            let port = self.svc[Kind::Speech.idx()].port.unwrap_or(0);
            e.push(("METIS_SPEECH_ENGINE".into(), "remote".into()));
            e.push((
                "METIS_SPEECH_ENDPOINT".into(),
                format!("http://127.0.0.1:{port}"),
            ));
            e.push(("METIS_SPEECH_SECRET".into(), sec.speech.clone()));
        };
        match kind {
            Kind::Speech => {
                e.push(("METIS_SPEECH_SECRET".into(), sec.speech.clone()));
                e.push(("METIS_SPEECH_MODEL_DIR".into(), p(&d.models)));
            }
            Kind::Api => {
                e.push(("METIS_DESKTOP_AUTH_SECRET".into(), sec.api.clone()));
                e.push(("METIS_DESKTOP_IDENTITY_PATH".into(), p(&d.identity)));
                e.push(("METIS_CORS_ORIGINS".into(), CORS_ORIGINS.into()));
                speech_remote(&mut e);
            }
            Kind::Worker => speech_remote(&mut e),
        }
        e
    }

    fn spawn(&mut self, i: usize) {
        let kind = self.svc[i].kind;
        let log = open_log(&self.cfg.data.logs, kind);
        let mut cmd = Command::new(&self.cfg.backend);
        cmd.args(&self.cfg.args[i])
            .env_clear()
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(log.map(Stdio::from).unwrap_or_else(|_| Stdio::null()));
        // Minimal inherited environment: no cloud credentials or METIS_* from the host.
        for k in [
            "PATH",
            "HOME",
            "TMPDIR",
            "TEMP",
            "TMP",
            "LANG",
            "SYSTEMROOT",
        ] {
            if let Some(v) = std::env::var_os(k) {
                cmd.env(k, v);
            }
        }
        cmd.envs(self.env_for(kind));
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        }
        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                // Not transient: restarting a missing/unexecutable file cannot help.
                self.svc[i].phase = Phase::Failed;
                self.svc[i].detail = Some(format!("cannot start {}: {e}", kind.name()));
                self.event(format!("{} spawn failed", kind.name()));
                return;
            }
        };
        let (tx, rx) = channel();
        if let Some(out) = child.stdout.take() {
            thread::spawn(move || {
                let mut r = BufReader::new(out);
                let mut first = String::new();
                if r.by_ref().take(MAX_LINE).read_line(&mut first).unwrap_or(0) > 0 {
                    let _ = tx.send(first);
                }
                let _ = std::io::copy(&mut r, &mut std::io::sink()); // drain, never log
            });
        }
        let s = &mut self.svc[i];
        s.stdin = child.stdin.take();
        s.child = Some(child);
        s.lines = Some(rx);
        s.spawned = Instant::now();
        s.phase = Phase::Starting;
        s.detail = None;
        s.port = None;
        s.record_seen = false;
        s.ready_since = None;
        s.poll_fails = 0;
        s.warm_since = None;
        s.next_poll = Instant::now();
        self.event(format!("{} spawned", kind.name()));
    }

    fn reap(&mut self, i: usize) {
        let s = &mut self.svc[i];
        s.child = None;
        s.stdin = None;
        s.lines = None;
        s.port = None;
    }

    /// Startup never completed: stop the child and count a failure.
    fn fail_start(&mut self, i: usize, why: String) {
        self.kill_child(i);
        self.fail_run(i, why);
    }

    fn fail_run(&mut self, i: usize, why: String) {
        let policy = self.cfg.policy.clone();
        let name = self.svc[i].kind.name();
        let s = &mut self.svc[i];
        if s.ready_since
            .is_some_and(|t| t.elapsed() >= policy.stable_after)
        {
            s.restarts = 0;
        }
        s.ready_since = None;
        s.restarts += 1;
        if s.restarts > policy.max_restarts {
            s.phase = Phase::Failed;
            s.detail = Some(format!("restart budget exhausted: {why}"));
            let msg = format!(
                "{name} circuit open after {} restarts: {why}",
                s.restarts - 1
            );
            self.event(msg);
        } else {
            let delay = policy.delay_for(s.restarts, unit_random());
            s.phase = Phase::Backoff;
            s.retry_at = Instant::now() + delay;
            s.detail = Some(why.clone());
            let msg = format!(
                "{name} restart #{} in {}ms: {why}",
                s.restarts,
                delay.as_millis()
            );
            self.event(msg);
        }
    }

    fn kill_child(&mut self, i: usize) {
        self.stop_child_with(i, Duration::ZERO);
    }

    fn stop_child(&mut self, i: usize) {
        self.stop_child_with(i, self.cfg.policy.stop_grace);
    }

    /// EOF on stdin, then SIGTERM, then SIGKILL; each step bounded by `grace`.
    fn stop_child_with(&mut self, i: usize, grace: Duration) {
        let s = &mut self.svc[i];
        let Some(mut child) = s.child.take() else {
            return;
        };
        drop(s.stdin.take());
        s.lines = None;
        s.port = None;
        let wait = |c: &mut Child, d: Duration| {
            let end = Instant::now() + d;
            loop {
                if matches!(c.try_wait(), Ok(Some(_))) {
                    return true;
                }
                if Instant::now() >= end {
                    return false;
                }
                thread::sleep(Duration::from_millis(10));
            }
        };
        if !wait(&mut child, grace) {
            terminate(&child);
            if !wait(&mut child, grace) {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

#[cfg(unix)]
fn terminate(c: &Child) {
    unsafe {
        libc::kill(c.id() as libc::pid_t, libc::SIGTERM);
    }
}
#[cfg(not(unix))]
fn terminate(_: &Child) {}

/// Strict parse of the first stdout line. Returns the announced port, if any.
pub fn parse_record(kind: Kind, line: &str) -> Result<Option<u16>, String> {
    let line = line.trim();
    let port = |n: u64| -> Result<u16, String> {
        u16::try_from(n)
            .ok()
            .filter(|p| *p != 0)
            .ok_or_else(|| "malformed readiness record: bad port".to_string())
    };
    match kind {
        Kind::Api => {
            let n = line
                .strip_prefix("METIS_API_PORT=")
                .and_then(|v| v.parse::<u64>().ok())
                .ok_or("malformed readiness record")?;
            port(n).map(Some)
        }
        Kind::Speech | Kind::Worker => {
            let v: serde_json::Value =
                serde_json::from_str(line).map_err(|_| "malformed readiness record")?;
            if v["protocol"].as_u64() != Some(PROTOCOL) {
                return Err("unsupported readiness protocol".into());
            }
            if kind == Kind::Speech {
                if v["event"] != "listening" || v["host"] != "127.0.0.1" {
                    return Err("malformed readiness record".into());
                }
                port(v["port"].as_u64().ok_or("malformed readiness record")?).map(Some)
            } else if v["event"] == "started" && v["service"] == "worker" {
                Ok(None)
            } else {
                Err("malformed readiness record".into())
            }
        }
    }
}

/// Minimal loopback HTTP/1.1 GET. Returns (status, body).
pub fn http_get(
    port: u16,
    path: &str,
    bearer: Option<&str>,
    timeout: Duration,
) -> Result<(u16, String), String> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut s = TcpStream::connect_timeout(&addr, timeout).map_err(|e| e.to_string())?;
    s.set_read_timeout(Some(timeout)).ok();
    s.set_write_timeout(Some(timeout)).ok();
    let auth = bearer
        .map(|b| format!("Authorization: Bearer {b}\r\n"))
        .unwrap_or_default();
    write!(
        s,
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n{auth}Connection: close\r\n\r\n"
    )
    .map_err(|e| e.to_string())?;
    let mut raw = Vec::new();
    s.take(1 << 20)
        .read_to_end(&mut raw)
        .map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&raw);
    let (head, body) = text.split_once("\r\n\r\n").ok_or("incomplete response")?;
    let code = head
        .split_whitespace()
        .nth(1)
        .and_then(|c| c.parse().ok())
        .ok_or("bad status line")?;
    Ok((code, body.to_string()))
}

/// Append-mode log file per service; truncated when over the cap at start.
fn open_log(dir: &Path, kind: Kind) -> std::io::Result<File> {
    let path = dir.join(format!("{}.log", kind.name()));
    if std::fs::metadata(&path).is_ok_and(|m| m.len() > LOG_CAP_BYTES) {
        let _ = std::fs::rename(&path, dir.join(format!("{}.log.1", kind.name())));
    }
    let mut o = OpenOptions::new();
    o.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    o.open(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_is_capped_and_jitter_only_shortens() {
        let p = Policy {
            jitter: 0.5,
            ..Policy::default()
        };
        assert_eq!(p.delay_for(1, 0.0), Duration::from_millis(500));
        assert_eq!(p.delay_for(2, 0.0), Duration::from_millis(1000));
        assert_eq!(p.delay_for(99, 0.0), p.max_delay);
        assert_eq!(p.delay_for(1, 1.0), Duration::from_millis(250));
        assert!(p.delay_for(99, 0.99) <= p.max_delay);
    }

    #[test]
    fn readiness_records_are_strict() {
        assert_eq!(
            parse_record(Kind::Api, "METIS_API_PORT=5123\n"),
            Ok(Some(5123))
        );
        assert!(parse_record(Kind::Api, "METIS_API_PORT=0").is_err());
        assert!(parse_record(Kind::Api, "METIS_API_PORT=70000").is_err());
        assert!(parse_record(Kind::Api, "INFO started on 5123").is_err());
        let ok = r#"{"event":"listening","protocol":1,"host":"127.0.0.1","port":4000,"pid":9}"#;
        assert_eq!(parse_record(Kind::Speech, ok), Ok(Some(4000)));
        assert!(parse_record(Kind::Speech, &ok.replace("127.0.0.1", "0.0.0.0")).is_err());
        assert!(parse_record(
            Kind::Speech,
            &ok.replace("\"protocol\":1", "\"protocol\":2")
        )
        .is_err());
        assert!(parse_record(Kind::Speech, "not json").is_err());
        let w = r#"{"event":"started","service":"worker","protocol":1,"pid":3}"#;
        assert_eq!(parse_record(Kind::Worker, w), Ok(None));
        assert!(parse_record(Kind::Worker, ok).is_err());
    }

    #[test]
    fn secrets_are_long_random_and_distinct() {
        let (a, b) = (new_secret().unwrap(), new_secret().unwrap());
        assert!(a.len() >= 32 && a != b);
    }

    #[test]
    fn aggregate_blocks_recording_unless_everything_ready() {
        let mk = |p: Phase| ServiceStatus {
            name: "x",
            phase: p,
            port: None,
            restarts: 0,
            detail: None,
        };
        let all = vec![mk(Phase::Ready), mk(Phase::Ready), mk(Phase::Ready)];
        assert!(aggregate(&all, false).recording_allowed);
        let model_down = vec![mk(Phase::Unhealthy), mk(Phase::Ready), mk(Phase::Ready)];
        let st = aggregate(&model_down, false);
        assert!(!st.recording_allowed && st.state == "degraded");
        let open = vec![mk(Phase::Ready), mk(Phase::Failed), mk(Phase::Pending)];
        assert_eq!(aggregate(&open, false).state, "unhealthy");
    }
}
