# Prompt: daily-planning schedule synchronization

```yaml
task_id: daily-scheduler
owner: pipeline-and-orchestration
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [services/scheduler]
read_first:
  - CLAUDE.md
  - docs/spec/architecture.md
dependencies: [coordinator-daily-schedule-contract]
```

Synchronize one timezone-aware recurring daily-plan schedule per user using the
coordinator-approved event and `ScheduleStore` boundary.

- Create/update/delete idempotently from current preferences.
- Use the user's IANA timezone in provider configuration so DST is not modeled
  as fixed UTC arithmetic.
- Use stable provider names and narrow internal payloads.
- Retrying cannot create duplicate schedules.
- Provider failure remains a visible pending/failed synchronization state.
- Provide a truthful deterministic local adapter/test path.
- Tests cover time, timezone, disable/re-enable, DST boundaries, stale provider
  invocation and provider failure.

```bash
uv run pytest services/scheduler/tests
uv run ruff check services/scheduler
uv run ruff format --check services/scheduler
uv run mypy services/scheduler
```

Commit only explicit `services/scheduler/` paths and return the standard
handoff plus the invocation payload expected by the API handler.
