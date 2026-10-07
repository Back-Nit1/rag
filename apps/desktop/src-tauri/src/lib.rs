//! Metis desktop shell: window + supervised local stack (speech, API, worker).
pub mod stack;

use serde::Serialize;
use stack::{Config, Stack, StackStatus};
use std::path::PathBuf;
use std::sync::Mutex;
use tauri::{Manager, RunEvent};

/// Name of the packaged backend (matches `bundle.externalBin`).
const BACKEND: &str = "metis-backend";

#[derive(Default)]
struct AppState {
    stack: Mutex<Option<Stack>>,
    startup_error: Mutex<Option<String>>,
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

pub fn run() {
    tauri::Builder::default()
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            health,
            desktop_status,
            desktop_connection
        ])
        .setup(|app| {
            let state = app.state::<AppState>();
            let started = app
                .path()
                .app_data_dir()
                .map_err(|e| e.to_string())
                .and_then(start_stack);
            match started {
                Ok(stack) => *state.stack.lock().unwrap() = Some(stack),
                Err(e) => *state.startup_error.lock().unwrap() = Some(e),
            }
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
                if let Some(mut s) = app.state::<AppState>().stack.lock().unwrap().take() {
                    s.shutdown();
                }
            }
        });
}
