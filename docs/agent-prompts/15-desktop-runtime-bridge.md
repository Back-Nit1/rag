# Prompt: runtime desktop connection bridge

```yaml
task_id: desktop-runtime-bridge
owner: frontend
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [apps/web]
read_first:
  - CLAUDE.md
  - docs/spec/interface.md
  - apps/web/src/lib/api.ts
  - apps/web/src/lib/captureFlow.ts
  - apps/web/src/app/session.tsx
dependencies: [ui-overhaul, desktop-foundation, desktop-auth-protocol]
```

Make the redesigned web client consume ephemeral desktop connection data while
preserving ordinary browser/PWA behavior. Do not hard-code or persist the API
port or launch secret.

Use the current supported Tauri API and the narrow Rust command exposed by the
desktop foundation. If a new `@tauri-apps` dependency is required, edit only
`apps/web/package.json` and request the coordinator-owned lockfile update.

## Requirements

- Browser/PWA mode continues to use `VITE_METIS_API` and its documented local
  fixture/cloud authentication behavior.
- Desktop mode waits for in-memory runtime bootstrap containing the loopback
  API URL and launch bearer before issuing requests.
- The bearer stays module-private and never enters local/session storage, URLs,
  rendered diagnostics, error strings or logs.
- The local upload PUT receives the same desktop authentication and correlation
  behavior as API calls. Never attach the desktop bearer to a cloud presigned
  external URL.
- Missing/malformed bootstrap produces a clear unavailable state, not requests
  to a fallback server.
- Sign-out/profile reset clears configured retained client data without
  deleting authoritative data unexpectedly.

## Tests and validation

Test browser mode, desktop bootstrap, authenticated loopback upload, external
presigned upload without the bearer, missing bootstrap, malformed connection
data and secret redaction.

```bash
pnpm --filter @metis/web test
pnpm --filter @metis/web typecheck
pnpm --filter @metis/web build
```

Commit only explicit `apps/web/` paths and return the standard handoff plus the
exact Rust command/response expected from the desktop owner.
