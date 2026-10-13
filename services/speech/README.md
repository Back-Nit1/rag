# services/speech — Metis owned speech-to-text

Self-hosted ASR around **pretrained open-source Whisper weights**
(faster-whisper / CTranslate2, CPU, INT8). Metis owns the capture UX, the upload
API, validation, the inference container, lifecycle, the correction flow,
deployment and the metrics. No OpenAI, Deepgram, AssemblyAI, ElevenLabs or
Amazon Transcribe key is required; AWS IAM roles are still needed for the AWS
infrastructure itself.

This is **self-hosted inference, not training**. Training an ASR model from
scratch is out of scope: it would need a labelled speech corpus, substantial
training compute and its own evaluation. Saving transcripts and correcting
mistakes does not update any model weights.

## Pieces

| File | Role |
| --- | --- |
| `transcriber.py` | `FasterWhisperTranscriber` (production) and `FixtureTranscriber` (local, always reports `adapter_mode=fixture`) |
| `audio.py` | envelope checks, PyAV decode to mono 16 kHz, decoded-duration enforcement, silence detection |
| `server.py` | warm loopback HTTP server (`/v1/transcribe`, `/v1/ready`, `/v1/flow/sessions…`), bearer secret, one decode at a time, `--port` for a fixed port |
| `flow.py` | hold-to-dictate sessions: in-memory PCM buffer, one live session, size cap |
| `polish.py` | deterministic dictation cleanup: fillers, case, dictionary, snippets, per-app style |
| `remote.py` | client used by the API/worker to reach the warm server |
| `worker.py` | container entrypoint: one job, persist the result, exit with a category-specific code |
| `evaluate.py` | WER plus separate name / date / negation fidelity, dev vs held-out splits |
| `Dockerfile` | model artifacts pinned and baked at build time; non-root; one job per container |

## Dictation sessions (Flow)

Served by `server.py`; every request needs `Authorization: Bearer $METIS_SPEECH_SECRET`.
Audio is raw PCM16 mono 16 kHz, held in memory only, never logged.

| Request | Result |
| --- | --- |
| `POST /v1/flow/sessions` `{language?, app?, settings?}` | `{session_id, max_seconds}`; a new session supersedes an abandoned one |
| `POST /v1/flow/sessions/{id}/audio` raw chunk | `{seconds, partial}`; `partial.provisional` is always `true`, HUD-only |
| `POST /v1/flow/sessions/{id}/finish` | `{text, raw_text, applied, no_speech, segments, speech}`; the only insertable text |
| `DELETE /v1/flow/sessions/{id}` | discard the audio |

`settings` is a `FlowSettings` (contracts). `polish` never changes dates, names
or negation; `raw_text` is returned beside the polished text, and an empty
recording sets `no_speech` and inserts nothing. See
`docs/architecture/0005-flow-dictation.md`.

## Why the duration check is on decoded audio

A container header can claim any duration. `decode_to_mono16k` counts actual
samples and aborts at the configured limit, so an oversized or pathological file
cannot consume unbounded decode time or memory.

## Why silence is detected before extraction

An empty or no-speech recording returns empty text with `no_speech: true`. The
pipeline then creates no proposal, so a silent fixture can never produce a task
or a reminder.

## Exit codes

| Code | Meaning | State machine behaviour |
| --- | --- | --- |
| 0 | result persisted | success |
| 1 | retryable fault | retry within the attempt budget |
| 2 | malformed job input | do not retry |
| 3 | permanent rejection (unsupported media, too large, forbidden key) | do not retry; surface an actionable error |

A successful `RunTask` is **not** a successful transcription. The caller reads
the persisted result.

## Model artifacts

```bash
docker build -t metis-speech:dev \
  --build-arg MODEL_ID=small.en \
  --build-arg MODEL_REPO=Systran/faster-whisper-small.en \
  --build-arg MODEL_REVISION=<commit sha> \
  --build-arg MODEL_SHA256=<sha256 of model.bin> \
  -f services/speech/Dockerfile .
```

Pin `MODEL_REVISION` and `MODEL_SHA256`; the build prints the digest it saw and
fails on a mismatch. Mirror the artifacts into a private location (ECR image or
an S3 artifact bucket) for repeatable deployments. `small.en` and `base.en` are
English-only — a multilingual checkpoint is required for other languages, and
`small.en` is **provisional** until the project's own evaluation passes.

## Evaluation

```bash
# 1. record your own audio for each reference into fixtures/audio/<id>.webm
# 2. run the worker over them and collect {id: transcript} into hypotheses.json
make speech-bench                                  # metric code, echoed references
python -m metis_speech.evaluate --hypotheses hypotheses.json --json
```

24 synthetic references cover clean speech, noise, fast speech, pauses, project
names (Cerno, Themis), Bloomberg/New York, spoken dates, negation and silence.
Held-out items exist so decode parameters are not tuned on the evaluation
split. No audio is committed, and a pilot of this size cannot support a
universal accuracy claim.

## Cost reality

An open-source decoder removes per-minute speech-provider charges. It does not
make cloud inference free: billed Fargate runtime starts at image pull and has a
one-minute minimum, and ECR storage, object storage/requests, orchestration
transitions, logs and networking all bill separately. Measure cold and warm runs
separately and price them against the region's rate card — see
`ops/cost-worksheet.md`.
