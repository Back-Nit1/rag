# Metis

A voice-first multimodal personal assistant. You speak or type a thought; Metis
turns it into sources, extraction proposals, tasks, events, plans and
reminders, and keeps a source reference on every derived fact.

This README describes **what actually runs today**, not the target product. The
full specification is `prompt (1).md`; role-scoped extracts are in
`docs/spec/`. Agent instructions are in `CLAUDE.md` / `AGENTS.md`, and the live
work ledger is `docs/progress.md`.

## Status: foundations, local mode only

What works end to end, right now, on a laptop with no cloud account and no AI
provider key:

| Area | State |
| --- | --- |
| Contracts | v1 frozen. 89 JSON Schemas + a bundle + `metis.ts` (91 artifacts), all generated from Pydantic and verified by `make contracts-check`. |
| API | FastAPI, 33 paths / 43 operations, bearer auth, `Idempotency-Key` replay, optimistic concurrency with `409`, rate limiting. |
| State | SQLite store with transactional outbox, revision log, tombstones, budget reservations. |
| Pipeline | Outbox dispatcher with leases, retry classification, dead-lettering and freshness suppression; a local worker loop. |
| Scheduler | Reminder lifecycle with `pending_schedule` / `schedule_failed`, suppression re-checked at dispatch, in-app delivery. |
| Retrieval | Deterministic fixture embedder, local memory store, scoped search, authoritative-state hydration of every hit. |
| Planner | Snapshot assembly with planning-epoch checks, a greedy packer and a deterministic validator. |
| Checks | `make check` (contracts, ruff, ruff format, mypy --strict, tsc) and `make test` (266 tests) both pass. |

What is **not** built yet, stated plainly so nobody mistakes a fixture for a
feature:

- **No web app.** `apps/web/` contains only the generated `metis.ts`. There is
  no `package.json`, no UI, no capture button.
- **No deployable infrastructure.** `infra/lib/` has CDK stacks for network,
  data, auth and API, but `infra/bin/metis.ts` — the CDK app entry point — does
  not exist, so `make synth` cannot run. Only the HTTP Lambda is wired; the
  outbox, reminder-due and daily-planning handlers exist in Python with no
  function defined for them.
- **No real speech recognition has ever run here.** The local transcriber is a
  fixture. `make speech-bench` without `--hypotheses` echoes the reference text
  and says so in its own output; it measures the metric code, not a decoder.
  The real ASR path needs the `asr` extra and a model artifact.
- **The cloud path is unexercised.** DynamoDB, S3, Bedrock and EventBridge
  Scheduler adapters are written and type-checked against `boto3-stubs`, but
  `boto3` is not even installed locally and no cloud test has been run. Tests
  marked `cloud` are deselected by default.
- **No end-to-end tests.** `tests/e2e/` is empty.
- **No runbooks or cost worksheet.** `ops/` is empty.

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
make synth         # cdk synth — blocked until infra/bin/metis.ts exists
make speech-bench  # WER + name/date/negation fidelity on the fixture set
make clean         # remove caches and local build output
```

## Layout

```
apps/web/            React + TS + Vite PWA  (contracts only, so far)
packages/contracts/  JSON Schema, generated TS, Pydantic, fixtures
services/api/        FastAPI app + Lambda adapter
services/state/      StateStore: SQLite (local) + DynamoDB (cloud)
services/pipeline/   jobs, outbox dispatcher, extraction workers
services/scheduler/  schedule sync + reminder dispatch
services/speech/     owned faster-whisper ASR worker + Dockerfile
services/retrieval/  memory ingestion, embeddings, scoped search
services/planner/    context assembly, plan generation, validation
infra/               AWS CDK stacks  (no app entry point yet)
ops/                 runbooks, cost worksheet  (empty)
tests/e2e/           cross-feature scenarios  (empty)
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
