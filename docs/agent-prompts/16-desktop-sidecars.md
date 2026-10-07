# Prompt: package and supervise the real local stack

```yaml
task_id: desktop-sidecars
owner: desktop
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [apps/desktop]
read_first:
  - CLAUDE.md
  - docs/superpowers/specs/2026-10-06-metis-desktop-push-to-talk-design.md
dependencies:
  - desktop-foundation
  - desktop-auth
  - speech-warm-server
  - desktop-runtime-bridge
```

Replace the feasibility probe with a repeatable packaged Metis local runtime.
The installed app must not depend on system Python or uv. Do not commit built
binaries, Python environments, model weights, SQLite files, recordings or
secrets.

Prototype and justify the packaging strategy. Prefer one packaged Metis
executable with narrow `api`, `worker` and `speech` subcommands if it reduces
duplication while preserving independent lifecycle/health; otherwise explain
why distinct binaries are safer. Every `externalBin` entry must be a genuine
target-triple executable.

## Supervisor requirements

- Generate the launch secret with an OS CSPRNG and pass it only through child
  environment plus the in-memory runtime bridge.
- Generate/load the stable per-install subject and data paths from protected
  application data; never derive ownership from the webview.
- Use explicit absolute paths for SQLite, objects, logs and model artifacts.
- Speech and API bind loopback on OS-assigned ports. The worker receives the
  real speech endpoint only after readiness; it does not bind an HTTP port.
- Read structured, versioned readiness records. Never scrape arbitrary human
  log lines for ports.
- Start services in dependency order and expose sanitized aggregate health.
- Implement startup deadlines, capped exponential backoff with jitter, restart
  budgets, graceful termination and a terminal unhealthy/circuit-open state.
- Distinguish intentional shutdown from crashes. Refuse recording while a
  required service is unhealthy.
- Expose no generic process spawn, filesystem or arbitrary-argument command to
  the webview.

## Acceptance and validation

- A clean machine with the packaged artifacts can start API, worker and warm
  speech service without developer tooling.
- Health proves the actual children and model readiness, not just adapters
  constructed inside the API process.
- Launch secret never appears in stdout, logs, errors, URLs or diagnostics.
- Startup failure, malformed readiness, child crash, restart exhaustion and
  app shutdown are covered by deterministic tests.

Run Rust formatting/clippy/tests, package tests, and a launch → authenticated
health → shutdown smoke test. Do not sign, notarize or publish. Commit only
explicit `apps/desktop/` paths and return the standard handoff plus artifact
layout and remaining packaging risks.
