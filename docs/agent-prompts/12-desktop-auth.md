# Prompt: secure desktop-local API authentication

```yaml
task_id: desktop-auth
owner: backend
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [services/api]
read_first:
  - CLAUDE.md
  - docs/spec/architecture.md
  - docs/superpowers/specs/2026-10-06-metis-desktop-push-to-talk-design.md
  - services/api/src/metis_api/auth.py
  - services/api/src/metis_api/deps.py
dependencies: [coordinator-baseline, coordinator-desktop-auth-config]
```

Implement the coordinator-approved desktop-local authentication design. The
Tauri shell generates a high-entropy per-launch secret and provides it to the
loopback API process without putting it in command-line arguments, logs,
source control or persisted frontend storage. Requests without the correct
secret must be rejected when desktop-auth mode is enabled. The secret is not
the owner identity: use the coordinator-approved stable per-install subject,
derived server-side, so data remains accessible across launches.

Do not weaken Cognito validation or production safeguards. Do not trust a
client-supplied `user_id`; authentication selects the server-owned local
identity. Ordinary fixture development mode must keep its documented behavior
unless the coordinator's decision explicitly changes it.

If the implementation requires a change in `packages/contracts` configuration
or `.env.example`, stop at the existing boundary and send the coordinator a
narrow field/default/validation proposal. Do not read environment variables
ad hoc in multiple modules and do not edit coordinator-owned paths.

## Security requirements

- Use constant-time secret comparison where applicable.
- Bind only to loopback; reject any configuration that attempts a non-loopback
  desktop API bind.
- Never log the secret or echo it in error responses.
- A random bearer must not fall through to Cognito network verification in
  desktop-local mode.
- Production cannot enable fixture or desktop-local identity accidentally.
- Health/limits exposure follows the coordinator's explicit decision; all
  mutation/read routes remain authenticated.
- Tests cover correct, missing and wrong secrets, mode separation, production
  refusal, malformed authorization and ownership derivation.
- `CaptureDetail` includes owner-scoped jobs for the capture so transcription
  failures are observable. If extraction failure needs a contract addition,
  send a narrow proposal instead of inventing a response field.

## Validation

```bash
uv run pytest services/api/tests
uv run ruff check services/api
uv run ruff format --check services/api
uv run mypy services/api
```

Commit only explicit `services/api/` paths. Return the standard handoff plus
the exact environment/process contract expected from the Tauri supervisor and
any coordinator-owned config changes still required.
