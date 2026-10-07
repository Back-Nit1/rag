# Prompt: coordinator and integrator

You are the sole coordinator/integrator for the Metis repository. Work from
the repository root.

Read `AGENTS.md` and all of `CLAUDE.md` first. Then read
`docs/spec/development.md`, `docs/spec/delivery.md`, `docs/progress.md`, and
`docs/superpowers/specs/2026-10-06-metis-desktop-push-to-talk-design.md`.
Inspect the current Git state before changing anything. Preserve the user's
untracked `session2.md`; do not stage, edit, delete, or move it.

## Goal

Prepare and coordinate the next implementation waves: UI overhaul, Tauri
desktop foundation, secure desktop-local authentication, warm local ASR,
native hold-to-talk capture, remaining product surfaces, backend jobs, E2E
coverage, and cloud/operations readiness.

## Coordinator-only responsibilities

- Reconcile `docs/progress.md` and `README.md` with the actual current tree.
- Run the baseline gates and publish a new verified `BASE_SHA` that includes
  the existing web app. Do not dispatch workers from the old frozen SHA if it
  lacks their required code.
- Keep `packages/contracts/`, root manifests/lockfiles, `docs/`,
  `.claude/agents/`, and integration history coordinator-owned.
- Create one branch/worktree per writing worker from the same published base.
- Assign exactly one writer per path and integrate one handoff at a time.
- Handle root lockfile changes requested by workers after reviewing their
  package-manifest changes.
- Review and implement contract/config changes before asking workers to depend
  on them. Never allow a worker to fork a local schema.
- Update the ledger after every integration with SHAs and actual gate results.
- Add the coordinator-owned CI workflow after the relevant commands exist:
  contract freshness, Python/web tests, lint/type checks, web build, CDK synth
  and non-microphone desktop/E2E gates. Real model/cloud jobs stay opt-in so an
  ordinary pull request cannot create spend.

## Skills preflight

Inventory installed skills and read every selected `SKILL.md` completely.
For the UI work, prefer the official `frontend-design`,
`web-design-guidelines`, `vercel-react-best-practices`, and `webapp-testing`
skills. If they are not installed, present the install commands from
`docs/agent-prompts/README.md`, install only with user authorization, and
record the result. Do not substitute the Sites skill: this is an existing
React application, not a Sites project. Do not claim an unavailable skill was
used.

## Baseline verification

Run and record:

```bash
make contracts-check
make check
make test
pnpm --filter @metis/web build
make synth
make speech-bench
git status --short
```

Use a workspace-writable uv cache if the execution sandbox blocks the default
cache. Treat expected failures honestly: today `make synth` is expected to fail
without `infra/bin/metis.ts`, and `make speech-bench` without hypotheses is not
a real decoder measurement.

## Desktop decisions to settle before implementation

Record narrow decisions for:

1. Python sidecar packaging and target-triple artifacts. Prototype before HUD
   polish.
2. Desktop-local authentication. The current local API accepts a missing token
   as fixture identity and treats a supplied bearer as Cognito; the proposed
   per-launch secret therefore needs an explicit implementation. Treat the
   launch secret as authentication only; use a stable server-derived
   per-install subject for ownership across launches.
3. UI reuse: use the existing `apps/web` production build as the main Tauri
   frontend. Decide whether the HUD is a dedicated route/window in that build
   or a tiny separate desktop frontend. Do not duplicate the full UI.
4. Loopback networking, ephemeral ports, Tauri production origin, CORS and CSP.
5. Model-artifact location, pinned revision/SHA-256, first-run behavior and
   cleanup. Never commit model weights.
6. Desktop identity and SQLite/object-store location per installation.
7. Runtime webview connection data. The API port and launch secret must arrive
   in memory at runtime, not through Vite build variables, URLs or persistent
   browser storage.
8. Real local speech selection independent from inference mode, so SQLite and
   fixture extraction can coexist with a real warm ASR service.
9. Microphone privacy: begin capture on key-down and retain the Armed interval;
   do not record before the keypress or implement an always-on pre-roll.
10. Durable capture status. Populate capture jobs and decide how extraction
    failures become user-visible before promising that every HUD error can name
    the failing step.
11. Proposal semantics: Review/Clarification, not Saved, is the normal terminal
    state until the user explicitly resolves a proposal.

If a decision changes a contract, configuration schema, or root manifest,
make that coordinator-owned change first, regenerate artifacts, run targeted
checks, and publish the new base before dispatching the dependent worker.

## Dispatch policy

Use the prompts in `docs/agent-prompts/` in the order described by the README.
Start at most four writing contexts including yourself. Do not run two prompts
that own the same path concurrently. Require approximate three-minute
checkpoint commits only at safe boundaries; explicit paths only.

## Final integration gate

After each wave, inspect every diff and run targeted tests. At milestone
boundaries run `make check`, `make test`, web build, Cargo tests for the desktop
package, a scripted sidecar E2E test, and `make synth` once infrastructure is
implemented. Do not deploy or create paid resources without a separate,
explicit user instruction.

Return a short factual report with integrated SHAs, passing/unrun gates,
remaining blockers, and the next dispatchable prompt IDs.
