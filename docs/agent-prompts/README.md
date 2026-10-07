# Metis continuation prompt pack

These prompts are copy/paste task briefs for continuing Metis with multiple
coding agents. They are intentionally sequenced: do not start every prompt at
once.

## Before dispatching agents

Run `00-coordinator.md` first. The published `BASE_SHA` in `docs/progress.md`
predates the current web application, so new work must use a freshly verified
integration base chosen by the coordinator. Every worker prompt contains a
`{{BASE_SHA}}` placeholder; replace it with that exact SHA.

The coordinator is the only writer of `packages/contracts/`, root manifests
and lockfiles, `docs/`, `.claude/agents/`, and the progress ledger. Workers
stage explicit owned paths only. `git add .` and `git add -A` remain forbidden.

## Recommended skills

The UI worker should use these skills after the coordinator verifies and reads
their actual `SKILL.md` files:

```bash
npx skills add https://github.com/anthropics/skills --skill frontend-design
npx skills add https://github.com/vercel-labs/agent-skills --skill web-design-guidelines
npx skills add https://github.com/vercel-labs/agent-skills --skill vercel-react-best-practices
npx skills add https://github.com/anthropics/skills --skill webapp-testing
```

Install skills once from the coordinator session, not independently in every
worktree. Review what the installer will change before accepting it. If a skill
is unavailable, the worker must say so and continue from the repository's
explicit interface specification; it must not claim the skill was used.

Other workers should use only skills that genuinely match their task and are
installed in their environment. In particular, Cloudflare or Sites skills do
not apply to this Tauri/AWS repository merely because it contains a web UI.

## Dispatch order

### Wave 0 — integration baseline

1. `00-coordinator.md`

The coordinator refreshes the ledger, chooses the new base, prepares worktrees,
and installs/records approved skills.

### Wave 1 — independent foundations

These may run concurrently in separate worktrees because their write paths do
not overlap:

1. `10-ui-overhaul.md` — redesign the existing PWA in `apps/web/`.
2. `11-desktop-foundation.md` — prove the Tauri shell and packaged-sidecar
   boundary in `apps/desktop/`.
3. `12-desktop-auth.md` — implement loopback authentication in
   `services/api/` after the coordinator publishes any required config change.
4. `13-warm-speech.md` — implement a warm local ASR server in
   `services/speech/`.
5. `14-real-asr-evaluation.md` — after explicit approval to fetch a pinned
   model artifact, measure the real decoder instead of fixture echoes.

After integrating the UI, foundation, auth and speech branches, run these two
dependency steps in order:

1. `15-desktop-runtime-bridge.md` — give the webview its ephemeral API URL and
   launch bearer in memory.
2. `16-desktop-sidecars.md` — replace the probe with the packaged API, worker
   and warm-speech runtime.

Integrate and verify those steps before starting Wave 2.

### Wave 2 — product completion

1. Run `20-desktop-capture.md` and the `22*` backend prompts concurrently:
   native
   shortcut/audio/HUD in the desktop path, and real export/daily-planning work
   in backend paths.
2. Integrate native capture, then run `20b-desktop-hud.md` in `apps/web`.
3. Integrate the backend jobs and HUD, then run `21-web-product-gaps.md` for
   projects/resume,
   planning, availability, authentication, preferences, push and export/delete
   UI.
4. Run `23-e2e-quality.md` after the desktop, backend and web work is
   integrated.

The two frontend-writing prompts (`10` and `21`) must not run concurrently.
No two prompts that write `apps/web` may run concurrently. The desktop prompts
`11`, `16` and `20` are also serialized.

### Wave 3 — delivery readiness

1. `30-infra-ops.md` — CDK entrypoint, worker infrastructure, operations and
   synth validation. This prompt does not authorize deployment or paid AWS
   resources.
2. `31-cloud-smoke.md` exists for the final real-AWS gate, but must not be
   dispatched until the user explicitly authorizes the account, region,
   environment and potentially paid resource changes.
3. The coordinator reconciles documentation, runs the full local gate, and
   writes the final implementation report.

## Standard worker return

Every worker returns only concrete evidence:

- branch and worktree;
- verified base SHA and contract version;
- changed paths;
- commit SHAs;
- behavior added;
- commands run and exact outcomes;
- screenshots or measured results where the assignment requires them;
- unrun checks and why;
- unresolved decisions and narrow requests to another owner.

Do not report fixture output as real inference, an unsynthesized stack as
deployable, or a rendered screen as an end-to-end feature.
