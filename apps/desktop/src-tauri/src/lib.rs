//! Metis desktop shell: window + supervised local stack (speech, API, worker).
pub mod audio;
pub mod backend;
pub mod hud;
pub mod machine;
pub mod session;
pub mod stack;

use serde::Serialize;
use session::{Emit, Handle, Msg, Snapshot};
use stack::{Config, Stack, StackStatus};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Instant;
use tauri::{Emitter, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
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

/// Publishes `capture:state` and drives the HUD window from the same snapshots.
struct TauriEmit {
    app: tauri::AppHandle,
    plan: Arc<Mutex<hud::Plan>>,
    t0: Instant,
}

impl TauriEmit {
    fn new(app: tauri::AppHandle) -> Self {
        Self {
            app,
            plan: Arc::default(),
            t0: Instant::now(),
        }
    }
}

fn apply_visibility(app: &tauri::AppHandle, v: Option<hud::Visibility>) {
    let (Some(v), Some(w)) = (v, app.get_webview_window(hud::LABEL)) else {
        return;
    };
    // `show` on a non-focusable window orders it front without activating the app.
    let _ = match v {
        hud::Visibility::Show => w.show(),
        hud::Visibility::Hide => w.hide(),
    };
}

impl Emit for TauriEmit {
    fn emit(&self, snap: &Snapshot) {
        let _ = self.app.emit(STATE_EVENT, snap);
        let now = self.t0.elapsed().as_millis() as u64;
        let v = self.plan.lock().unwrap().on_state(snap.state, now);
        apply_visibility(&self.app, v);
        if matches!(
            snap.state,
            session::State::Review | session::State::Saved | session::State::Error
        ) {
            // A stale timer is harmless: `on_tick` checks the dwell against the
            // newest terminal state, so it can never cut a newer one short.
            let (app, plan, t0) = (self.app.clone(), self.plan.clone(), self.t0);
            std::thread::spawn(move || {
                std::thread::sleep(hud::DWELL);
                let v = plan
                    .lock()
                    .unwrap()
                    .on_tick(t0.elapsed().as_millis() as u64);
                apply_visibility(&app, v);
            });
        }
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

/// HUD "open in Metis": shows and focuses `main`, then tells it which capture
/// to open. The only input is an optional entity id; anything else is refused.
#[tauri::command]
fn show_main_window(app: tauri::AppHandle, capture_id: Option<String>) -> Result<(), String> {
    if capture_id
        .as_deref()
        .is_some_and(|c| !hud::valid_entity_id(c))
    {
        return Err("invalid capture id".into());
    }
    let main = app
        .get_webview_window("main")
        .ok_or("main window missing")?;
    let _ = main.unminimize();
    main.show().map_err(|e| e.to_string())?;
    let _ = main.set_focus();
    let _ = app.emit_to(
        "main",
        hud::OPEN_REVIEW_EVENT,
        hud::OpenReview { capture_id },
    );
    Ok(())
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
            capture_prepare_mic,
            show_main_window
        ])
        .setup(|app| {
            let state = app.state::<AppState>();
            let data_dir = app.path().app_data_dir().map_err(|e| e.to_string());
            let started = data_dir.clone().and_then(start_stack);
            match started {
                Ok(stack) => *state.stack.lock().unwrap() = Some(stack),
                Err(e) => *state.startup_error.lock().unwrap() = Some(e),
            }
            // Hidden until capture leaves Idle; see `hud::Plan`. Not focusable, so
            // showing it never takes the keyboard from the app being dictated into.
            if let Err(e) =
                WebviewWindowBuilder::new(app, hud::LABEL, WebviewUrl::App(hud::ROUTE.into()))
                    .title("Metis HUD")
                    .inner_size(hud::WIDTH, hud::HEIGHT)
                    .resizable(false)
                    .decorations(false)
                    .always_on_top(true)
                    .skip_taskbar(true)
                    .focusable(false)
                    .focused(false)
                    .visible(false)
                    .accept_first_mouse(true)
                    .build()
            {
                eprintln!("metis: HUD window not created: {e}");
            }
            // The controller always exists so failures stay visible; it refuses
            // to record unless the stack reports `recording_allowed`.
            let (handle, join) = session::spawn(
                || Box::new(audio::CpalSource::default()),
                Arc::new(backend::HttpBackend::new(state.stack.clone())),
                Arc::new(TauriEmit::new(app.handle().clone())),
                data_dir.ok().map(|d| d.join("mic_confirmed")),
            );
            register_hotkey(app.handle(), &handle);
            *state.capture.lock().unwrap() = Some((handle, join));
            Ok(())
        })
        .on_page_load(|w, _| {
            if w.label() != "main" {
                return;
            }
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Documents the exact wire format the webview sees. Deliberately mixed:
    /// `capture:state` and `desktop_connection` are camelCase; `desktop_status`
    /// (and its `StackStatus`/`ServiceStatus`) keeps Rust snake_case; enum
    /// values (`state`, `phase`) are snake_case lowercase. Changing any of these
    /// breaks apps/web, so this test must be updated together with it.
    #[test]
    fn wire_format_is_pinned() {
        let snap = serde_json::to_value(session::Snapshot::default_for_test()).unwrap();
        let mut keys: Vec<_> = snap.as_object().unwrap().keys().cloned().collect();
        keys.sort();
        assert_eq!(
            keys,
            [
                "adapterMode",
                "captureId",
                "elapsedMs",
                "error",
                "extractionMs",
                "locked",
                "micReady",
                "needsClarification",
                "releaseToTranscriptMs",
                "state",
                "transcript",
                "truncated"
            ]
        );
        assert_eq!(snap["state"], "idle");

        let status = serde_json::to_value(DesktopStatus {
            mode: "debug",
            version: "0",
            stack: Some(StackStatus {
                state: "ready",
                recording_allowed: true,
                services: vec![],
            }),
            error: None,
        })
        .unwrap();
        assert_eq!(
            status,
            json!({"mode":"debug","version":"0","error":null,
                   "stack":{"state":"ready","recording_allowed":true,"services":[]}})
        );

        let conn = serde_json::to_value(ConnectionDto {
            api_base_url: "u".into(),
            bearer: "b".into(),
        })
        .unwrap();
        assert_eq!(conn, json!({"apiBaseUrl":"u","bearer":"b"}));
    }
}
