<!-- Extracted from prompt (1).md; block `delivery`. Do not edit here; edit the source spec. -->

## Implementation phases and deliverables

### Phase 0 — inspection, contracts and fixture baseline

Deliver a repository map, current stack decision, concise `CLAUDE.md`, extracted spec blocks, role ownership table, development ledger, baseline data/event contracts, configuration schema, provider adapters and local fixture mode. Validate Git status and install only the selected dependencies. Bootstrap coherent shared contract generation before parallel feature changes.

Phase gate: typed synthetic capture validates and saves in local mode; all agents share the same contract commit and schema version; the frontend can render the same fixture records the backend produces.

### Phase 1 — complete capture and commitment slice

Deliver text capture, authenticated source persistence, extraction/validation, editable tasks/events/thoughts, clarifications, source provenance and change history. Build real Bedrock adapter and fixture adapter behind the same interface. Add audio upload/status UI concurrently with the owned ASR worker implementation.

Phase gate: a synthetic Bloomberg intent with missing date remains unresolved; a single clarification resolves it; an explicit cancellation changes current state and preserves the source history. No invented date, registration or completion.

### Phase 2 — owned speech and dependable reminders

Deliver recording/upload, finite cloud ASR jobs, finalized transcript/correction, source-version handling, schedule synchronization, reminder dispatch, snooze/cancel, in-app feed and web-push adapter. Verify the production worker path independently from fixtures.

Phase gate: recorded input reaches structured state; duplicate job deliveries do not duplicate commitments; a reminder canceled or moved before dispatch is suppressed when its old schedule fires. Report ASR cold/warm latency, the external transport cancellation boundary, and cost assumptions.

### Phase 3 — contextual planning and engineering resumption

Deliver semantic indexing and scoped retrieval, citations, work packets, deterministic plan validation, realistic daily outline, overflow, acceptance/edit feedback and historical attempts. Compare retrieval baselines before enabling hybrid or reranking. Plans remain usable when retrieval is stale or unavailable.

Phase gate: resuming a project surfaces its actual latest blocker and failed attempts; daily planning preserves fixed events and exposes infeasible workload rather than fitting everything into invented free time.

### Phase 4 — reproducible cloud readiness

Deliver CDK stacks, least-privilege runtime roles, CI checks, deployment/runbook, cost worksheet, teardown/retention guidance, observability, encrypted transport/storage, export/deletion, and supported platform testing. Synthesize and validate deployment plans locally. Deploy only in an authorized AWS account/environment, under the implementation session's applicable authorization. This specification-writing task itself creates no AWS resources.

Phase gate: independent setup from the README succeeds in fixture mode; an authorized cloud smoke test covers real ASR, inference, state, schedule and delivery. If credentials or access are unavailable, mark those checks unrun and supply the exact missing prerequisite. Never label a mocked demo as deployed production.

### Later phases

Add screenshot input, ICS/provider calendar integrations, live transcription, rich external research, optional AgentCore hosting and learned reranking only after the earlier slice is correct. Preserve service interfaces, cost limits and state/version contracts.

### Required project outputs

- Working frontend and backend with a full local demo.
- Owned ASR service, container build and model-artifact setup.
- Typed versioned contracts and fixtures.
- Pipeline workers with idempotency, outbox/reconciliation and schedule synchronization.
- Retrieval adapters, evaluation fixtures and readable results.
- Meaningful tests for state changes, scheduling, concurrency and failure recovery.
- Infrastructure as code and non-secret environment examples.
- Setup README, operational runbook, cost estimate, architecture decisions and concise agent handoff ledger.
- Git history reflecting reviewed work and approximate three-minute active-work checkpoints.
- Final delivery report separating implemented, tested, deployed, deferred and blocked items.

### Completion criteria

Do not declare Metis complete merely because screens render. The end-to-end behavior, authoritative-state transitions, source links, cost controls and reminder cancellation must work. Run targeted tests after each meaningful change; run broader integration checks at phase boundaries or when changes/failures justify them. Avoid repeating every check after a documentation-only change.

Key acceptance scenarios, in addition to the detailed architecture/speech tests:

1. "Maybe I should attend" creates tentative intent, not a confirmed calendar appointment.
2. An explicit task with a date is retained even if its note is semantically unrelated to the daily planning request.
3. An event without a date produces clarification and no guessed event-time alarm.
4. "I already registered" updates registration and removes a now-useless registration follow-up.
5. "Cancel that" resolves its target or asks if ambiguous; it does not cancel every similar event.
6. A task update made while planning invalidates the obsolete generated plan before acceptance.
7. A failed approach remains historical evidence and is not recommended as an untried approach.
8. An edited transcript supersedes its old extraction and indexing jobs safely; correcting a deadline after schedule creation reconciles the old reminder while preserving later manual edits.
9. A source deletion makes it inaccessible immediately and removes derived vectors/cache content through a resumable process.
10. A cross-user vector hit is rejected before any text reaches the model.
11. A provider outage leaves raw capture, manual edits and already-scheduled deterministic reminders usable.
12. The budget cap blocks optional inference while retaining essential non-model operations.
13. Over-capacity planning returns an overflow list and explains which constraints prevented scheduling.
14. Offline retry with the same client request ID produces one source and one set of commitments.
15. A claimed delivery has evidence from the delivery adapter; unsupported push environments use the visible feed.
16. Ordinary reminders run without invoking an LLM.
17. JSON that conforms structurally but asserts an unsupported deadline fails semantic validation.
18. Development agents do not overwrite each other's changes or commit another agent's unrelated files.

### Final implementation report format

Keep the report short and factual: working behaviors, principal architecture decisions, meaningful tests/results, measured/estimated cost and latency, unrun checks, and concrete limitations. Include exact commands for local startup and authorized deployment. Link to documentation instead of pasting large logs. Do not claim unavailable design skills, deployment, agent concurrency, training, or passing checks.
