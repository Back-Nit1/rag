# Prompt: Tauri foundation and sidecar feasibility

```yaml
task_id: desktop-foundation
owner: desktop
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [apps/desktop]
read_first:
  - CLAUDE.md
  - docs/superpowers/specs/2026-10-06-metis-desktop-push-to-talk-design.md
  - docs/spec/architecture.md
  - apps/web/package.json
dependencies: [coordinator-baseline, desktop-decisions]
```

Build the smallest credible Tauri v2 foundation for Metis. This task is a
packaging and supervision feasibility spike, not the full hold-to-talk feature.
Do not edit `apps/web`, Python services, contracts, root manifests or lockfiles.
Send narrow requests to their owners.

Before editing, inspect the installed Tauri/Rust-related skills and read any
that actually apply. Verify current official Tauri v2 documentation for the
CLI, capabilities and sidecar behavior. Do not cargo/npm-install an unpinned
major version based on memory.

## Deliverable

- A new `apps/desktop/` package containing Tauri v2 configuration and Rust
  source.
- Development mode loads the existing `apps/web` Vite dev server.
- Production configuration embeds the existing `apps/web/dist`; do not copy or
  fork the React application.
- A main window opens, reports its build/mode clearly, and can reach a minimal
  Rust health command.
- A packaged dummy or health sidecar proves `externalBin` naming for the host
  target triple, spawn, stdout parsing, clean shutdown and unexpected-exit
  reporting.
- Capabilities grant only the commands/plugins actually needed. Establish a
  restrictive CSP with an explicit loopback `connect-src` plan.
- macOS has an `NSMicrophoneUsageDescription`, even though real capture lands
  in the later task.
- No fixed production port, LAN bind, arbitrary shell execution, checked-in
  Python environment, model weight or private data.

Prototype the packaging boundary first. If Python packaging cannot yet be
completed, a purpose-built trivial sidecar may prove Tauri mechanics, but the
handoff must state exactly what remains to package the Metis API/worker/speech
processes. Do not call a shell-script wrapper a production sidecar.

The design document currently overstates two points: the pipeline worker does
not bind an HTTP port, and Tauri sidecars must be packaged executables rather
than raw `uvicorn`/`python -m` commands. Reflect those facts in the code and
handoff; do not edit the design document yourself.

## Acceptance

- `tauri dev` opens the existing Metis UI on the host machine.
- The Rust health command is callable only from the intended window/capability.
- Sidecar lifecycle tests cover clean exit, crash, bounded restart backoff and
  parent shutdown.
- Production asset paths resolve from a clean web build.
- The package contains no embedded credentials and does not expose a LAN
  listener.
- Missing sidecar artifacts produce a clear startup error rather than a blank
  window.

## Validation

Run the package's formatting, clippy, unit tests and build checks, plus:

```bash
pnpm --filter @metis/web build
pnpm --filter @metis/desktop typecheck
pnpm --filter @metis/desktop test
pnpm --filter @metis/desktop tauri build --debug
```

Use the actual scripts implemented by the package if their names differ and
report them exactly. Do not sign, notarize or publish. Commit only explicit
paths under `apps/desktop/`. Return the standard handoff and a concrete
sidecar-packaging recommendation for the next milestone.
