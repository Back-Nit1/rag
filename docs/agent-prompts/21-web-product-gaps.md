# Prompt: complete the missing web product surfaces

```yaml
task_id: web-product-completion
owner: frontend
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [apps/web]
read_first:
  - CLAUDE.md
  - docs/spec/interface.md
  - docs/spec/product.md
  - docs/spec/retrieval.md
  - apps/web/HANDOFF.md
dependencies: [ui-overhaul, backend-jobs]
```

Complete the important product interactions missing from the redesigned PWA
without diluting its visual system. Read and apply the installed frontend,
React, web-guideline and webapp-testing skills used by the UI overhaul.

Implement against existing contract v1 and real API routes:

- Projects list/detail, work packet/resume, source-backed timeline and update
  flow.
- Search with evidence links and an honest no-relevant-memory state.
- Plan request, processing, proposed-plan review, accept, adjust and regenerate
  flow; stale-plan conflicts remain explicit.
- Manual availability/fixed-block entry and editing.
- Editable quiet hours, work hours, focus limit, channels and relevant cost
  preferences.
- Push subscription registration when the server exposes required public
  configuration; otherwise show a precise unsupported/unconfigured state.
- Cognito authorization-code/PKCE sign-in, callback, refresh and sign-out for
  cloud mode using coordinator-approved public configuration. Local desktop
  and fixture modes must not be forced through cloud login, and no client
  secret belongs in the bundle.
- Export job initiation/status and capture/source deletion confirmation with
  pending-purge state.
- Reminder cancel/snooze/reschedule controls where supported.

Do not invent APIs or create client-side fake success. If an API capability is
missing, stop that narrow flow at an honest state and send a concrete request
to the owning backend agent. Do not edit generated contracts manually.

## Acceptance

- All new flows preserve optimistic concurrency and idempotency.
- Supporting evidence opens the relevant source/span when the API supplies it.
- A plan cannot be presented as accepted or current after its input epoch is
  stale.
- Export/purge/push states reflect server evidence, not browser optimism.
- Cloud authentication refuses failed/expired sessions without silently
  falling back to fixture identity; sign-out clears configured local user data.
- Full keyboard use, responsive layouts, reduced motion and visible focus
  remain intact.
- New async states, errors and empty states match the overhaul's design system.
- Add focused logic/component tests and a real-browser happy/error-path pass.

## Validation

```bash
pnpm --filter @metis/web test
pnpm --filter @metis/web typecheck
pnpm --filter @metis/web build
```

Commit only explicit `apps/web/` paths. Return the standard handoff, screenshots
of the major new surfaces, and a route-by-route list of any backend gaps.
