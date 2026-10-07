# Prompt: native hold-to-talk desktop capture

```yaml
task_id: desktop-hold-to-talk
owner: desktop
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [apps/desktop]
read_first:
  - CLAUDE.md
  - docs/superpowers/specs/2026-10-06-metis-desktop-push-to-talk-design.md
  - docs/spec/interface.md
  - docs/spec/speech.md
dependencies: [desktop-sidecars, desktop-runtime-bridge]
```

Extend the integrated Tauri foundation into the first complete desktop
hold-to-talk slice. Do not edit `apps/web`, Python services, contracts or root
files. Use the published API and sidecar protocols; request changes from their
owners.

Read any installed Tauri/Rust/audio testing skill that genuinely applies. Use
the current official Tauri global-shortcut and capability documentation rather
than recalled APIs.

## Required implementation

- Global `Cmd+Option+Space` with separate press/release handling and a visible
  conflict/error when registration fails.
- Native microphone capture at an appropriate device rate, converted safely to
  16 kHz mono for the finalized payload.
- A bounded sample buffer beginning at key-down. Preserve the approximately
  150 ms Armed interval when recording begins; do not capture pre-keypress
  audio or create an always-on microphone.
- State machine: Idle, Armed, Recording, Transcribing, Extracting, Review,
  Saved and Error. A short accidental tap creates no capture.
- Double-tap lock mode and explicit stop/cancel behavior, if the integrated
  foundation can support it without destabilizing the primary hold gesture.
- Upload through the existing capture/create-upload-complete-status flow with
  idempotency and the per-launch local secret.
- Emit the sanitized gesture/audio/process state needed by the separately
  owned HUD route; do not fork a second frontend or design system in Rust.
- Microphone permission onboarding before the first hotkey gesture.
- Release devices, buffers and child processes on cancel, error and app exit.

Bare Right-Option/CGEventTap, focused-app text insertion, Accessibility
permission, clipboard mutation, signing, notarization and auto-update are not
part of this task.

## Acceptance

- A scripted WAV can traverse desktop capture → API source → transcript →
  extraction proposal without a physical microphone.
- Hold/release with a microphone produces exactly one logical capture.
- Short tap, cancel, duplicate release and sidecar retry do not create duplicate
  captures.
- Sample-buffer boundary and Armed-interval retention have deterministic tests.
- Every state can reach a clear error and recover to idle.
- Missing permission, missing model, speech-not-ready, API death and shortcut
  conflict are visible and do not silently record.
- Fixture inference/transcription is labeled; real ASR metadata is not faked.

## Validation

Run formatter, clippy, Cargo tests, desktop package tests/build, the scripted
WAV E2E scenario, and one manual microphone pass when hardware permission is
available. Report latency from key release to transcript separately from
extraction time. Do not quote estimates as measurements.

Commit only explicit `apps/desktop/` paths. Return the standard handoff plus
measured/unmeasured latency, permission behavior, and known platform limits.
