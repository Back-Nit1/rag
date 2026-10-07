# Prompt: cross-feature E2E and security gate

```yaml
task_id: qa-e2e
owner: quality
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [tests/e2e]
read_first:
  - CLAUDE.md
  - docs/spec/delivery.md
  - docs/spec/architecture.md
dependencies: [wave-1-integrated, backend-jobs]
```

Build a deterministic cross-feature acceptance suite against local adapters and
a controllable clock. You are read-only outside `tests/e2e/`; report product
defects with severity, reproduction, location and owning role instead of fixing
them in another owner's path.

Use an installed testing/browser skill only after reading its `SKILL.md`. Do
not require a cloud account, paid model, live notification service or physical
microphone for the default suite.

Cover at minimum:

- text/audio fixture capture → transcript → extraction → resolution → records;
- tentative event with no invented date;
- one clarification resolving multiple fields;
- offline retry with stable client ID and no duplicate commitments;
- transcript correction superseding old proposal/index/schedule work;
- changed deadline reconciling the old reminder;
- canceled task/event suppressing a stale due delivery;
- ordinary reminder making zero model calls;
- plan invalidation after a task revision and overflow under insufficient
  capacity;
- project resumption retaining failed attempts;
- provider outage and budget exhaustion preserving manual/deterministic work;
- capture deletion, immediate tombstone and resumable purge;
- cross-user source, job and retrieval isolation;
- export retry and owner-scoped result;
- scheduled daily planning idempotency.

Use the existing contract fixtures where possible. Do not commit private
recordings. A scripted synthetic WAV may be added only if deliberately small,
licensed/consented and designated as a fixture.

## Acceptance and validation

- Tests are deterministic, bounded and included by `make test` or a documented
  local E2E target.
- Failures show the state transition and assertion without dumping transcripts,
  tokens or large payloads.
- The suite distinguishes fixture adapters from real ASR/model/cloud gates.
- Return separate lists of passing local evidence, unrun integration/cloud
  gates, and product defects.

Commit only explicit `tests/e2e/` paths and return the standard handoff.
