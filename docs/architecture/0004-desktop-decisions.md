# ADR 0004 — desktop shell decisions

Status: accepted 2026-10-07 (coordinator). Spec: `docs/superpowers/specs/2026-10-06-metis-desktop-push-to-talk-design.md`.

1. **Sidecars.** Prototype first (prompt 11/16). Default plan: uv-managed
   Python runtime per target triple, launched as Tauri `externalBin`; PyInstaller
   only if the uv route fails. Artifacts named `<name>-<target-triple>`.
2. **Auth.** `METIS_DESKTOP_AUTH_SECRET` (32+ chars, per launch, env only)
   authenticates the caller. When set, a missing or wrong bearer is rejected and
   a bearer is **never** sent to Cognito. Ownership is a stable per-install
   subject (`desktop_<uuid4hex>`), created by the API at
   `METIS_DESKTOP_IDENTITY_PATH` on first use. Refused when `METIS_ENV=prod`.
   Fixture identity behaviour is unchanged when the secret is unset.
3. **UI reuse.** The Tauri main window loads the `apps/web` production build.
   The HUD is a second window on a `#/hud` route of the same build. No
   second frontend.
4. **Network.** Sidecars bind `127.0.0.1:0`, report the port on stdout. Only
   loopback is accepted (config rejects other speech endpoints). CORS allows the
   Tauri origin (`tauri://localhost`, `http://tauri.localhost`); CSP
   `connect-src` is limited to `http://127.0.0.1:*`.
5. **Models.** Directory `METIS_SPEECH_MODEL_DIR` under the app data dir.
   Pinned revision + SHA-256 fetched at install time by packaging, never at
   runtime, never committed. First run without weights says so and offers the fetch.
6. **Identity/storage.** One SQLite file and object dir per install under the
   OS app-data dir; path passed via `METIS_SQLITE_PATH` / `METIS_LOCAL_OBJECTS_DIR`.
7. **Runtime connection data.** API base URL and bearer reach the webview via a
   Tauri command held in memory. Not Vite env, URL, localStorage or cookies.
8. **Speech selection.** `METIS_SPEECH_ENGINE` (`auto|fixture|faster_whisper|remote`)
   is independent of `METIS_MODE`; `remote` + loopback endpoint + secret.
9. **Mic privacy.** Capture begins on key-down; the Armed interval is kept.
   No always-on pre-roll, no buffer before the keypress.
10. **Capture status.** `CaptureDetail` carries owner-scoped jobs. Extraction
    failures need a contract field only if jobs prove insufficient; propose
    via handoff.
11. **Proposals.** Review/Clarification is the normal terminal state until the
    user resolves a proposal. "Saved" only after explicit resolution.

Supersedes the spec's 500ms pre-roll ring buffer (decision 9).
