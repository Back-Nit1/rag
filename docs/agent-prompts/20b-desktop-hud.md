# Prompt: desktop hold-to-talk HUD

```yaml
task_id: desktop-hud
owner: frontend
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [apps/web]
read_first:
  - CLAUDE.md
  - docs/spec/interface.md
  - apps/web/src/lib/status.ts
  - apps/web/src/lib/captureFlow.ts
dependencies: [ui-overhaul, desktop-runtime-bridge, desktop-hold-to-talk]
```

Add a dedicated compact route for the Tauri always-on-top HUD while reusing the
redesigned web application's tokens, status vocabulary and proposal components.
Do not fork contracts, API types or a second design system.

Read and apply the installed frontend-design, web-guideline, React and testing
skills used by the UI-overhaul worker.

Use Rust events for ephemeral gesture/audio/process state and `CaptureDetail`
for durable server state. Display Idle, Armed, Recording, Transcribing,
Extracting, Review, Clarification and Error honestly. Show Saved only after an
explicit proposal resolution or server evidence of created records; proposal
creation alone is not Saved.

Surface short-tap drop, cancellation, microphone denial, hotkey conflict,
speech loading/not-ready, missing model, API/worker/speech death, timeout,
silence and fixture adapters without leaking secrets, usernames in filesystem
paths, raw logs or exception traces. Real local ASR and fixture extraction must
be labeled independently.

The compact window remains keyboard accessible, screen-reader meaningful,
high-contrast and reduced-motion aware. It must not trap focus or steal focus
on every status update. Provide a clear path to open the full review surface.

Validate unit/component logic, typecheck, production build and a manual Tauri
HUD walkthrough. Capture screenshots for idle, recording, processing, review
and error states. Commit only explicit `apps/web/` paths and return the standard
handoff.
