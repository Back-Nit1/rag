# Prompt: remove API access to private state paging

```yaml
task_id: state-port-cleanup
owner: backend-and-data
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [services/api, services/state]
read_first:
  - CLAUDE.md
  - docs/spec/architecture.md
  - services/api/src/metis_api/services.py
dependencies: [coordinator-state-port-contract]
```

Implement the coordinator-published public `StateStore` reads needed for
stable client-capture lookup, proposal/source listing and plan lookup. Remove
API access to private `_page` implementation methods.

- Implement every new read in both SQLite and DynamoDB stores.
- Preserve bounded reads, stable cursors, tenant isolation and parity.
- Do not hide missing ports behind `Any`, `getattr` or another private helper.
- Add contract-style parity tests plus capture-idempotency and stale/latest-plan
  regressions.
- Acceptance: `rg` finds no `_page` access outside state-store implementations.

```bash
uv run pytest services/state/tests services/api/tests
uv run ruff check services/state services/api
uv run ruff format --check services/state services/api
uv run mypy services/state services/api
```

Do not edit `packages/contracts`; the coordinator must publish the port first.
Commit explicit `services/api/` and `services/state/` paths only and return the
standard handoff.
