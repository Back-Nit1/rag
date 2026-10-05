<!-- Extracted from prompt (1).md; block `development`. Do not edit here; edit the source spec. -->

## Claude Code parallel development and context discipline

This section is an implementation instruction for the coding coordinator building Metis. Development agents are coding workers, not the deployed Metis runtime agents. Keep those two meanings separate in code, architecture documentation, logs, and task names. Parallel coding does not require buying another application API subscription. Runtime credentials remain server-side AWS credentials or local service credentials as specified by the architecture.

### Capability check and startup

Read the root repository instructions and inspect the existing workspace before scaffolding. Preserve user changes. Discover the installed Claude Code version, supported delegation tools, available skills, existing package manager, and git state. Do not claim a feature exists solely because a recent documentation example mentions it. Claude Code's native agent teams are experimental and disabled by default in the documentation checked on 2026-10-05. If agent teams are available and permitted, a lead may coordinate them; otherwise use supported subagents or separate Claude Code sessions in explicit git worktrees. Do not block product implementation on an experimental orchestration feature.

Create versioned project subagent definitions under `.claude/agents/` only for roles that will recur. Keep each description to one or two specific sentences and put the role procedure in the body. Set models according to the user's existing subscription/provider and the complexity of the task; do not silently introduce another API key. Begin with inherited models if provider support is uncertain. Use cheaper supported models for bounded discovery or mechanical review after confirming they can do that task. Keep high capability reasoning for contract design, difficult failures, and integration decisions.

The user requested "code munch ponytail frontend design skills". Inspect the actual installed skills/tools and their local documentation for that exact request. Do not invent an installed skill, silently replace Ponytail with Tailwind, or assume jCodeMunch is a frontend design system. If jCodeMunch is present, it can optionally help retrieve code symbols; verify its actual capabilities before using it. If the design skills cannot be found, record the missing names and continue with the approved UI requirements and existing frontend stack. Use an available frontend design skill only after reading it, and load it only in the frontend worker that needs it.

### One coordinator, named owners

Use one coordinator as the sole writer of shared contracts, root manifests, root lockfiles, global developer instructions, integration branches, the shared progress ledger, and architecture decisions. Workers may propose changes to those files in a handoff, but must not independently mutate them. Every assignment names its allowed paths and interface version. The folder names below are recommended ownership boundaries; adapt them once to the chosen repository structure and publish the final map before work starts.

| Role | Exclusive implementation paths | Deliverable and boundaries |
|---|---|---|
| Coordinator / integrator | `packages/contracts/`, root configuration, `docs/architecture/`, `docs/progress.md`, `.claude/agents/` | Define versioned interfaces, sequence tasks, resolve conflicting proposals, integrate commits, run the final vertical slice. Do not become a second implementation owner for every feature. |
| Backend and data | `services/api/`, `services/state/`, backend unit tests | Authenticated API, transcript/intent/task/event records, optimistic concurrency, CRUD and completion/cancellation semantics, signed uploads, database adapters. Consume published event schemas; do not recreate scheduler or retrieval behavior here. |
| Frontend and interaction | `apps/web/`, UI-specific tests | Voice capture controls, transcript correction, uncertainty resolution, daily outline, task/event details, notification preferences, clear processing/error states, responsive accessible interface. Use contract fixtures while backend work is in progress. Never put AWS or model credentials in browser code. |
| Pipeline and orchestration | `services/pipeline/`, `services/scheduler/`, pipeline tests | Idempotent capture → transcription → extraction → persistence → embedding jobs, retries, dead-letter handling, cancellation-aware scheduling, run status, local workers, event adapters. Own coordination code rather than speech inference/model selection. |
| Speech engineering | `services/speech/`, speech adapter tests and evaluation fixtures | A self-hosted speech-to-text service using an existing open-source pretrained neural network, audio validation/conversion, model load lifecycle, transcription response contract, language settings and confidence/quality behavior. Own inference, not task extraction. Training a recognizer from scratch is out of scope for the first release. |
| Retrieval and planning | `services/retrieval/`, `services/planner/`, retrieval evaluation fixtures | Metadata filtering, semantic memory retrieval, optional lexical baseline, context assembly, source references, grounded preparation and day planning, hard constraint validation. Never let similarity search replace querying actual active obligations. |
| Infrastructure and operations | `infra/`, `ops/`, infrastructure tests | IaC, local/cloud deployment manifests, queues, scheduling integration, least privilege IAM, secret handling, budget controls, observability, model artifact management, deployment runbooks. Prepare reviewable plans; don't provision paid resources merely to satisfy a timer. |
| Quality / security reviewer | Read-only by default; `tests/e2e/` when explicitly assigned | Cross-feature acceptance tests, isolation and permissions review, stale reminder/retry tests, changed-deadline and tentative-intent cases, model/cost regression checks. Return findings with severity, location, reproduction, and ownership. |

