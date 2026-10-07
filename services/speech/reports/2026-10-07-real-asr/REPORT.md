# Real faster-whisper validation — 2026-10-07

Base SHA 3721caf (branch main). **Synthetic TTS audio (macOS `say`, voice default) re-encoded to Opus/WebM; no human speech.** TTS is cleaner and more regular than people, so WER here is an optimistic floor. 24 items, one speaker; not a universal accuracy claim.

## Artifact
- Repo `Systran/faster-whisper-small.en`, revision `d1d751a5f8271d482d14ca55d9e2deeebbae577f`
- `model.bin` sha256 `62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a` (483,545,366 B) — verified with `shasum -a 256` after download, matches HF LFS metadata
- config.json / tokenizer.json / vocabulary.txt covered by the revision pin only
- Stored outside git (scratchpad). Audio not committed (*.webm is gitignored).

## Environment
macOS 26.4 (Darwin 25.4.0) arm64, Apple M4 Pro, 12 cores; Python 3.12.6; faster-whisper 1.0.3, ctranslate2 4.4.0, av 12.3.0, numpy 1.26.4.
Decode settings (repo defaults, unchanged): compute_type int8, cpu_threads 0 (library default), beam 1, temperature 0.0, VAD on, language en, condition_on_previous_text off, no initial_prompt, word_timestamps off.

## Latency (raw: bench.json)
| Metric | Value |
| --- | --- |
| Model load, first ever (cold page cache) | 21.47 s |
| Model load, OS-cache warm (3 fresh processes, incl. 0.9–1.5 s import) | ~0.9–1.5 s |
| First decode after load (1st file) | 2.11 s wall (3.06 s audio) |
| First pass over 22 speech files | mean 1.06 s, p50 1.04, p95 1.14, RTF 0.351 |
| Warm, 3 passes × 22 speech files (n=66) | mean 1.04 s, p50 1.05, p95 1.24, max 1.58, RTF 0.345 |
| Silence short-circuit | 9–32 ms (no model call) |
| Warm HTTP server `/v1/transcribe` (4.4 s clip) | 1.04–1.07 s (first request 1.22 s); `/v1/ready` ~1 ms |
| One-shot fresh process, import+load+decode (3 runs) | 2.62 / 2.18 / 2.01 s (decode part 1.10–1.16 s) |
Warm server saves the ~1 s load per job once the OS cache is warm, ~20 s on a truly cold start. Decode time is ~1 s regardless of 2.9–4.4 s clip length (greedy, fixed per-call overhead dominates short clips). Peak memory footprint ~1.04 GB (failure-case run). Decodes were deterministic across all 4 passes.

## Accuracy (raw: eval.json, hypotheses.json; `evaluate.py --hypotheses`)
| Split | WER | name | date | negation |
| --- | --- | --- | --- | --- |
| dev (14) | 6.96% | 8/9 (0.889) | 0.667 | 1.0 |
| heldout (10) | 3.68% | 1.0 | 0.8 | 1.0 |
Real errors: `Cerno`→`Surno` once (ev_names_02), `Cerno`→`CERNO` (case only, scored OK); "diff-aware invalidation"→"diff aware and validation" plus a mid-sentence period (ev_fast_01); "re-reading"→"rereading".
**Date fidelity is understated by the metric**: the decoder writes digits ("twelfth"→"12th", "nine"→"9", "forty"→"40", "six pm"→"6 PM"), and `evaluate.py` does not canonicalise numerals, so these score as misses although the values are correct. No date value was actually wrong. Fix is an evaluator change (spoken-number/ordinal canonicalisation), not made here. Downstream date parsing must accept digit forms.

## Failure behaviour (failures.py, in-process + server)
| Input | Result |
| --- | --- |
| empty | `unsupported_media` "recording is empty" (server: 415) |
| text / random bytes | `unsupported_media` (server 415) |
| header-only webm | `unsupported_media` |
| video-only mp4 | `unsupported_media` "no audio stream" |
| 400 s silence vs 300 s limit | `limit_exceeded` on decoded duration (server 413) |
| 3 s digital silence | OK, text "", `no_speech: true`, no model call |
| 3 s pink noise | OK, text "", `no_speech` false (RMS gate passes; VAD removed it) — empty text either way |
| **truncated webm (half file)** | **accepted**: returns partial transcript "There is a Bloomberg event." No error or truncation flag. |
| no/bad bearer token | 401 |
Truncation finding: a cut-off upload silently yields a partial transcript. Not changed (needs a contract/decision: e.g. compare decoded duration to container header, or have the client send a checksum/length).
Minor: `cold_start: true` is reported on the first decode after `warm()` with `model_load_seconds: 0.0`; "cold" there means first decode, not model load.

## Tests
- `pytest services/speech`: 42 passed, 1 skipped (the integration test skips without a clip).
- Real-decoder integration (`pytest -m integration`, with a synthetic clip temporarily copied to the gitignored `fixtures/audio/hello.webm`, since removed): 2 passed.

## Not run / blockers
- Human or consented recordings; accented/far-field/phone audio; clips >5 s; concurrent load.
- linux/amd64 container build and Fargate timing (no Docker/AWS run); numbers above are Apple-silicon only and do not transfer to Fargate cost.
- Other decode settings (beam 5, threads sweep, glossary prompt) — defaults only.
- Worker entrypoint with S3 source store (needs `metis_state` + storage); "one-shot" here is a fresh process doing import+warm+one transcribe, the same calls the worker makes.
- `small.en` stays provisional: 24 TTS items prove the pipeline works, not production accuracy.

Reproduce: `scripts/gen_synthetic_audio.py` → `scripts/bench_real.py <audio> <out>` → `python -m metis_speech.evaluate --hypotheses <out>/hypotheses.json --json`.
