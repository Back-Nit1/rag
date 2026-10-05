# Metis — agent instructions

Voice-first multimodal personal assistant. Converts spoken/typed thoughts into
sources, extraction proposals, tasks, events, plans and reminders.

Canonical spec: `prompt (1).md`. Role-scoped extracts: `docs/spec/*.md`
(regenerate with `python scripts/extract_spec.py`). **Read only the block(s)
your task needs** — do not load the whole spec into every turn.

| Block | Read it when working on |
| --- | --- |
| `docs/spec/product.md` | behaviour, scope, workflows |
| `docs/spec/architecture.md` | state, API, events, jobs, reliability |
| `docs/spec/speech.md` | ASR worker, capture/upload |
| `docs/spec/retrieval.md` | memory, embeddings, evidence |
| `docs/spec/interface.md` | screens, interaction states |
| `docs/spec/cost.md` | budgets, token/cost controls |
| `docs/spec/development.md` | parallel agents, git cadence |
| `docs/spec/delivery.md` | phases, gates, completion |

## Non-negotiable rules

1. **Contracts first.** `packages/contracts/` is the single source of truth for
   schemas, events and errors. Only the coordinator edits it. Workers propose
   changes via handoff; never fork a second schema.
2. **Ownership comes from the server.** `user_id` is derived from the
   authenticated subject. Never trust a request body or queue payload for
   ownership. Reject keys outside the owner's S3 prefix.
3. **Honest states.** "Reminder scheduled" requires a confirmed scheduler sync.
   Use `pending_schedule` / `schedule_failed` otherwise. Uploaded ≠ transcribed.
   A fixture/mock result must never be presented as production output.
4. **Nothing is invented.** Unknown date/time/registration stays `null`. Every
   derived fact carries `source_id` + `source_version` + `span_ref`. Estimates
   are labelled as estimates.
5. **Optimistic concurrency.** Every mutation carries an expected `revision`;
   conflicts return `409`. Mutations that create work carry `Idempotency-Key`.
6. **Transactional outbox.** Entity change + side-effect request commit in one
   transaction; a separate dispatcher performs scheduler/index/delivery sync.
7. **Budgets before calls.** Reserve estimated cost atomically before a paid
   model call, settle after. Deterministic reminders make zero model calls.
8. **No credentials in the browser.** No AWS keys, no Bedrock calls, no model
   IDs with secrets in `apps/web/`.
9. **Local mode works with no cloud account.** `METIS_MODE=local` uses SQLite,
   the fixture transcriber and the fixture inference client.
10. **Path ownership.** Stay inside your assigned paths (see
    `docs/spec/development.md` table and `.claude/agents/`). Never `git add -A`.

## Layout

```
apps/web/            React + TS + Vite PWA
packages/contracts/  JSON Schema, generated TS, Pydantic, fixtures
services/api/        FastAPI app + Lambda adapter
services/state/      StateStore: SQLite (local) + DynamoDB (cloud)
services/pipeline/   jobs, outbox dispatcher, extraction workers
services/scheduler/  schedule sync + reminder dispatch
services/speech/     owned faster-whisper ASR worker + Dockerfile
services/retrieval/  memory ingestion, embeddings, scoped search
services/planner/    context assembly, plan generation, validation
infra/               AWS CDK stacks
ops/                 runbooks, cost worksheet, env procedures
tests/e2e/           cross-feature scenarios with a controllable clock
docs/                spec extracts, ADRs, progress ledger
```

## Commands

```bash
make bootstrap     # install python + node deps
make contracts     # regenerate TS types + Pydantic models from JSON Schema
make dev           # local API + web + local worker loop (fixture adapters)
make test          # python + web unit tests
make check         # lint, format check, typecheck
make synth         # cdk synth (no deployment)
```

## Git cadence

Checkpoint commits roughly every 2 minutes of active work, at a safe boundary.
Explicit paths only. Never commit `.env`, credentials, model weights, bundles or
private recordings. Label incomplete work `checkpoint:` in the message body.