These are responsibilities, not a requirement to keep seven model contexts running. Start with the coordinator plus three or four workers. Increase to at most five workers only when truly independent work and provider/runtime limits permit. Rotate the same workers between bounded assignments after clean handoffs. Do not spawn nested delegations or duplicative "research", "architecture", and "review" agents for every small change.

### Contracts-first barrier

Before parallel implementation, the coordinator must publish a small runnable contract package and freeze a numbered version. Include JSON Schema or equivalent validation schemas, generated or shared TypeScript types, API request/response examples, event envelopes, error codes, and fixtures. Provide at least one fixture for a complete event, an incomplete event that needs clarification, a canceled task, and a revised deadline. Workers acknowledge the version and can implement against fixtures without waiting for other workers.

Define these interfaces early: capture submission and status; audio object reference; transcription output; extraction proposal; clarified/persisted intent; immutable source reference; task/event current-state record; embedding request; retrieval request and evidence; planning input/output; schedule creation/cancellation; notification delivery attempt. The contract includes authenticated user identity, stable entity/run IDs, schema version, event type, timestamp, source IDs, idempotency key, current record version, and correlation ID where applicable. Do not send large audio bytes through workflow events; use authenticated object references. Use explicit timezone and UTC timestamp conventions.

After freezing the contracts, pipeline and speech agents can implement their adapters concurrently; frontend and backend can implement fixture-backed interactions concurrently. Infrastructure can prepare local services and cloud modules in parallel. A breaking schema change requires a coordinator-reviewed proposal and a new version, followed by consumer updates. Workers may not make each other discover a changed property through failing builds.

### Worktree and merge procedure

Each writing worker gets a dedicated feature branch and git worktree created from the same published contract commit. Explicitly verify the base SHA; current Claude Code worktree defaults may use the remote default branch rather than the lead's in-progress `HEAD`. Native `isolation: worktree` or supported `worktree.baseRef` settings may be used after checking installed-version behavior; plain `git worktree` remains the fallback. Do not copy private credentials into every worktree. Do not share mutable build output directories or a single install process between workers.

An assignment begins with branch, worktree, allowed paths, base SHA, dependencies, deliverable, acceptance criteria, and exact validation commands. Workers inspect their own diff before handing off. The coordinator integrates one branch or commit sequence at a time, preserving authorship and checkpoint history. It checks the staged changes, executes relevant integration checks, and resolves conflicts using the agreed contracts. Workers do not merge other workers' branches, rewrite the integration history, force-push, or delete another worker's worktree. A worker needing another domain sends a narrow request to its owner.

If worktrees are unavailable, allow concurrent writes only to disjoint paths with one writer per file, and serialize all root/configuration/contract edits. If the runtime cannot safely support multiple writing contexts, retain parallel read-only research/review and serialize implementation. Correctness takes precedence over pretending unsupported parallelism exists.

### Three-minute commits during active implementation

The user requested commits every three minutes. Implement a target checkpoint cadence of approximately 180 seconds of active development per worker, and checkpoint before handoff or context reset. The cadence must not create empty commits, stage another person's work, bypass tests, include secrets, or interrupt a currently running validation process. A timer cannot guarantee a model action while its session is asleep, disconnected, or blocked. Describe the cadence accurately as best effort at the next safe active-session boundary.

