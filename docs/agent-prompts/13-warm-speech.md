# Prompt: warm local speech server

```yaml
task_id: speech-warm-server
owner: speech
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [services/speech]
read_first:
  - CLAUDE.md
  - docs/spec/speech.md
  - docs/superpowers/specs/2026-10-06-metis-desktop-push-to-talk-design.md
  - services/speech/src/metis_speech/transcriber.py
  - services/speech/src/metis_speech/worker.py
dependencies: [coordinator-baseline, desktop-auth-protocol]
```

Implement the local loopback speech server proposed by the desktop design. It
must load one `FasterWhisperTranscriber`, warm it once, and reuse it across
bounded sequential decode requests. Keep the existing one-shot cloud worker
unchanged in behavior; both paths use the same transcriber implementation.
Also implement the speech-service-local remote `SpeechTranscriber` client used
by the existing pipeline worker; a server with no caller is not complete.

Inspect installed Python/speech skills and read only those that apply. Do not
download a model during tests or at runtime. Model artifacts are supplied from
a configured local directory and must be pinned/verified by the packaging
workflow outside this task.

## Required behavior

- Bind only to `127.0.0.1` on an OS-assigned port and report a small
  machine-readable ready record to stdout without secrets.
- Require the coordinator-approved per-launch authentication secret on every
  decode/readiness request.
- While the model is loading, return an explicit not-ready response; never hang
  indefinitely or queue unbounded audio.
- Enforce encoded byte and decoded-duration limits, supported media validation,
  silence handling and one decode at a time.
- Return transcript text, ordered segments and existing speech metadata with
  honest adapter mode/model information.
- Select the remote client through coordinator-approved speech-engine/endpoint
  configuration independently of `METIS_MODE`, allowing local state and
  fixture extraction alongside real local speech. Prevent self-recursive
  server configuration.
- Bound request bodies, timeouts and error text. Do not accept file paths or
  arbitrary URLs from callers.
- Handle SIGTERM/parent shutdown cleanly and leave no temporary audio behind.
- Keep raw audio and transcripts out of logs.

Prefer a minimal server surface. A new dependency may be declared in
`services/speech/pyproject.toml`, but do not edit the root `uv.lock`; request
that update from the coordinator.

## Tests

- Fake transcriber proves warm-once and repeated requests.
- Loading/not-ready, invalid auth, oversized/corrupt/silent audio, busy server,
  decode failure and shutdown are covered.
- A real-decoder integration test is marked appropriately and skipped unless
  pinned artifacts and ASR extras are present.
- No fixture result is labeled as real ASR.

## Validation

```bash
uv run pytest services/speech/tests
uv run ruff check services/speech
uv run ruff format --check services/speech
uv run mypy services/speech
```

If artifacts are available, also run and report one real cold and one warm
decode with machine/model/revision/parameters. Otherwise state that the neural
gate is unrun. Commit only explicit `services/speech/` paths and return the
standard handoff plus the sidecar invocation/readiness protocol.
