# Prompt: real local ASR evaluation gate

```yaml
task_id: speech-real-evaluation
owner: speech
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [services/speech]
read_first:
  - CLAUDE.md
  - docs/spec/speech.md
  - services/speech/README.md
  - services/speech/fixtures/evaluation_set.json
dependencies: [speech-warm-server, approved-model-artifact-source]
```

Run the owned neural speech path for real and record reproducible evidence.
This task requires explicit user/coordinator approval before downloading model
artifacts or installing heavy ASR dependencies. Never commit model weights,
private recordings, caches or credentials.

Use a pinned faster-whisper model revision and verified SHA-256 from the
coordinator-approved artifact source. Record machine architecture, OS, Python,
library versions, model/revision/checksum, compute type, thread count, beam,
VAD, language and conditioning settings.

Create or use consented/synthetic audio for the existing evaluation sentences.
Do not commit a person's private recordings. Commit only deliberately approved
small fixtures, anonymized hypotheses and aggregate reports. Keep the held-out
split untouched while tuning.

## Required measurements

- One cold and multiple warm decodes, separating load, decode and wall time.
- WER plus name, date-expression and negation fidelity on dev and held-out
  splits.
- Silence/non-speech behavior.
- At least one corrupt, unsupported and over-duration rejection.
- Warm loopback-server latency compared with the one-shot worker path.
- Actual peak memory/CPU observation when the environment supports it.

Do not present echoed-reference output from `make speech-bench` as accuracy.
Do not claim universal quality from this pilot. If an artifact, codec or
hardware prerequisite is missing, stop and report the exact blocker.

## Validation

Run the speech unit suite, real-decoder integration test and evaluator with an
explicit hypotheses file. Return exact commands, raw summary metrics, unrun
cases and the recommended provisional model/settings. Commit only explicit
approved paths under `services/speech/` and return the standard handoff.
