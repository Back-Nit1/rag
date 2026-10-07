# Prompt: resumable export worker

```yaml
task_id: export-worker
owner: pipeline
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [services/pipeline]
read_first:
  - CLAUDE.md
  - docs/spec/architecture.md
  - docs/spec/delivery.md
dependencies: [export-api, coordinator-export-contract, state-listing-ports]
```

Implement the typed export-request handler. Re-read the authoritative job and
owner-scoped records; do not trust event payload ownership or paths.

- Honor approved inclusion selections for sources, memories and commitments.
- Produce a deterministic manifest/archive with schema and build metadata.
- Write through `SourceStore` under a server-selected owner prefix.
- Mark the job ready only after bytes are durably stored and verified.
- Retry idempotently without conflicting artifacts; make terminal failures
  actionable and resumable where appropriate.
- Bound pages, total bytes and work duration. Large bytes never enter events or
  state records.
- Do not log exported content or create public objects.
- Test exclusions, pagination, duplicate delivery, partial write/failure,
  deletion races and cross-tenant isolation.

```bash
uv run pytest services/pipeline/tests
uv run ruff check services/pipeline
uv run ruff format --check services/pipeline
uv run mypy services/pipeline
```

Commit only explicit `services/pipeline/` paths and return the standard
handoff.
