# Prompt: durable export API

```yaml
task_id: export-api
owner: backend
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [services/api]
read_first:
  - CLAUDE.md
  - docs/spec/architecture.md
  - docs/spec/delivery.md
  - services/api/src/metis_api/routes/notifications.py
dependencies: [coordinator-export-contract]
```

Turn `POST /v1/exports` into a durable, idempotent request and add the
coordinator-approved owner-checked status/download boundary.

- Require `Idempotency-Key` and replay the original response on retry.
- Persist the job plus typed export-request outbox event in one transaction.
- Remove timestamp-derived idempotency.
- Never accept `user_id` or an object key from the browser.
- A queued/running/failed export is never described as downloadable.
- Result access re-reads authoritative ownership and returns only a short-lived
  authorized reference or controlled response after durable completion.
- Tests cover replay, missing key, not-ready, failed, deleted and foreign-owner
  access.

Do not edit contracts; stop and propose an exact schema/port change if the
published design is insufficient.

```bash
uv run pytest services/api/tests
uv run ruff check services/api
uv run ruff format --check services/api
uv run mypy services/api
```

Commit only explicit `services/api/` paths and return the standard handoff plus
the exact event/result contract consumed by the pipeline worker.