At a due checkpoint: finish a coherent edit; review `git status` and the diff; run a fast domain-appropriate validation; stage only explicitly owned paths; inspect the staged diff; commit with a descriptive message, task ID, and checkpoint designation when incomplete. `git add .` and `git add -A` are forbidden for automatic checkpoints. Use explicit paths. Capture the commit SHA and validation result in the next handoff. If there is no safe changed work, record the reason in the coordinator's next progress update and keep working; do not manufacture a commit. If a feature is intentionally incomplete, its isolated branch may contain a clearly labeled checkpoint with the remaining work disclosed. It must not be represented as ready to merge.

Prefer an explicit reviewed file list to staging an entire owned directory if unrelated changes are present there. Keep `.env`, credentials, model weights, generated bundles, caches, raw private recordings and private user data out of commits. Small consented/licensed test audio is allowed only when deliberately designated as a fixture. Create a repository-local checkpoint reminder helper if useful: it tracks the worker's last checkpoint and reports when 180 seconds elapsed, but does not autonomously stage/commit. Each worker checks it at normal action boundaries; the coordinator records any meaningful delay. This is a development workflow, not a recurring Metis user reminder and not an account-level automation request.

Do not rerun the entire test suite every three minutes. Checkpoint validation can be schema checks, formatting/type checks scoped to the package, or the affected unit tests. Full integration and end-to-end checks run at merge/milestone boundaries. Do not weaken tests or skip hooks to hit the clock. If git identity or a repository is missing, report the concrete setup blocker rather than claiming commits exist. Do not push, publish, or deploy just because a checkpoint is due. A checkpoint reminder hook may announce elapsed time; it must not blindly commit or run an unbounded background mutation loop.

### Token-efficient task briefs and handoffs

The coordinator reads this master prompt once, then derives a short `CLAUDE.md` index with non-negotiable rules and links to role references. Avoid loading this entire specification into every worker turn. Keep role prompts and fixtures in separate files so agents load the relevant material on demand. Claude Code skills support this pattern, but their full body can be preloaded into a subagent; preload only the skills that role will actually use.

Use a bounded assignment format:

```yaml
task_id: speech-02
owner: speech
goal: Implement the transcription adapter behind contract v1.
base_sha: <verified contract commit>
allowed_paths: [services/speech]
read_first: [packages/contracts/speech.schema.json, docs/architecture/speech.md]
dependencies: [contracts-v1]
acceptance: [fixture audio produces contract-valid text, invalid media is rejected]
validation: [<actual package command>]
return: [changed paths, commit SHAs, checks, unresolved decisions]
```

The following are working heuristics, not platform-enforced token guarantees: aim for 300–800 words per assignment and 150–300 words per routine handoff; read roughly 100–200 lines around relevant code before expanding; print at most about 100 useful lines of a failing log while retaining the complete log outside model context. Prefer `rg`/symbol navigation to reading whole repositories. Do not dump dependency trees, generated bundles, audio bytes, model weights, large JSON payloads, or complete test logs into conversation context. Small test fixtures are preferable to huge copied user histories.

Each worker returns facts: branch/worktree, base/contract version, changed paths, commit SHAs, behavior added, tests and outcomes, remaining blocker, requested shared-file changes. Do not send continuous narrations, generic status pings, duplicate source research, or broadcast messages when one owner is sufficient. Reuse the same worker while its context remains useful; shut it down when done rather than paying for idle coordination.

The coordinator alone updates `docs/progress.md` with task states (`ready`, `active`, `blocked`, `review`, `done`), owners, dependency IDs, current contract version, accepted decisions, integrated SHAs, verification results, next action, and blockers. Keep it factual and compact. Before compaction or clearing a session, update the relevant handoff, then restore from the ledger and relevant contracts. Recheck current files before relying on an old summary.

### Milestones and completion evidence

Use bounded waves: first contracts/local scaffold; then voice ingestion, state API, fixture UI, and infrastructure; then extraction/scheduling/retrieval; finally grounded planning, frontend integration, and cross-feature review. Each wave produces runnable behavior, not disconnected agent demonstrations. Demonstrate the complete Bloomberg-event flow, a brainstorming thought that remains tentative, an engineering resumption briefing, cancellation before a due reminder, and a changed deadline. Include a local mode that works without additional third-party API keys and a documented cloud path. Report real test output and actual commit SHAs; never claim successful deployment, scheduled delivery, or cloud cost measurements from mocks alone.
