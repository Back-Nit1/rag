# What's next (ASAP)

State at `main` (see `docs/progress.md`): local mode and the desktop shell are
built and green on fixtures. Nothing real has run yet: no speech model, no
microphone, no cloud. The next work is to make the app **real**, in this order.

## 1. Make it work with a real voice (blocks everything else)
1. **Approve and run a pinned model fetch** (prompt 14): choose `small.en`
   revision + SHA-256, put it in `<app-data>/models`, run
   `make speech-bench --hypotheses` and record real WER, name/date/negation
   fidelity, cold and warm latency. Replace every "fixture" number with a
   measurement.
2. **Hardware pass on a Mac** (steps are in the `desktop/20` and `desktop/20c`
   handoffs): `pnpm --filter @metis/desktop tauri dev`, mic permission,
   `Cmd+Option+Space` hold/tap/double-tap/cancel, HUD show/hide without stealing
   focus, `hud:open-review`. Fix whatever breaks. Watch for the shortcut
   colliding with Finder search.
3. **Real extraction locally.** Local mode only has fixture extraction, so the
   assistant can't understand a real sentence yet. Add a local `InferenceClient`
   (llama.cpp or Ollama) behind the existing port, plus local embeddings.

## 2. Close product defects (strict `xfail` in `tests/e2e/`)
Need a contract decision first:
- **D7** spoken "I already registered" must update the existing event
  (target reference on a proposal item + an update action).
- **D8** "cancel that" needs an extraction kind and target resolution.
- **D10** link a registration task to its event, or add a reminder purpose, so
  registered events stop nagging without silencing event-time reminders.
Smaller: purge transcripts on capture delete; D9 needs real extraction to emit
`project_hint`; the daily-plan `claim_once` retry gap.

## 3. Ship the desktop app
- First-run model fetch UX (pinned URL + SHA-256, never silent).
- Signing, notarization, `tauri build`, auto-update; build other target triples.
- Real icons. Decide onedir vs onefile (cold start ~5 s, ~100 MB).
- Give `local_worker` its own structured readiness line (removes a monkey-patch).

## 4. Cloud (only when you want it; needs explicit approval + account/region/spend)
- Decide the interface-endpoint cost (≈90% of the ≈$25/mo estimate, above the
  default budget of 25): raise budget to ~40 or drop endpoints.
- Run the cloud smoke test (prompt 31). Every AWS check is still UNRUN.
- Real DynamoDB parity tests, real EventBridge DST behaviour, S3 presign for
  expiring export URLs, backend metrics for stuck outbox / late reminders.

## 5. Housekeeping
- Remove the sibling worktrees `../metis-*` and merged `worktree` branches.
- Decide whether to gate `/v1/health` and `/v1/limits` (currently open).
- Add a `make desktop-test` target and desktop job in CI (macOS runner).
- Missing API: reminder reschedule, project PATCH, attach capture to project.
