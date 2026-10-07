//! Metis desktop shell: window + supervised local stack (speech, API, worker).
pub mod audio;
pub mod backend;
pub mod machine;
pub mod session;
pub mod stack;

use serde::Serialize;
use session::{Emit, Handle, Msg, Snapshot};
use stack::{Config, Stack, StackStatus};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

/// Event the HUD listens to; payload is a sanitized [`Snapshot`].
pub const STATE_EVENT: &str = "capture:state";

/// Name of the packaged backend (matches `bundle.externalBin`).
const BACKEND: &str = "metis-backend";

#[derive(Default)]
struct AppState {
    stack: Arc<Mutex<Option<Stack>>>,
    startup_error: Mutex<Option<String>>,
    capture: Mutex<Option<(Handle, JoinHandle<()>)>>,
}

struct TauriEmit(tauri::AppHandle);

impl Emit for TauriEmit {
    fn emit(&self, snap: &Snapshot) {
        let _ = self.0.emit(STATE_EVENT, snap);
    }
}

#[derive(Serialize)]
struct DesktopStatus {
    mode: &'static str,
    version: &'static str,
    /// Sanitised aggregate health; None only if startup itself failed.
    stack: Option<StackStatus>,
    error: Option<String>,
}

/// Camel-case, exactly the shape `apps/web/src/lib/desktop.ts` parses.
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ConnectionDto {
    api_base_url: String,
    bearer: String,
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
        stack: state.stack.lock().unwrap().as_ref().map(Stack::status),
        error: state.startup_error.lock().unwrap().clone(),
    }
}

/// Current capture snapshot, for a HUD that mounts after the last event.
#[tauri::command]
fn capture_state(state: tauri::State<'_, AppState>) -> Result<Snapshot, String> {
    match state.capture.lock().unwrap().as_ref() {
        Some((h, _)) => Ok(h.snapshot.lock().unwrap().clone()),
        None => Err("unavailable".into()),
    }
}

/// Explicit cancel: discards a recording, abandons in-flight work, dismisses an error.
#[tauri::command]
fn capture_cancel(state: tauri::State<'_, AppState>) {
    if let Some((h, _)) = state.capture.lock().unwrap().as_ref() {
        h.send(Msg::Cancel);
    }
}

/// Microphone onboarding: opens the mic once so macOS asks for consent.
#[tauri::command]
fn capture_prepare_mic(state: tauri::State<'_, AppState>) {
    if let Some((h, _)) = state.capture.lock().unwrap().as_ref() {
        h.send(Msg::PrepareMic);
    }
}

/// Rejects exactly `not_ready` while the stack starts, `unavailable` when it cannot.
#[tauri::command]
fn desktop_connection(state: tauri::State<'_, AppState>) -> Result<ConnectionDto, String> {
    match state.stack.lock().unwrap().as_ref() {
        Some(stack) => stack
            .connection()
            .map(|c| ConnectionDto {
                api_base_url: c.api_base_url,
                bearer: c.bearer,
            })
            .map_err(str::to_string),
        None => Err("unavailable".into()),
    }
}

/// Visible startup error instead of a blank window.
fn error_script(msg: &str) -> String {
    let msg = serde_json::to_string(msg).unwrap();
    format!(
        "addEventListener('DOMContentLoaded',()=>{{const d=document.createElement('div');d.setAttribute('role','alert');d.style.cssText='position:fixed;inset:0 0 auto 0;z-index:99999;background:#7f1d1d;color:#fff;padding:12px;font:14px system-ui';d.textContent='Metis desktop: '+{msg};document.body.prepend(d)}})"
    )
}

fn start_stack(data_dir: PathBuf) -> Result<Stack, String> {
    let backend = std::env::current_exe()
        .map_err(|e| e.to_string())?
        .parent()
        .ok_or("no executable directory")?
        .join(format!("{BACKEND}{}", std::env::consts::EXE_SUFFIX));
    Stack::start(Config::production(backend, &data_dir))
}

/// Cmd+Option+Space, with separate press and release. A registration failure
/// (another app owns the chord, or the OS refuses) is shown, never swallowed.
fn register_hotkey(app: &tauri::AppHandle, handle: &Handle) {
    let shortcut = Shortcut::new(Some(Modifiers::SUPER | Modifiers::ALT), Code::Space);
    let h = handle.clone();
    let result = app
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .map_err(|e| e.to_string())
        .and_then(|_| {
            app.global_shortcut()
                .on_shortcut(shortcut, move |_, _, event| {
                    h.send(match event.state() {
                        ShortcutState::Pressed => Msg::Down,
                        ShortcutState::Released => Msg::Up,
                    })
                })
                .map_err(|e| e.to_string())
        });
    if let Err(e) = result {
        handle.send(Msg::Fail {
            step: "shortcut",
            message: format!(
                "Cmd+Option+Space could not be registered (another app may use it): {e}"
            ),
        });
    }
}

pub fn run() {
    tauri::Builder::default()
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            health,
            desktop_status,
            desktop_connection,
            capture_state,
            capture_cancel,
            capture_prepare_mic
        ])
        .setup(|app| {
            let state = app.state::<AppState>();
            let data_dir = app.path().app_data_dir().map_err(|e| e.to_string());
            let started = data_dir.clone().and_then(start_stack);
            match started {
                Ok(stack) => *state.stack.lock().unwrap() = Some(stack),
                Err(e) => *state.startup_error.lock().unwrap() = Some(e),
            }
            // The controller always exists so failures stay visible; it refuses
            // to record unless the stack reports `recording_allowed`.
            let (handle, join) = session::spawn(
                || Box::new(audio::CpalSource::default()),
                Arc::new(backend::HttpBackend::new(state.stack.clone())),
                Arc::new(TauriEmit(app.handle().clone())),
                data_dir.ok().map(|d| d.join("mic_confirmed")),
            );
            register_hotkey(app.handle(), &handle);
            *state.capture.lock().unwrap() = Some((handle, join));
            Ok(())
        })
        .on_page_load(|w, _| {
            let state = w.app_handle().state::<AppState>();
            let err = state.startup_error.lock().unwrap().clone();
            if let Some(e) = err {
                let _ = w.eval(error_script(&e));
            }
        })
        .build(tauri::generate_context!())
        .expect("error building Metis desktop")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                // Release the microphone and in-flight work before the stack stops.
                if let Some((h, join)) = app.state::<AppState>().capture.lock().unwrap().take() {
                    h.send(Msg::Shutdown);
                    let _ = join.join();
                }
                if let Some(mut s) = app.state::<AppState>().stack.lock().unwrap().take() {
                    s.shutdown();
                }
            }
        });
}
