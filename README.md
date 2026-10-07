# Metis

A voice-first multimodal personal assistant. You speak or type a thought; Metis
turns it into sources, extraction proposals, tasks, events, plans and
reminders, and keeps a source reference on every derived fact.

This README describes **what actually runs today**, not the target product. The
full specification is `prompt (1).md`; role-scoped extracts are in
`docs/spec/`. Agent instructions are in `CLAUDE.md` / `AGENTS.md`, and the live
work ledger is `docs/progress.md`.

## Status: local mode works end to end; nothing is deployed

What runs on a laptop with no cloud account and no AI provider key (fixture
inference and fixture transcription are labelled as such in the UI):

| Area | State |
| --- | --- |
| Contracts | v1, generated from Pydantic (93 artifacts), verified by `make contracts-check`. |
| API | FastAPI with bearer auth, `Idempotency-Key` replay, `409` on stale revisions, durable exports, daily-plan enqueueing, desktop-local auth. |
| State / pipeline / scheduler / retrieval / planner | SQLite store with transactional outbox, local worker loop, honest reminder states, scoped search, deterministic plan validation. |
| Web | React/Vite PWA: capture, proposals, Today, plans, projects, search, settings, export, optional Cognito PKCE (only when `VITE_METIS_COGNITO_*` is set), desktop HUD route. |
| Desktop | Tauri v2 shell with a packaged `metis-backend` (api/worker/speech) supervised by Rust, hold-to-talk state machine and uploader, HUD window. Built and tested on aarch64 macOS only. |
| Infra | CDK app synthesizes 7 stacks (`make synth`), template assertions pass. `ops/` has runbook, cost worksheet and procedures. |
| Checks | `make check`, `make test` (python, web, E2E in `tests/e2e`) and `pnpm --filter @metis/desktop test` pass. Exact counts: `docs/progress.md`. |

What is **not** done or **not verified**, stated plainly:

- **No real speech recognition has run.** No model artifact is pinned or fetched;
  `make speech-bench` without `--hypotheses` echoes the reference text. The
  desktop app refuses to record until a model is placed in `<app-data>/models`.
- **No hardware pass.** The global shortcut, microphone capture and the HUD
  window have not been exercised by a person. Steps are in the desktop handoff.
- **Nothing is deployed.** Every cloud check is UNRUN (`ops/runbook.md`).
  DynamoDB parity is tested against a SQLite-backed stand-in, not DynamoDB.
- **Known product defects** are strict `xfail` tests in `tests/e2e/` (spoken
  "already registered"/"cancel that", registration-reminder suppression,
  duplicate registration task, project attachment of captured notes).
- **Desktop packaging** is unsigned, un-notarized, one target triple, ~100 MB.
- **Fixture extraction** is the only extraction in local mode.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node 20+ and pnpm 9 (only needed for `infra/`, and later `apps/web/`)
- No AWS account, no API key, no Docker for the default path

## Local setup

```bash
cp .env.example .env     # defaults are already local-mode
make bootstrap           # uv sync --all-packages + pnpm install
make test                # 266 python tests; the web filter is a no-op for now
make dev                 # API on :8000 + the worker loop
```

`make dev` starts uvicorn and the pipeline worker, and skips the web client
until `apps/web/package.json` exists. Check it is alive:

```bash
curl -s localhost:8000/v1/health -H 'Authorization: Bearer demo' | python3 -m json.tool
```

The response names every adapter's mode (`local`, `fixture`, `cloud`), which is
the point: the client can tell what is real. In local mode you should see
`"inference": "fixture"` and `"transcriber": "fixture"`.

Local state lives in `.metis/` (SQLite plus an object directory) and is
gitignored. Delete the directory to reset.

### Fixture identity

Local mode accepts any bearer token and resolves it to
`METIS_FIXTURE_USER_ID`. This is a development convenience and is refused when
`METIS_ENV=prod`, which now fails at startup if `METIS_ENV` is not one of
`dev`, `staging`, `prod`.

## Commands

```bash
make bootstrap     # install python + node dependencies
make contracts     # regenerate schemas + metis.ts from the Pydantic models
make contracts-check  # fail if the committed artifacts are stale
make dev           # API + worker (+ web when present)
make api / worker  # just one of them
make test          # python unit tests + web unit tests
make check         # contracts-check, ruff, ruff format, mypy, tsc
make fmt           # autoformat
make synth         # cdk synth (no AWS calls, no deploy)
make speech-bench  # WER + name/date/negation fidelity on the fixture set
make clean         # remove caches and local build output
```

## Layout

```
apps/web/            React + TS + Vite PWA
apps/desktop/        Tauri v2 shell, supervisor, hold-to-talk, packaged backend
packages/contracts/  JSON Schema, generated TS, Pydantic, fixtures
services/api/        FastAPI app + Lambda adapter
services/state/      StateStore: SQLite (local) + DynamoDB (cloud)
services/pipeline/   jobs, outbox dispatcher, extraction workers
services/scheduler/  schedule sync + reminder dispatch
services/speech/     owned faster-whisper ASR worker + Dockerfile
services/retrieval/  memory ingestion, embeddings, scoped search
services/planner/    context assembly, plan generation, validation
infra/               AWS CDK stacks
ops/                 runbooks, cost worksheet, procedures
tests/e2e/           cross-feature scenarios (fixture adapters)
docs/                spec extracts, ADRs, progress ledger
```

## Design rules this codebase actually enforces

These are checked by code and tests, not just documented:

1. **Ownership comes from the server.** `user_id` is the authenticated subject.
   A request body or queue payload never decides ownership, and a key outside
   the owner's object prefix is rejected.
2. **Honest states.** "Reminder scheduled" requires a confirmed scheduler sync;
   otherwise the state is `pending_schedule` or `schedule_failed`. Uploaded is
   not transcribed. A fixture result is labelled a fixture.
3. **Nothing is invented.** An unknown date, time or registration status stays
   `null`. Every derived fact carries `source_id`, `source_version` and a span
   reference. Estimates are labelled as estimates.
4. **Optimistic concurrency.** Every mutation carries an expected `revision`;
   a conflict is a `409` with the current revision attached.
5. **Transactional outbox.** An entity change and its side-effect request commit
   together; a separate dispatcher performs the scheduler, index and delivery
   sync.
6. **Budgets before calls.** Estimated cost is reserved atomically before a paid
   model call and settled after. Deterministic reminders make no model calls.
7. **No credentials in the browser.** No AWS keys, no Bedrock calls and no model
   ids in `apps/web/`.

## Contributing

`packages/contracts/`, the root manifests and lockfiles, `docs/architecture/`,
`docs/progress.md` and `.claude/agents/` are coordinator-owned. Propose changes
to them in a handoff rather than editing them directly. Everything else has a
named owner in the table in `docs/spec/development.md`. Stage explicit paths —
`git add -A` is forbidden.
