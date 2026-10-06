# Metis desktop: hold-to-talk capture

Design spec, 2026-10-06. Milestone 1 of the desktop pivot.

## Why

Metis today is a browser PWA. Capture requires opening a tab, clicking into a
screen and pressing a button. That is too much friction for the product's
actual job: catching a thought at the moment it occurs.

The target interaction is the one Wispr Flow popularised — hold a key, speak,
release, and the words are already working for you. Flow itself is **not** a
model for the internals: it is 100% cloud, streaming audio to its servers with
an OpenAI subprocessor and a fine-tuned Llama for cleanup, hitting ~700ms p99
including a ~200ms network budget. Metis copies the *interaction*, not the
architecture. Everything stays on the machine.

The relevant prior art for the local build is
[whispr-bro](https://github.com/Micaxes/whispr-bro/issues/1), a fully on-device
Flow clone: 500ms pre-roll ring buffer, Silero VAD, local ASR, 480–650ms
end-to-end with no network code in the binary.

## Goals

* Hold a key anywhere in the OS, speak, release, and the utterance enters the
  existing capture pipeline: source → transcript → extraction → proposal →
  tasks/events/reminders, with retrieval over prior memories.
* No cloud account, no provider API key, no model id with a secret. A fresh
  clone of this repo must work offline.
* Reuse `services/` unchanged. The pivot is additive.
* Honest state at every step. "Transcribing" is not "transcribed"; a fixture
  transcript is never displayed as a real one.

## Non-goals for this milestone

* Inserting text into the focused application. That needs Accessibility,
  Input Monitoring, secure-field refusal and clipboard save/restore — a
  subsystem of its own (milestone 3).
* Replacing Bedrock extraction and Titan embeddings with local models
  (milestone 2). Desktop runs `METIS_MODE=local` and therefore the fixture
  inference client until then, which the UI labels as a fixture.
* Code signing, notarization, auto-update, installers (milestone 4).
* Windows and Linux hotkey parity. The chord path is cross-platform by
  construction; the bare-modifier path is macOS-first.

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Desktop shell | Tauri v2 | Keeps `apps/web` and every Python service. ~10MB binary. Rust owns hotkey, audio and process supervision. |
| ASR on the hot path | existing `FasterWhisperTranscriber` | Already written, already tested, no API key, cross-platform. `SpeechTranscriber` stays the port so Parakeet/CoreML drops in later without touching callers. |
| Default trigger | hold `Cmd+Option+Space` | Expressible as a Tauri shortcut with `ShortcutState::Pressed`/`Released`, so hold-to-talk needs no permission beyond the microphone. |
| Optional trigger | hold bare Right-Option | What Flow users expect, but a bare modifier is not bindable as a shortcut: it needs a listen-only `CGEventTap`, which requires Input Monitoring. Opt-in, and the settings copy states the permission cost. |
| Transcript destination | the Metis pipeline | Not the focused app. See non-goals. |

### Rejected

* **Native Swift.** Flow-class latency on day one, but discards `apps/web` and
  forces `services/` to be reimplemented or demoted to a daemon. macOS only.
* **Electron.** Same reuse as Tauri at ~150MB and higher idle memory.
* **Parakeet CoreML first.** ~190ms is real, but it is macOS-arm64 only, so
  the faster-whisper path has to exist anyway. Two engines to maintain before
  the first build ships.
* **Hold "M" for 5 seconds**, as originally proposed. A global tap on a letter
  key fires whenever that letter is typed in any application, and a 5-second
  hold is longer than the entire latency budget it gates.

## Architecture

```
apps/desktop/                     Tauri v2
  src-tauri/
    hotkey.rs      chord via tauri-plugin-global-shortcut (Pressed/Released)
                   optional bare Right-Option via CGEventTap (macOS, opt-in)
    audio.rs       cpal 16kHz mono capture -> ring buffer (500ms pre-roll)
    vad.rs         silence trim, end-of-speech detection
    session.rs     utterance state machine
    sidecar.rs     supervises three Python children, health, restart, shutdown
    hud.rs         small always-on-top window
  src/             HUD route, reusing apps/web components

services/speech/
  serve.py         NEW: loopback server holding one warm transcriber

apps/web/          unchanged, loaded by the Tauri webview
services/*         unchanged
```

### Hot path

```
key down
  session: Idle -> Armed
  audio: ring buffer already recording; mark utterance start (pre-roll supplies
         the ~300-500ms of speech that precedes a human's keypress)
key up  (or VAD end-of-speech while locked)
  session: Recording -> Transcribing
  audio: finalize WAV bytes from the ring buffer, VAD-trimmed
  POST   /v1/captures                      -> capture_id, upload target
  PUT    /v1/local-uploads/{capture_id}    -> raw bytes, ownership enforced
  POST   /v1/captures/{capture_id}/complete (Idempotency-Key)
  outbox: capture.completed -> transcript.finalized -> extraction.requested
          -> extraction.completed
  HUD polls GET /v1/captures/{capture_id}
  session: Transcribing -> Extracting -> Review (or Saved, when nothing is missing)
```

Every HTTP step above already exists and is exercised by the browser client.
The Rust layer replaces `MediaRecorder`; nothing downstream changes.

### The one real gap: warm models

`metis_speech.worker` is one-shot by design — one job per process, then exit.
That is correct for `ecs:runTask.sync` and fatal for push-to-talk, because the
model reloads on every utterance.

`services/speech/serve.py` adds a loopback server that loads the model once at
startup (`FasterWhisperTranscriber.warm()`, which already exists for exactly
this reason) and decodes utterances on request. The one-shot worker is not
touched; cloud keeps using it. Both call the same `SpeechTranscriber` port, so
there is one decoder implementation, not two.

### Process supervision

Tauri spawns and owns three children, as `externalBin` sidecars:

1. `uvicorn metis_api.main:app` — the API
2. `python -m metis_pipeline.local_worker` — outbox dispatch + due reminders
3. `python -m metis_speech.serve` — the warm decoder

Rules:

* Each binds `127.0.0.1` on an ephemeral port, reported back to the shell on
  stdout. No fixed ports, no LAN exposure.
* A per-launch random secret is generated by the shell, passed to the children
  by environment variable, and sent as a bearer token on every request. This
  stops another local process from driving the user's Metis.
* `user_id` is still derived server-side. The shell never sends an owner, and
  the API must keep rejecting any attempt to. This is the project's standing
  ownership rule and the desktop shell is not an exception to it.
* Children are killed on app exit, including on crash, and restarted with
  backoff on unexpected exit. A dead sidecar surfaces in the HUD as a refusal
  to record, never as a silent failure.

### State machine

```
Idle -> Armed -> Recording -> Transcribing -> Extracting -> Review -> Idle
                                                         -> Saved  -> Idle
any  -> Error -> Idle
```

* Double-tap the trigger to lock hands-free; VAD end-of-speech or a second tap
  ends it.
* `Armed` exists so a key tapped by accident, shorter than ~150ms, is dropped
  without creating a capture.
* `Error` is reachable from every state and always says which step failed.

### Honest states

These are the project's rules applied to the HUD:

* "Transcribing", never "Transcribed", until a transcript revision is committed.
* If `adapter_mode` comes back `fixture`, the HUD labels it in the open. A
  fixture result must never be presented as production output.
* Reminder states keep `pending_schedule` / `scheduled` / `schedule_failed`.
  "Reminder scheduled" requires a confirmed scheduler sync, as today.
* Unknown fields stay empty. The HUD never guesses a date, a name or a
  registration status to fill a gap.

### Latency budget

| Stage | Target |
| --- | --- |
| key up → WAV finalized | ~5ms (in memory) |
| faster-whisper decode, warm, short utterance | 800–1500ms |
| capture POST + upload + complete, loopback | <20ms |
| extraction, fixture, local | <50ms |
| HUD first paint of the transcript | immediately on arrival |

End of speech to visible transcript lands near ~1s, not Flow's 700ms. That is
the accepted cost of option 1; the `SpeechTranscriber` port is the escape
hatch when Parakeet lands. The HUD therefore streams state transitions and
never blocks on the full pipeline.

## Testing

* Rust unit tests: ring buffer wraparound and pre-roll correctness; the
  utterance state machine including the short-tap drop and the double-tap lock;
  sidecar restart backoff.
* Python: `serve.py` decodes a WAV fixture and reports `adapter_mode`
  correctly; a cold request while the model is still loading returns a clear
  "not ready" rather than hanging.
* End-to-end, no microphone: a scripted WAV pushed through the sidecar stack
  asserting source → transcript → proposal, so it runs in CI.
* Manual, once per build: a real microphone utterance on a fresh profile with a
  fresh SQLite path, verifying the HUD states against the recorded outbox rows.

## Risks

* **Packaging Python as a sidecar** is the fiddliest part of the milestone
  (uv-managed runtime or PyInstaller, per platform). Prototype this before
  building the HUD; it is the step most likely to change the plan.
* **Model weight delivery.** Pinned URL plus SHA-256, fetched at install time,
  never at runtime — matching the refusal `FasterWhisperTranscriber._load()`
  already raises when artifacts are absent. First run without weights must say
  so plainly and offer the fetch, not download silently.
* **Microphone permission on first launch** arrives mid-gesture: the user holds
  the key, the OS prompt steals focus, the utterance is lost. Request it at
  onboarding, before the first hotkey is ever armed.
* **Hotkey conflicts.** `Cmd+Option+Space` collides with some Spotlight
  configurations. Detect registration failure and say so; do not fail silently.

## Open questions for the coordinator

* Does the HUD need a contract addition for its state projection, or is
  `CaptureDetail` sufficient? Current reading: sufficient. `packages/contracts/`
  is coordinator-owned, so if it is not, this goes through handoff rather than
  a second schema.
* Desktop single-user identity: local mode uses the fixture subject today.
  Milestone 4 should decide whether the desktop app keeps that or derives a
  per-install identity, since `METIS_ALLOW_FIXTURE_IDENTITY` is refused in prod.

## Milestones after this one

2. Local inference: llama.cpp or Ollama adapters behind the existing
   `InferenceClient` port, plus local embeddings, removing Bedrock and Titan
   from the offline path.
3. Insert into the focused application: AX direct-set with pasteboard fallback,
   secure-field refusal, clipboard restore.
4. Packaging: signing, notarization, model fetch UX, auto-update, identity.
