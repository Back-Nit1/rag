# Prompt: idempotent scheduled daily planning

```yaml
task_id: daily-planning-api
owner: backend
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [services/api]
read_first:
  - CLAUDE.md
  - docs/spec/architecture.md
  - services/api/src/metis_api/routes/plans.py
  - services/api/src/metis_api/lambda_entry.py
dependencies: [daily-scheduler, coordinator-daily-schedule-contract]
```

Replace the `enqueued: 0` stub with owner-scoped, idempotent plan enqueueing.

- Extract shared application logic used by `POST /v1/plans` and the scheduled
  handler; do not call the HTTP route internally.
- On relevant preference changes, append schedule-sync work in the same
  transaction as the preference update.
- Re-read current preferences, validate the occurrence and derive the correct
  local date and planning epoch server-side.
- Use one stable idempotency key per user/local-date occurrence.
- Suppress stale invocations after timezone/time/disable changes.
- Reuse the ordinary `plan.requested` worker path, budget gate and freshness
  checks. The scheduled Lambda does not generate a plan directly.
- Tests cover duplicate invocation, DST, stale schedule, missing availability,
  budget exhaustion and provider failure.

```bash
uv run pytest services/api/tests
uv run ruff check services/api
uv run ruff format --check services/api
uv run mypy services/api
```

Commit only explicit `services/api/` paths and return the standard handoff.
