//! Metis desktop shell: window + health command + sidecar supervision.
pub mod sidecar;

use serde::Serialize;
use std::sync::{Arc, Mutex};
use tauri::{Manager, RunEvent};

/// Name of the packaged sidecar (matches `bundle.externalBin`).
const HEALTH_SIDECAR: &str = "metis-health-sidecar";

#[derive(Clone, Serialize, Default)]
struct SidecarStatus {
    state: String,
    port: Option<u16>,
    error: Option<String>,
}

#[derive(Default)]
struct AppState {
    status: Arc<Mutex<SidecarStatus>>,
    handle: Mutex<Option<sidecar::Handle>>,
}

#[derive(Serialize)]
struct DesktopStatus {
    mode: &'static str,
    version: &'static str,
    sidecar: SidecarStatus,
}

#[tauri::command]
fn health() -> &'static str {
    "ok"
}

#[tauri::command]
fn desktop_status(state: tauri::State<'_, AppState>) -> DesktopStatus {
    DesktopStatus {
        mode: if cfg!(debug_assertions) {
            "debug"
        } else {
            "release"
        },
        version: env!("CARGO_PKG_VERSION"),
        sidecar: state.status.lock().unwrap().clone(),
    }
}

fn set_status(status: &Mutex<SidecarStatus>, ev: &sidecar::Event) {
    use sidecar::Event::*;
    let mut s = status.lock().unwrap();
    match ev {
        Ready { port } => {
            *s = SidecarStatus {
                state: "ready".into(),
                port: Some(*port),
                error: None,
            }
        }
        Exited { expected: true, .. } => s.state = "stopped".into(),
        Exited { code, .. } => {
            *s = SidecarStatus {
                state: "crashed".into(),
                port: None,
                error: Some(format!("exited unexpectedly (code {code:?})")),
            }
        }
        Restarting { attempt, .. } => s.state = format!("restarting#{attempt}"),
        GaveUp { attempts } => {
            s.state = "failed".into();
            s.error = Some(format!("gave up after {attempts} restarts"));
        }
        SpawnFailed(e) => {
            *s = SidecarStatus {
                state: "failed".into(),
                port: None,
                error: Some(e.clone()),
            }
        }
    }
}

/// Visible startup error instead of a blank window.
fn error_script(msg: &str) -> String {
    let msg = serde_json::to_string(msg).unwrap();
    format!(
        "addEventListener('DOMContentLoaded',()=>{{const d=document.createElement('div');d.setAttribute('role','alert');d.style.cssText='position:fixed;inset:0 0 auto 0;z-index:99999;background:#7f1d1d;color:#fff;padding:12px;font:14px system-ui';d.textContent='Metis desktop: '+{msg};document.body.prepend(d)}})"
    )
}

pub fn run() {
    let state = AppState::default();
    let status = state.status.clone();
    let startup_error: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));
    let err_for_load = startup_error.clone();

    let result = sidecar::bundled_path(HEALTH_SIDECAR)
        .map_err(|e| e.to_string())
        .and_then(|path| {
            let status = status.clone();
            sidecar::spawn(
                path,
                vec![],
                sidecar::Policy::default(),
                Arc::new(move |ev| set_status(&status, &ev)),
            )
            .map_err(|e| e.to_string())
        });
    match result {
        Ok(h) => *state.handle.lock().unwrap() = Some(h),
        Err(e) => {
            *status.lock().unwrap() = SidecarStatus {
                state: "failed".into(),
                port: None,
                error: Some(e.clone()),
            };
            *startup_error.lock().unwrap() = Some(e);
        }
    }

    tauri::Builder::default()
        .manage(state)
        .invoke_handler(tauri::generate_handler![health, desktop_status])
        .on_page_load(move |w, _| {
            if let Some(e) = err_for_load.lock().unwrap().as_ref() {
                let _ = w.eval(error_script(e));
            }
        })
        .build(tauri::generate_context!())
        .expect("error building Metis desktop")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut h) = app.state::<AppState>().handle.lock().unwrap().take() {
                    h.shutdown();
                }
            }
        });
}
