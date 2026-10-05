# Metis — implementation prompt for Claude Code

Specification date: 2026-10-05. Status: implementation instructions, not a claim that any code, cloud deployment, or integration already exists.

## Execution mandate

Build **Metis**, a voice-first multimodal personal assistant that converts unstructured thoughts into reliable memory, explicit commitments, concrete engineering next steps, realistic daily plans, and timely follow-ups. The user should be able to speak naturally without maintaining a carefully organized note system. The application does the organizing and makes the information useful again when it matters.

Use this document as the canonical product specification. Implement a working application with a complete local demonstration and a reproducible AWS deployment path. Continue through implementation and validation; do not stop after writing a plan or scaffolding screens. If a genuine external prerequisite blocks deployment, complete all independent work and produce the exact reviewable deployment steps, with the blocker stated accurately.

The user explicitly requests concurrent development agents, an owned speech-to-text service without unnecessary third-party API keys, token-conscious development and runtime behavior, and Git checkpoints approximately every three minutes during active implementation. These requirements apply to the future implementation session started with this prompt. Creating this document has not started a persistent commit timer or deployed infrastructure.

Preserve these user decisions:

- Project and product name: **Metis**.
- The complete product is a multimodal assistant; RAG is one subsystem.
- Speech capture must lead to structured, actionable records, not just transcript text on a screen.
- Daily planning, brainstorming continuity, engineering task resumption, event preparation, and follow-up are primary use cases.
- Support asynchronous work while the client is closed.
- AWS is the preferred cloud, with Bedrock for model inference and IAM-based service credentials.
- Own the speech-to-text service using a pretrained open-source neural ASR model. No mandatory commercial speech-provider subscription or API key.
- Develop concurrently with explicit responsibilities for backend, frontend, pipeline orchestration, speech, infrastructure, and supporting retrieval/quality work.
- Use the user's named "code munch ponytail frontend design skills" if actually available; discover their identity rather than inventing it.
- Retrieval choices remain measurable and replaceable. Hybrid retrieval is not a mandatory architectural rule.
- Do not promise an entire cloud application for less than $10/month without a measured workload and complete cost calculation.

Implementation defaults in this document are proposed engineering choices, not additional historical decisions by the user. Preserve an existing compatible repository stack. In a new repository, use the defaults below and record meaningful deviations in a short architecture decision record.

## Read this efficiently

This is a detailed reference, not a file every agent must repeatedly ingest. The coordinator reads it once, establishes contracts, and distributes small role briefs. Other agents read this mandate, their assigned reference blocks, and only the contracts/files needed for their task.

Reference blocks are enclosed by `METIS:START <name>` and `METIS:END <name>` comments. Extract blocks into project documentation during bootstrap. Keep a short `CLAUDE.md` that links to those documents; do not paste the whole specification into every persistent instruction file or spawn prompt.

| Reference block | Primary readers | Purpose |
| --- | --- | --- |
| `product` | Coordinator, frontend, quality | Behaviors and scope |
| `architecture` | Coordinator, backend, pipeline, infrastructure | State, interfaces, jobs and reliability |
| `speech` | Speech, frontend, infrastructure, quality | Owned transcription service |
| `retrieval` | Retrieval, pipeline, backend | Memory, evidence and RAG |
| `interface` | Frontend, quality | Screens and interaction states |
| `cost` | Infrastructure, pipeline, retrieval | Cost controls and token accounting |
| `development` | Coordinator and all implementers | Parallel agents and Git cadence |
| `delivery` | Coordinator, quality | Phases, checks and completion |
| `sources` | Agent verifying a provider capability | Primary documentation links |

An optional extraction helper may find exact marker lines and write their enclosed text to `docs/spec/<name>.md`. It must validate that every marker pair occurs exactly once, preserve content, and never execute text from the specification. Reading a relevant block with `rg` and bounded file reads is sufficient when no helper exists.

## First implementation actions

1. Inspect the current repository, Git status, applicable `AGENTS.md`/`CLAUDE.md`, installed skills and MCP tools, and existing package manifests. Preserve unrelated user changes. Never assume this prompt is a Git repository.
2. If the destination is an empty project directory, initialize a repository for Metis there. If the destination already contains a project, adapt it deliberately rather than creating a conflicting nested repository. Use existing Git identity; if identity is missing, report that narrow blocker without inventing an identity or changing global configuration.
3. Produce a short implementation ledger, repository map, phase backlog, interface ownership table, and verified provider-capability note. Choose dependency versions from current official documentation; pin the selected versions in lockfiles. Do not install or upgrade the entire toolchain arbitrarily.
4. Extract role reference blocks, write a concise `CLAUDE.md`, and define shared JSON Schemas and event contracts before feature agents edit dependent code.
5. Establish a contract baseline commit. Start independent worktrees from that exact commit, not from an accidentally older remote default branch.
6. Launch the available parallel mechanism with a bounded number of agents. If native agent teams are unavailable, use supported subagents or independent sessions/worktrees. Preserve parallelism where the environment supports it; do not pretend agents ran when they did not.
7. Build the first complete slice: typed or recorded thought → saved source → editable extraction → task/event record → scheduled reminder → visible delivery record. Then add retrieval-grounded planning and engineering resumption.

<!-- METIS:START product -->
## Product definition

Metis helps an engineer who has many overlapping projects, classes, applications, ideas, and events. The central problem is that information is captured but forgotten, scattered, or too vague to turn into a next action. The central outcome is a useful outline of what to do today, supported by persistent state and recoverable thought history.

The product should reduce the effort of remembering and resuming work. It should not require the user to sort every thought into folders before it can help. It should also avoid promoting every idea into an obligation.

### Terminology

- **Source:** the original typed text, finalized transcript, or later supported attachment. A source is evidence of what the user said or imported.
- **Thought:** a captured idea, observation, question, hypothesis, or reflection. It does not automatically create a task.
- **Commitment:** an explicitly intended action with a task state. Deadlines and dependencies are stored as typed fields.
- **Event intent:** interest in attending something, possibly with unresolved date, registration, or venue details.
- **Event:** a time-bound appointment or activity, with an explicit attendance state and source-linked details.
- **Decision:** a chosen direction; later decisions may supersede it without deleting history.
- **Memory:** a searchable representation linked to a source. It is not a model-weight update and is not the authority for current task status.
- **Work packet:** enough context to resume an engineering task: objective, current state, attempted approaches, blocker, next action, completion criterion, relevant sources, and an estimated time range.
- **Plan:** a proposed allocation of current tasks to available time. It is versioned and can be revised.
- **Reminder:** a scheduled follow-up with a job identity, current target version, delivery state, and user-configured channel.
- **Agent:** distinguish a Claude Code development agent from a Metis runtime planning worker. They have different lifetimes, permissions, budgets, and responsibilities.

### Primary workflow: Bloomberg event

Use a synthetic event as a fixture; do not imply an actual Bloomberg event has been discovered or verified.

The user says: "There is a Bloomberg event in New York I want to go to. Remind me to register and help me prepare."

Expected behavior:

1. Save the source immediately and display processing progress.
2. Extract the event title and city, interest in attending, registration task, and preparation request.
3. Keep the event date/time, exact venue, registration deadline, and registration completion unknown unless supplied by a source.
4. Ask a compact clarification such as the event date and preferred registration reminder time. Accept a single reply that resolves several fields.
5. Create a candidate preparation task and show its basis. Do not claim that a ticket was booked or registration completed.
6. Once a time is known, schedule the requested follow-up through ordinary application logic.
7. If a briefing is requested, retrieve relevant project background and produce a practical preparation outline with source links. For example, suggest how to explain Cerno, but do not fabricate its achievements, attendees, agenda, or a train departure.
8. Include preparation in the daily plan only when it is active and fits current capacity.
9. When the user cancels or reschedules the event, update dependent tasks, plans, and schedules. A delivery worker must discard stale jobs.

### Primary workflow: engineering resumption

The user records: "On Cerno, the review keeps re-reading files. I tried caching whole files but it got stale after changes. Next I want to test diff-aware invalidation. I have about forty minutes tomorrow."

Expected behavior:

- Preserve the raw reasoning, attempted approach and limitation.
- Record a candidate task to test diff-aware invalidation, with the referenced project and suggested capacity.
- Distinguish a proposed test from a final design decision.
- On "where did I leave off?", retrieve the latest relevant state and prior attempts for that project.
- Generate a work packet with a concrete first action, such as specifying invalidation cases, then testing the smallest reproduction. Treat any inferred file path or command as a suggestion unless verified from imported project material.
- Suggest a completion criterion appropriate to the captured information, such as documenting when a cached result must be invalidated. Do not invent passing test results.
- Allow "I tried that and it failed" to update the task and add an attempt record. Preserve failure context so it is not suggested repeatedly as if never tried.

### Primary workflow: daily planning

The user asks: "Plan today around my event, studying, applications, and Cerno."

The application first reads all relevant active obligations and explicit calendar blocks from authoritative records. It then retrieves supporting memories for a bounded set of tasks. A language model proposes the outline; deterministic code validates it.

The plan must include:

- Actual available intervals and explicit fixed appointments.
- A small number of primary outcomes, initially at most three major focus tasks, configurable by the user.
- Tasks broken into executable steps with completion criteria.
- Estimated durations as estimates, with a range or uncertainty where useful.
- Preparation and transition time when configured or explicitly known.
- A visible overflow list if commitments exceed capacity.
- A reason for task selection grounded in deadline, explicit priority, dependencies or a stated goal.
- Source links for retrieved context and a clear distinction between recorded facts and suggestions.
- A sensible fallback outline when AI or retrieval is unavailable.

For v1, include every eligible near-term obligation in the planning input even when semantic similarity would rank it poorly. Do not retrieve only the top similar notes and assume those represent the user's complete obligations.

### Primary workflow: brainstorming continuity

The user can capture disconnected ideas over several days and later request an outline. Metis groups related thoughts, surfaces unresolved questions, and connects new thoughts to earlier attempts. The user can promote a thought to a task or decision. Capture and organization can be automatic; consequential changes should reflect explicit intent and the product's selected autonomy policy.

Outlines should have useful structure: objective, existing observations, alternatives, constraints, next experiment, open questions, and source references. Do not produce a generic summary when the user asked for an actionable outline.

### Scope and priorities

**V1 must implement:**

- Text capture and push-to-record audio capture.
- Owned cloud-hosted ASR service with visible asynchronous states and typed fallback.
- Source-preserving structured extraction, corrections and clarification.
- Tasks, thoughts, projects, decisions, events, reminders and preferences.
- Manual calendar availability and fixed blocks; no mandatory external calendar provider.
- Reliable scheduled reminders with cancellation, snooze and duplicate prevention.
- Retrieval-backed project resumption and daily planning.
- Current-state checks, provenance, deletion and export.
- Cost accounting, bounded model calls, offline fixtures, and observability sufficient to debug a missed reminder.
- Responsive PWA interface and reproducible local/cloud setup.

**V1.1 or later:**

- Opt-in ICS import/export before provider-specific calendar OAuth.
- Screenshot/image capture with selected OCR or a Bedrock vision-capable model, using the same source and extraction contracts.
- Approved calendar synchronization and additional notification channels.
- Live partial transcription when justified by the recorded-audio benchmark.
- Richer event preparation through explicitly selected web/calendar connectors.
- Measured reranking, learned preference ranking, or model fine-tuning when sufficient consented feedback exists.
- AgentCore hosting when runtime workflows exceed the short-worker implementation.

**Not required for v1:** custom foundation-model training; reinforcement learning of schedules; continuous microphone monitoring; generic autonomous web browsing; automatic purchases; outbound email; enterprise Q Business; a permanent GPU endpoint; a social network; a note editor with every feature of Notion; or a separate paid API provider for each stage.

### Autonomy and uncertainty

- Save a raw note without asking permission again.
- Create tasks/reminders from clear user instructions when required fields are known.
- Keep ambiguous ideas tentative and unresolved times null. Do not use invented placeholder dates as real schedules.
- Ask only clarifications that affect behavior. Batch related missing fields where possible.
- Store a generated daily plan as proposed. The user can accept or adjust it with one action.
- Do not silently move fixed appointments or externally commit the user to an event.
- Do not treat lack of response to a notification as task completion.
- Mark an action completed only from an explicit user update or a reliable, authorized integration result.
- Keep no more than one active clarification thread per captured item unless new independent uncertainty is material.
- Learn editable preferences from explicit selections or clearly labeled repeated patterns; do not invent a permanent personality profile from one choice.

### Neural-network boundaries

Metis uses existing neural networks for ASR, embeddings and language generation. These components form a pipeline; they do not require one end-to-end multimodal model. Deterministic code handles identity, validation, scheduling, state transitions, budgets and permissions.

Saving a note, embedding it, or updating a task does not train model weights. A future learned ranker needs a separate dataset, feedback consent, held-out evaluation, training/version management and a rule for returning to the simpler baseline. Do not add an RL component merely because the developer has RL experience.
<!-- METIS:END product -->

<!-- METIS:START architecture -->
## Architecture and implementation contract

The following is the proposed implementation for Metis. Treat these choices as the proposed project baseline, not a claim that a particular retrieval algorithm, model, or cloud service is universally optimal. Implement the usable product before adding elaborate agent machinery.

Metis is a voice-first multimodal assistant for capturing thoughts, distinguishing ideas from commitments, maintaining project context, producing realistic daily outlines, and following up at the appropriate time. RAG is one capability within this system. Transcription, extraction, authoritative state, scheduling, delivery, and planning are separate responsibilities. Persisting personal information does not train the foundation model's weights.

### Component boundaries

| Component | Responsibility | Initial implementation |
|---|---|---|
| Web application | Recording, text/image capture, review, today view, inbox, project context, completion and correction | React, TypeScript, Vite PWA, and Tailwind; responsive desktop/mobile experience |
| API | Authentication, authorized reads/writes, upload coordination, schema validation, optimistic concurrency | Python FastAPI behind API Gateway and Lambda |
| Authoritative state | Tasks, events, preferences, revisions, plans, schedules, job status, notification records | DynamoDB on demand |
| Source storage | Original recordings, transcripts, screenshots, attached documents | Private S3 buckets |
| Pipeline | Queue-based ingestion, extraction, enrichment, indexing, bounded planning work | SQS and Lambda; durable job records |
| Speech service | Owned open-source speech-to-text inference behind an application contract | `faster-whisper`, `small.en`, CPU int8, asynchronous finite ECS Fargate worker task |
| Language inference | Structured extraction and optional contextual generation | Bedrock Converse with configured model adapters |
| Semantic memory | Search supporting thoughts and historical context | Optional S3 Vectors adapter; structured queries work without it |
| Timing | Invoke due reminder and planning jobs | EventBridge Scheduler |
| Delivery | In-app notification records plus configured push delivery | Separate delivery adapter; never require an external messaging API for MVP |
| Infrastructure | IAM, deployment, cost controls, logging, retention, alarms | AWS CDK TypeScript; synth/plan before deployment |

Keep short orchestration jobs in Lambda. Do not load heavyweight speech models into every API invocation. Do not maintain an LLM process that repeatedly checks whether anything is due. AgentCore is an optional later runtime for longer tool workflows; the MVP uses queues, state transitions, and bounded Bedrock calls. Do not require Bedrock Agents Classic.

Use AWS identity and role-based credentials server-side. No Bedrock credentials, permanent AWS keys, or model-service secrets belong in the browser. An application-owned speech worker avoids a separate speech SaaS key; it still incurs cloud compute/storage costs and must use a properly licensed model. Push may require platform credentials or a generated Web Push signing key; document this rather than promising that every feature is credential-free.

### Shared records and schema rules

Define contracts before parallel feature implementation. Use coordinator-owned versioned JSON schemas and TypeScript/Pydantic contracts. Every record must have an opaque `id`, `user_id`, `schema_version`, `created_at`, `updated_at`, and `revision`. Backend authorization supplies `user_id`; clients cannot choose another owner. Use UTC ISO timestamps for instants and IANA timezone identifiers for interpreting local dates. Store date-only deadlines separately from precise timestamps. Typed/manual calendar capture is the MVP; ICS import/export and provider integrations are later opt-in features and must not be required.

Required entities:

- **SourceArtifact:** source kind (`audio`, `text`, `image`, `document`), S3 references, capture timezone, upload/checksum information, lifecycle state, and retention settings. Transcripts retain model/version and segment timestamps. A normalized note refers to this record rather than copying raw audio into prompts.
- **ExtractionProposal:** source ID, proposed items, missing fields, evidence spans, extraction/model version, and disposition (`pending`, `accepted`, `edited`, `dismissed`). A confidence value is a routing hint, never proof of truth.
- **Task:** title, description, project ID, state (`inbox`, `ready`, `in_progress`, `blocked`, `done`, `canceled`), optional deadline, duration estimate and provenance, dependencies, priority, source references, and completion/cancellation timestamps.
- **Event:** title, location or meeting link, date-only or precise start/end, timezone, attendance intent (`tentative`, `committed`, `declined`, `canceled`), registration state (`unknown`, `not_registered`, `registered`), external reference if present, and source references. Registration is never inferred solely from an intention to attend.
- **Memory:** source-backed content, project/entity links, kind (`idea`, `observation`, `hypothesis`, `decision`, `commitment`, `work_log`), validity (`current`, `superseded`, `uncertain`), optional `supersedes_id`, effective time, and immutable evidence references. Task/event status remains authoritative in the task/event records.
- **Reminder:** target entity ID and required revision, desired trigger, timezone, status, scheduler reference, delivery channel, and deduplication identifier. Quiet-hours behavior and reminders for overdue work follow explicit user preferences.
- **Plan:** target local date, timezone, input entity revisions, time budget, proposed blocks, explanations, source references, generation status, and validation results. It is a proposal; displaying a plan does not imply tasks were completed or commitments changed.
- **Job/OutboxEvent:** type, schema version, owner, target revision, correlation ID, idempotency key, attempt state, timestamps, and failure category. Queue messages contain references, not unnecessarily large transcripts or secrets.
- **Notification:** owner, reminder/job reference, body, source/action links, deduplication key, delivery status, and read/dismissed timestamps.
- **Preferences:** timezone, work hours, quiet hours, breaks, daily planning time, channels, default planning horizon, and model/speech budget settings. Start with user-editable defaults; do not invent availability from note wording.
- **Project:** title and aliases, current objective, related tasks, source-backed blocker/next-action references, current decision pointers, archive state, and user-pinned resumption context. Project summaries are derived/versioned and cannot fabricate project progress.

An extracted item has an explicit intent type. “Maybe I should go to Bloomberg” creates a tentative event/idea. “Remind me tomorrow at 9 to register” creates a requested reminder if the local date, time, and target can be resolved. “I tried Kafka and abandoned it” is a work-log/decision update, not a new Kafka task. Store where each interpreted fact came from. An estimate generated by Metis must be labeled as an estimate.

Maintain immutable revisions or source-linked change records for important state changes. Do not keep superseded versions active because they have high semantic similarity. Do not silently convert every brainstorm into a task. Updates must distinguish `set`, `clear`, and `unchanged`; a missing field does not erase an existing value.

On transcript correction, invalidate pending extraction/index results for the previous source version. Also identify already-created fields/tasks derived from that version. Automatically reconcile an unconfirmed source-derived field only when its current provenance/value still matches the old extraction and no later user/integration edit superseded it. Suspend not-yet-dispatched reminders depending on a disputed critical date while clarification is pending. Preserve explicitly confirmed or manually edited values, show the conflicting old/new evidence, and request a narrow resolution rather than silently overwriting them. Maintain the old source and change history. User correction is not merely a second note leaving the original erroneous commitment active.

Deleting a capture removes application access to its source and derived memory immediately through tombstones, then purges object/vector/cache copies asynchronously. Explicitly committed tasks need a documented policy: preserve their independent user-confirmed fields with an unavailable-source marker, or cancel them if the user selected deletion of associated commitments. Do not unexpectedly delete an independently edited task when its original note is removed. Short-lived previously issued object URLs have their own expiry/revocation boundary; avoid promising recall of downloaded/exported copies.

### API and event contracts

Use one contract package as the source of truth. API requests carry `Idempotency-Key` for mutations; entity updates also carry an expected revision. A repeated successful request returns the prior result. Revision conflicts return `409` with a reload/review path. All paginated queries have bounded limits and stable cursors.

| Method and route | Contract and outcome |
|---|---|
| `POST /v1/captures` | Create a text capture or request a short-lived authorized upload URL for audio/image; return capture ID |
| `POST /v1/captures/{id}/complete` | Verify uploaded artifact exists and passes limits; enqueue ingestion; return job ID |
| `PATCH /v1/captures/{id}/transcript` | Create a corrected finalized transcript revision with expected revision; supersede old extraction/index jobs |
| `GET /v1/jobs/{id}` | Return the user's current job phase, result references, and actionable error |
| `GET /v1/captures/{id}` | Source metadata, transcript if present, extracted items, and review state |
| `POST /v1/proposals/{id}/resolve` | Accept/edit/dismiss extracted items using expected proposal revision |
| `GET /v1/tasks` and `GET /v1/events` | Structured filters for date/project/status; current state; cursor pagination |
| `PATCH /v1/tasks/{id}` and `PATCH /v1/events/{id}` | Validated changes with expected revision; emit outbox effects in the same database transaction |
| `POST /v1/reminders` | Create an explicit reminder request; distinguish `pending_schedule` from `scheduled` |
| `GET /v1/today?date=YYYY-MM-DD` | Deterministic commitments/current tasks plus existing plan and freshness state |
| `POST /v1/plans` | Request a bounded asynchronous plan for one date/time budget; return job ID |
| `POST /v1/plans/{id}/accept` | Accept using expected plan revision and planning epoch; atomically reject stale inputs with `409` |
| `PATCH /v1/plans/{id}` | Edit/pin proposed blocks with expected revision; rerun deterministic capacity/dependency validation |
| `GET /v1/projects` and `GET /v1/projects/{id}` | Current project state and source-backed resumption context |
| `POST /v1/projects` and `PATCH /v1/projects/{id}` | Create/update explicit project identity, aliases and objective with normal ownership/revision rules |
| `POST /v1/search` | Structured/semantic memory query with explicit project/date scope and source-backed results |
| `GET /v1/notifications` | In-app delivery history with read status and entity links |
| `PATCH /v1/preferences` | Validated local-time, retention, planning, and budget preferences |
| `POST /v1/push-subscriptions` | Register the authenticated user's supported browser push subscription; validate endpoint and ownership |
| `DELETE /v1/push-subscriptions/{id}` | Remove this user's subscription and stop future dispatches to it |
| `DELETE /v1/captures/{id}` | Immediately tombstone application access; enqueue resumable derived-data deletion; return deletion job |
| `POST /v1/exports` | Create an authenticated export job for selected user-owned records and sources |

Avoid inventing public agent-to-agent endpoints. Internal event types include `capture.uploaded`, `transcript.finalized`, `extraction.completed`, `entity.changed`, `memory.index_requested`, `reminder.schedule_requested`, `reminder.cancel_requested`, `reminder.due`, and `plan.requested`. Each envelope includes `event_id`, `event_type`, `event_version`, `user_id`, `occurred_at`, `correlation_id`, `target_id`, and `target_revision`. Authenticate/authorize consumers and reject unsupported versions visibly.

### Repository and adapter layout

Use these boundaries for a new repository, preserving an equivalent existing structure when present:

```text
apps/web/                  React PWA and browser integration
packages/contracts/        canonical JSON Schema, generated TS/Pydantic, fixtures
services/api/              FastAPI application and Lambda entry adapter
services/state/            DynamoDB and local SQLite state adapters
services/pipeline/         extraction, jobs, outbox and workflow coordination
services/scheduler/        schedule synchronization and reminder dispatch
services/speech/           owned ASR worker, Dockerfile, decoding and evaluation
services/retrieval/        memory ingestion, embeddings and scoped search
services/planner/          context assembly, outline generation and validation
infra/                     CDK deployment stacks and infrastructure checks
ops/                       runbooks, cost worksheet and environment procedures
tests/e2e/                 cross-feature scenarios and controllable clock fixtures
docs/spec/                 role-scoped blocks extracted from this prompt
docs/architecture/         accepted architecture decisions
docs/progress.md           coordinator-owned factual implementation ledger
.claude/agents/            compact reusable development-role definitions
```

Backend Python adapters must expose an application service layer that works both locally and through Lambda. Use a pinned maintained ASGI-to-Lambda adapter if needed rather than hand-writing a partial HTTP gateway translation. The coordinator owns root dependency/workspace manifests and lockfiles; domain agents propose dependency additions through narrow handoffs.

Provide `StateStore`, `SourceStore`, `SpeechTranscriber`, `InferenceClient`, `MemoryStore`, `Clock`, `ScheduleStore`, and `NotificationTransport` interfaces. Fixture/local/cloud implementations share contract validation. For local state, use SQLite transactions with the same revision/idempotency/outbox semantics. A fixture inference client is deterministic and explicitly marked as fixture mode. A separate optional local ASR container verifies actual neural decoding without forcing model execution on the user's computer during the default lightweight demo.

### Authentication, database access and consistent snapshots

Default production authentication is an AWS Cognito user pool with a supported browser authorization-code/PKCE flow and JWT validation at the API boundary. Verify current SDK configuration rather than inventing an OAuth flow. Serve the PWA over HTTPS through a private static S3 origin and CloudFront, or preserve an existing compatible frontend host. Local fixture identity is accepted only when an explicit non-production configuration enables it; deployment checks reject that mode for production.

For the personal-scale DynamoDB baseline, partition user-owned entity rows by `PK = USER#<authenticated-subject>` and a typed `SK = <TYPE>#<id>`. Keep queryable revision/change rows under that same ownership scope. The actual table/index design must be documented with example access patterns and transaction boundaries before implementation. No global table scan is permitted to find one user's tasks.

Exact deadline/date indexes may accelerate queries, but an eventually consistent secondary index alone must not define the complete planning obligations. For v1 personal-scale planning, query and paginate the authoritative user's task/event/availability item ranges with strong consistency where supported, then filter current state in code. Do not silently stop at the first query page. If a configured bounded read limit is reached, show incomplete coverage and decline to claim a complete plan. A future obligation projection must be maintained atomically with authoritative changes and meet the same completeness invariant.

Maintain a per-user `planning_epoch` incremented transactionally with changes affecting availability, relevant tasks/events, dependencies or planning preferences. Read the epoch before and after assembling the snapshot; retry within a bounded limit if it changed. This detects edits during multi-page reads. A plan stores the epoch and its relevant entity/source versions. Acceptance atomically verifies the epoch; a newly added commitment must invalidate an old plan even when it was not among the previously retrieved task IDs.

Use conditional claims/leases for jobs, expiring leases for crash recovery, and a reconciler for stalled work. Automatic record TTL cleanup is eventual; never rely on TTL deletion as the precise timer for a reminder or budget release. Define reminder states as `pending_schedule`, `scheduled`, `dispatching`, `delivered`, `schedule_failed`, `delivery_failed`, `snoozed`, `canceled`, or `superseded`, with a validated transition table. A delivered transport acceptance and a user read receipt remain separate facts.

### Canonical examples and field semantics

These are synthetic fixtures, not facts about a discovered event or the user's actual calendar. Treat them as examples for schemas and tests; generate schema/type definitions rather than copying example JSON into production logic. Use one name for each contract field across all services.

Example extraction before clarification:

```json
{
  "schema_version": 1,
  "source_id": "src_demo_bloomberg",
  "source_version": 1,
  "items": [
    {
      "kind": "event_intent",
      "title": "Bloomberg event",
      "location_text": "New York City",
      "attendance_status": "tentative",
      "registration_status": "unknown",
      "starts_at": null,
      "local_date": null,
      "timezone": "America/New_York",
      "requested_actions": ["remind_to_register", "prepare_for_event"],
      "missing_fields": ["event_date_time", "registration_reminder_time"],
      "evidence": [{"source_id": "src_demo_bloomberg", "source_version": 1, "span_ref": "span_001"}]
    }
  ],
  "requires_clarification": true
}
```

Example normalized event after a synthetic clarification that explicitly supplies October 8, 2026 at 6 p.m. in New York:

```json
{
  "id": "event_demo_bloomberg",
  "schema_version": 1,
  "revision": 2,
  "title": "Bloomberg event",
  "starts_at": "2026-10-08T22:00:00Z",
  "ends_at": null,
  "local_date": "2026-10-08",
  "timezone": "America/New_York",
  "attendance_status": "committed",
  "registration_status": "not_registered",
  "location_text": "New York City",
  "venue_address": null,
  "source_refs": ["src_demo_bloomberg:1", "src_demo_clarification:1"]
}
```

The example is a client-safe projection; server records add authenticated ownership and audit timestamps. Do not infer `committed` or `not_registered` unless the synthetic clarification actually states them. An unknown end time remains unknown; any planning duration is a visible estimate, not a fabricated event end.

Canonical internal event envelope:

```json
{
  "event_id": "evt_demo_001",
  "event_type": "transcript.finalized",
  "event_version": 1,
  "user_id": "demo_authenticated_subject",
  "occurred_at": "2026-10-05T14:00:00Z",
  "correlation_id": "run_demo_001",
  "target_id": "src_demo_bloomberg",
  "target_revision": 2,
  "idempotency_key": "transcript:src_demo_bloomberg:2",
  "payload": {"recording_id": "src_demo_bloomberg", "source_version": 2, "transcript_object_ref": "private-object-reference"}
}
```

`recording_id` identifies an audio SourceArtifact; `source_version` identifies its accepted text/source revision for downstream extraction. A transcript edit increments this revision even though audio bytes are unchanged. The immutable audio checksum is separate. `target_revision` must equal the referenced current revision before applying a state-sensitive side effect. Internal IDs and queue fields do not grant authorization by themselves.

Define a source-span table instead of loose unverified quotation strings: span ID, source ID/version, exact text range or segment references, and optional audio start/end milliseconds. Use Unicode character offsets consistently, not a mix of bytes and JavaScript code units; validate each span against its immutable source text. Generated descriptions must not be represented as verbatim quotations.

The planner's schema must include `plan_id`, `local_date`, `timezone`, `planning_epoch`, `input_revisions`, `assumptions`, `blocks`, `overflow_task_ids`, and `validation`. Each block has `task_id` or `event_id`, start/end instants, estimated-duration provenance where applicable, ordered next steps, a completion criterion, short rationale, and evidence references. A validation result is produced by application code; the model cannot mark its own plan as valid.

Date-only deadlines use a date field and the user's timezone; they are not secretly stored as midnight UTC. Define inclusive/exclusive end semantics, overdue handling, locale parsing, daylight-saving nonexistent/ambiguous local times, and the interpretation of relative phrases. Evaluate "tomorrow" against the original capture instant in its recorded timezone, not against a delayed worker's execution date. Ask for clarification where competing interpretations would materially change behavior.

Changes to dependency graphs must reject cycles. Duration estimates must be nonnegative, bounded and marked as user-supplied or inferred. Unknown fields are nullable; arbitrary low confidence does not justify guessing a valid-looking value. JSON-schema conformity is necessary but does not establish semantic truth.

### Model adapters and permitted tool behavior

Pick a currently available, inexpensive Bedrock model for extraction after evaluating the fixture set; Nova Micro/Lite are candidates rather than guaranteed best choices. Keep extraction, planning and embedding model IDs separate and configurable. The embedding baseline candidate is Titan Text Embeddings V2, using a supported output dimension and explicit normalization settings verified against its current API. Store model/dimension versions with vectors; never query an old index with incompatible embeddings.

Prefer native schema-constrained output when the selected model/API supports it. Otherwise use a supported tool-output or JSON-generation path, followed by server schema and semantic validation. Do not assume native structured output exists on every Bedrock model. Limit repair/escalation as specified in the cost block; never keep asking the model until any JSON-looking string appears.

The extraction model receives source content as data. It cannot call arbitrary tools, register for events, create shell commands or change infrastructure. Planning tools are narrow server-controlled operations such as `listEligibleObligations`, `getProjectHistory`, `retrieveSupportingMemories`, `getAvailability`, and `proposePlan`. Writes go through validated application services. No retrieved note can redefine permissions or override system/tool policies.

Save short decision rationales and sources, not private chain-of-thought traces. Build prompts from bounded typed inputs rather than concatenating all user history. Record prompt/schema versions so changes can be evaluated and rolled back.

### Capture and interpretation workflow

1. The user deliberately starts/stops recording. Show recording status, elapsed time, local upload state, and failure recovery. Avoid continuous microphone collection. Design for 10–120-second notes; enforce a five-minute/25-MiB maximum and supported media types before upload. The server validates the same limits.
2. Create a capture record and obtain an authorized upload location. Upload bytes directly to S3. The completion endpoint checks expected owner, object path, actual existence, and size. A duplicate completion call must not start another logical inference job; any necessary provider retry is bounded and counted.
3. Enqueue transcription. The speech worker returns text with timestamps and metadata through a shared contract. Text input bypasses speech; image input uses a configured vision/OCR adapter and retains the original image. If image interpretation is deferred to a later phase, show that explicitly in UI rather than faking image support.
4. Save the transcript before extraction. Speech errors can affect dates, names, and intent. Let the user correct a transcript, regenerate a proposal from that revision, or enter text manually.
5. Submit only the required transcript/window and a compact extraction schema to a small Bedrock model. Avoid retrieving the entire personal history for routine extraction. Tools remain disabled for this step; input content is data, not an instruction to execute commands.
6. Validate the result against the schema and perform application-level date/time/enum checks. At most one bounded repair attempt is allowed. If still invalid, retain the transcript, mark the proposal unresolved, and show a retry/edit path.
7. Save unambiguous user-requested entries under the configured capture policy and display what was created. Ask narrowly for missing critical information or conflicts. A tentative thought remains tentative without blocking capture. Do not require approval for every ordinary personal task save; require clarification when it affects actual timing or commitment meaning.
8. Transactionally update authoritative entities and write outbox events. Downstream indexing and scheduling happen asynchronously. Display `processing`, `needs_detail`, or `pending_schedule` accurately until the corresponding side effect succeeds.

Example: “There's a Bloomberg event in New York I want to attend. Remind me to register and help me prepare.” Extract event intent, location, requested registration follow-up, and preparation request. `starts_at`, reminder time, and registration state remain unresolved. Ask for the event/date and preferred reminder time or let the user save it to the inbox. Do not fabricate a registration deadline or current registration state.

### Scheduling, cancellation, and reliable follow-up

Database writes and EventBridge Scheduler changes cannot be treated as one atomic transaction. Use the transactional outbox: entity changes and requested side effects commit together; a separate dispatcher performs schedule synchronization. The dispatcher uses stable reminder identifiers, conditional updates, retries, and a dead-letter path. Each delivered change refers to a target revision. Reject an older outbox action when a newer revision already supersedes it.

Store scheduling progress and expose failures. A successful entity save with failed schedule creation means `pending_schedule` or `schedule_failed`, not “reminder set.” On cancellation or date changes, request deletion/update of the prior schedule and create the revised one. Superseded schedules may still fire during a race, so the due handler must independently validate freshness.

When a due job executes:

1. Read current reminder and target state using the appropriate consistency guarantees.
2. Verify owner, active status, target revision, actual due time, completion/cancellation, quiet-hours policy, and any user-defined suppression.
3. Claim the notification attempt using a conditional write keyed by reminder occurrence. A duplicate worker must not create another in-app notification.
4. Use a template for ordinary reminders. Only request generation when the selected reminder explicitly requires context, preparation, or replanning.
5. If generation is requested, reserve budget, retrieve scoped context, produce bounded content, and validate it. A model failure falls back to the ordinary reminder where possible.
6. Create a notification record and dispatch via the configured transport. Track acceptance/failure separately from a read receipt. Retry only according to policy and preserve the same deduplication identifier.

Do not claim exactly-once delivery across queues, a database, scheduler, and external push transport. Implement idempotency and make duplicate handling explicit. A push provider may accept a send even if its acknowledgment is lost; document this residual delivery ambiguity and never equate a push request with the user seeing it.

Recheck current cancellation/status immediately before external dispatch. Suppression is guaranteed for canceled/superseded jobs detected before the dispatch decision; a message already handed to an external transport may not be recallable. State this boundary accurately rather than promising cancellation can undo a notification already sent. Keep duplicate logical in-app records prevented while acknowledging external transport ambiguity.

### Daily planning and RAG behavior

Fetch obligations with structured queries first: fixed events, relevant incomplete tasks, due/overdue deadlines, dependencies, and available time. Use project/date/status indexes; do not rely on a vector search to discover every hard obligation. Historical records provide context but cannot override current authoritative status.

For each candidate task that needs context, retrieve scoped memories about prior investigations, approaches tried, blockers, and next steps. Apply owner/project/validity filters. Retrieve current project state directly when its identity is known. Semantic search helps recover differently worded thoughts; literal/metadata lookup handles names, filenames, and identifiers. Running both searches and fusing results is optional, not a fixed architecture requirement.

Embedding jobs run only for changed/new searchable content and reference its revision. The indexing worker verifies freshness before writing. A search result includes source ID, evidence text, revision, and timestamp; revalidate state-sensitive results before use. When vector indexing is delayed/unavailable, structured task views and scheduled reminders must continue working. Do not turn embedding similarity into truth, commitment priority, or factual confidence.

The planner receives a compact normalized context: fixed intervals, candidate tasks, duration estimates, dependencies, user preferences, and a small number of source-backed excerpts. It emits structured blocks and explanations. Deterministic code checks overlap, interval ordering, available capacity, prerequisite ordering, known deadlines, and task states. A plan that cannot fit all work explicitly shows overflow; it does not promise everything will be completed.

The plan is a suggested outline with editable estimates. Unknown duration can be represented with a visible default estimate. Protect time for travel/breaks only when configured or identified; if exact travel time is needed, require a supported source/lookup and do not invent it. Editing today's commitments invalidates a plan whose recorded input revisions have changed. Replan from current state, preserving explicitly user-pinned blocks where feasible.

Evaluate retrieval with the user's realistic cases: project resumption, forgotten decisions, exact identifiers, tentative ideas, and superseded deadlines. Compare structured/lexical/semantic and optional hybrid retrieval using held-out questions and the same final context budget. Record relevant-source recall, obsolete-source errors, latency, and cost. Do not add a reranker or knowledge graph without demonstrated benefit. Existing embedding/STT/generation models are neural networks; model training/fine-tuning is a later experiment with a defined dataset and held-out evaluation, not an MVP dependency.

### Resource limits and observable failures

Use environment-configured limits for audio duration, uploads, queue attempts, transcription wall time, extraction input/output tokens, generation context, retrieved sources, model-call timeout, daily per-user spend, and global spend. Validate limits centrally. Reserve a bounded estimated cost before a paid call using an atomic ledger update; concurrent workers must not independently pass the same remaining-budget check. Reconcile actual usage afterward, and treat uncertain failures conservatively. AWS budgets/alarms are monitoring, not a guaranteed real-time cutoff.

Rate-limit user requests and concurrency. Keep retry counts bounded; recognize throttling versus validation errors; use jittered backoff; send exhausted jobs to a DLQ with a user-visible error. Correlation IDs connect API, queue, model call, and notification logs. Never log source audio, entire transcripts, model prompts, sensitive URLs, tokens, or secrets by default. Use aggregate timings, token counts, model IDs, phase/error categories, and authorized diagnostic references.

Cache stable prompts only when the chosen model supports it and usage justifies it. Reuse unchanged embeddings. Save briefings with input revision references and invalidate when their inputs change. Do not rerun extraction, embedding, or planning merely because a page is refreshed. A deterministic reminder must incur zero language-model calls.

Use encryption in transit/at rest, narrow IAM permissions, private storage, owner-scoped data access, lifecycle policies, and a documented deletion path. Deleting an artifact must remove or tombstone associated derived text/vectors and reconcile reminders. Keep backups and audit retention explicit. The MVP does not send email, contact another person, register for events, buy tickets, or perform third-party account actions autonomously.

### End-to-end acceptance criteria

| Scenario | Required observable result |
|---|---|
| Clear spoken task with a deadline | Transcript and source-linked task appear; correct local-date interpretation; reminder schedule status is honest |
| Bloomberg thought without dates | Tentative event and requested follow-up are preserved; narrow missing-details question; no fabricated time or registration |
| Speech mishears a name/date | Original transcript remains reviewable; correction creates a new revision; downstream proposal is regenerated safely |
| Duplicate upload completion/message | One logical transcription/extraction job and one effective set of entities; any billed retries remain measured |
| Two simultaneous edits | One expected-revision update succeeds; the other receives conflict/review behavior; no silent lost update |
| Date changes before an old reminder fires | Old occurrence is suppressed by fresh revision/state check; new reminder follows the updated date |
| Event canceled or task completed | Pending and racing reminders are suppressed; retrieval does not describe it as active |
| Save succeeds but scheduler is unavailable | Data remains saved; UI shows schedule pending/failure; retry recovers without duplicate reminders |
| Vector index unavailable/stale | Task/calendar/reminder features remain usable; contextual answer indicates available sources and cannot resurrect stale obligations |
| No time to fit all work | Plan respects fixed intervals and capacity; overflow tasks and assumptions are visible |
| Ambiguous “next Friday” near midnight/DST | User timezone governs; ambiguous critical interpretations are clarified; no silent server-time substitution |
| Model returns malformed JSON or times out | Bounded retry/repair, retained capture, actionable state; no invented tasks |
| Budget exhausted by concurrent jobs | New paid work is blocked or queued according to policy; deterministic reminders continue |
| Push acknowledgment lost | Same notification identifier retained; retry ambiguity recorded; no exactly-once promise |
| Cross-user record/search attempt | Access denied and no metadata/source text leakage |
| User deletes capture/project data | Defined cascade/tombstone removes derived memory and reconciles future jobs; retention exceptions disclosed |

Use a controllable clock and model/speech/scheduler adapters for deterministic integration tests. Include at least one live development smoke test for speech, Bedrock extraction, scheduled invocation, and authenticated delivery after credentials are configured. Record measured transcription latency, extraction accuracy, and a priced sample workload; do not assert a universal under-$10 monthly cost or interactive speech latency before measurement.

<!-- METIS:END architecture -->

<!-- METIS:START speech -->
## Owned speech-to-text service

### Scope and neural model boundary

Build Metis's own cloud-hosted speech service around pretrained, open-source Whisper weights. Own the capture UX, upload API, validation, inference container, lifecycle, correction flow, deployment, metrics, and integration. Do not require OpenAI, Deepgram, AssemblyAI, ElevenLabs, or Amazon Transcribe API keys. AWS IAM credentials/roles remain necessary for AWS infrastructure and Bedrock usage. Download public model artifacts at build time, pin an artifact revision and checksum, and mirror them into an approved private artifact location for repeatable deployments. Do not accidentally select a gated model requiring another credential.

This is self-hosted neural inference, not training an original ASR neural network. Training from scratch is outside v1 and would require a labeled speech dataset, substantial training compute, and separate quality evaluation. Whisper is a pretrained Transformer speech model; use it as the ASR component. Embeddings and the Bedrock language model are separate pretrained neural components. Saving transcripts and editing mistakes does not automatically update model weights. Later adaptation may use consented corrections, with a separate training and validation plan.

Implement `SpeechTranscriber` as a deterministic adapter interface, not an autonomous LLM repeatedly reasoning over audio. A speech feature needs jobs, model inference, status, and errors; it does not need another chat agent or an orchestration framework inside the decoder. Metis's planner should receive finalized text, never raw model confidence as evidence that an event or commitment is true.

### Default engine and runtime

Use Python 3.12, faster-whisper/CTranslate2, CPU execution, and INT8 inference as the default. Pin compatible dependency versions rather than installing `main` during deployment. Start by benchmarking `small.en` for English recordings; include `base.en` as the faster, lower-resource comparison. Treat the selected default as provisional until the project-specific evaluation passes. Avoid claiming model speed or accuracy from another machine's benchmark. Support configurable language and multilingual checkpoints; English-only checkpoints are not appropriate for arbitrary languages. Preserve the spoken language by default; translation is a separate feature.

Initial configuration: CPU threads bounded by allocated vCPUs, one inference job per container, beam size 1 as the inexpensive baseline, temperature 0, VAD enabled, and configurable previous-text conditioning. Compare beam size 5 and conditioning changes only when evaluation demonstrates useful gains. Load model files from a local directory baked into the image or a controlled artifact cache; never download them anew from the public internet on every transcription. Persist model identifier, artifact revision, library version, compute type, and decoding parameters with every result.

Alternative engine: whisper.cpp for a smaller native runtime or a future optional on-device implementation. Keep one production engine in v1; do not make two speech engines a prerequisite for shipping. An optional device-based mode may reduce cloud usage but is not the cloud default and must not impose model inference on the user's computer unexpectedly.

### Browser capture and upload

V1 uses push-to-talk/record, stop, then whole-recording upload. Display an unmistakable recording state, elapsed time, cancel, and stop. Request microphone permission only after the recording action. Handle permission denial, unavailable device, interruption, empty recording, unsupported codec, and network loss. Release microphone tracks after stop or cancellation. Provide a text input alternative.

Use `getUserMedia` and `MediaRecorder` on HTTPS. Negotiate an actual supported MIME type with `MediaRecorder.isTypeSupported`; do not assume every browser produces WebM/Opus. Test at least Chromium WebM/Opus and Safari-supported recording output. Collect the complete ordered blob before upload. A `timeslice` is not a guarantee that each emitted fragment is independently decodable.

Typical v1 capture is 10–120 seconds, with a hard maximum of five minutes and 25 MiB encoded upload, both configurable and enforced server-side. These are product limits chosen for short thought capture, not upstream model limits. Do not implement an always-on microphone or continuous cloud transcription loop. Let users retain and retry a failed recording during the session. Explicitly tell them if navigating away will discard it; use the bounded IndexedDB capture outbox required by the frontend section for recoverable unsent drafts, while showing eviction/storage failures honestly.

The authenticated API issues a short-lived presigned S3 upload permission scoped to an owner-specific generated key. Generate object paths on the server. Use a signed content-length policy when available, then recheck stored size and metadata before processing. Validate object ownership and actual object bytes, not just a filename or submitted MIME header. Never send full audio through Lambda invocation JSON or a Bedrock text prompt. Store large bytes in private object storage and pass references.

The primary job input contract contains `recording_id`, `user_id`, `source_version`, and `s3_key`, plus an idempotency key and correlation metadata. The server derives or validates `user_id` against the authenticated owner rather than trusting client input. Reject a key outside that owner's prefix and a version that no longer matches the current recording. Canonical operations: `POST /v1/captures` with kind `audio` returns the capture ID and upload instructions; `POST /v1/captures/{id}/complete` validates the completed object and schedules work; `GET /v1/jobs/{job_id}` returns state and result references; `PATCH /v1/captures/{id}/transcript` creates a user-edited transcript revision. Every API requires authenticated ownership checks and an idempotency key where it creates work. Align operation names with the coordinator's shared contracts before implementing; workers may not invent a second schema.

### Cloud job execution

Practical cost-first default: finite ECS Fargate Linux CPU tasks, initially benchmarked at 2 vCPU/4 GiB, started by the pipeline for pending speech jobs and stopped after completion. This is an asynchronous UX: display queued, starting, and transcribing rather than promising instant text. Image pull, process startup, model loading, decoding, and inference contribute to latency. Measure each independently.

Use the pipeline's durable job record and Step Functions Standard `ecs:runTask.sync` integration for task completion/retry handling. If SQS buffers ingestion, let the dispatcher acknowledge its message only after durable workflow handoff. Deduplicate workflow starts and job execution using stable IDs and conditional state transitions. Do not mark the entire transcription successful merely because `RunTask` returned. Verify the worker persisted a finalized result, and inspect launch failures and container exit codes. If workers consume SQS directly instead, use renewable visibility plus a durable job lease and delete messages only after successful persistence. Select one pattern for v1; do not implement both.

Cap personal-account speech concurrency initially at one active task. Bound retries, route permanent failures into an actionable error state, and use a DLQ where queues apply. A cancellation or deletion increments the capture revision so late job results cannot resurrect it. The pipeline's existing limits must reject or defer excess launches rather than letting concurrent agents create uncapped compute. Do not create one endlessly idle container per user.

Fargate currently supports CPU execution, not GPUs. GPU acceleration would require a separately evaluated EC2/ECS GPU capacity strategy. Do not provision GPU instances for v1 by default. A model-loaded long-running CPU service is a later option for steady usage or tighter latency; its idle compute is billable. EC2 scale-to-zero also has startup, image/model loading, management, and capacity constraints. Spot capacity is interruptible and needs job retries.

Standard Lambda is suitable for control operations, not the selected v1 ASR worker. Its published limits include a 15-minute function timeout, 10 GiB maximum container image, 10,240 MiB maximum memory, and configurable temporary storage. Native decoding dependencies, model loading, cold starts, memory demand, and payload limits require benchmarking if ASR is moved there. Current Lambda Managed Instances have different supported execution limits; do not silently apply those to ordinary Lambda or add another hosting mode without a decision record.

### Decode, results, and extraction ordering

Decode in the isolated worker using PyAV or a pinned media toolchain. Reject corrupt, unsupported, overlong, oversized, or pathological files with bounded decoder time and memory. Treat metadata duration as untrusted; enforce decoded duration. Convert to the model's required mono/sample-rate representation. Do not create shell commands from user filenames, accept arbitrary URLs, or expose an unauthenticated transcription server. Clean temporary audio on completion/failure; set a short configurable retention policy for source recordings.

Job states: `uploading`, `queued`, `starting`, `transcribing`, `ready`, `failed`, `canceled`. Return timestamps, attempt count, error code, and optional retry guidance. A `ready` result contains original ASR text, current edited text, text revision, language, ordered segments, segment start/end times, model metadata, and processing metrics. Keep timing fields separate from semantic event dates. Word-level timestamps are optional; do not compute them unless the UX uses them.

Do not expose decoding log-probabilities or no-speech scores as calibrated probabilities that words are correct. Use conservative review hints only after evaluation. All transcripts remain editable. A configured project glossary may provide a short contextual prompt or alias list; it must not overwrite the raw ASR output or turn expected names into fabricated speech. Do not use an LLM to silently rewrite dates, names, negation, or commitments.

Emit exactly one logical `transcript.finalized` event per accepted text revision. The ingestion pipeline can automatically extract tentative structured proposals from finalized ASR output. A user correction creates a new revision and supersedes pending proposals from the old revision. Material facts such as uncertain dates or reminder times require explicit resolution before scheduling. During later streaming experiments, partial hypotheses must remain provisional: no reminders, irreversible actions, or durable task commitments may originate from partial text. Include revision and idempotency keys in every downstream event.

### Cost accounting and operational limits

An open-source decoder removes per-minute speech-provider charges but does not make cloud inference free. Track billed runtime, vCPU/memory settings, cold-start count, object storage/requests, orchestration transitions, queue requests, ECR storage, logs, network egress, and networking infrastructure. Fargate Linux charges run from image pull through task termination with a one-minute minimum; public IPv4 and surrounding services add charges. Avoid accidentally introducing a fixed-cost NAT gateway solely to fetch public model files at runtime. Document the selected network path and its costs.

Calculate monthly compute as the sum of measured billed seconds multiplied by chosen regional vCPU and memory rates. Include startup in that measurement. Report an idle estimate and workload scenarios such as 30, 300, and 600 short recordings per month; never promise under $10 from file count or a free-tier assumption. Compare burst tasks and a warm service using measured duration and the actual rate card. Set maximum recording duration, concurrency, retries, and daily inference allowance; a monthly AWS Budget alert is not a hard spend cap. Reuse immutable model artifacts. Re-transcribe only changed audio, and never rerun STT just because the planner generated another daily outline.

### Acceptance and evaluation

Ship a small consented or synthetic-fixture evaluation set with 20–30 short recordings covering clean speech, ordinary noise, fast speech, pauses, project names such as Cerno and Themis, Bloomberg/New York, spoken dates, negation, and silence/non-speech. Provide human reference transcripts. Measure WER with a documented normalization rule plus separate exact fidelity for names, date expressions, and negation. Ordinary WER can hide errors that matter to scheduling. Keep part of the set held out while tuning model parameters. Do not make a universal accuracy promise from this pilot.

Required verifications: actual neural transcription succeeds without an external STT API credential; unsupported/corrupt and excessive audio fail safely; an empty/no-speech fixture creates no task or reminder; duplicate completion requests produce one logical job; retries do not duplicate transcript events; another user cannot read audio or jobs; cancel/delete defeats a late result; an edit supersedes old extraction proposals; source recording expiry does not delete the text unexpectedly; task failures become visible in the UI; queue/startup/inference latency and cost measurements are recorded. Use real recorded browser-format fixtures so mocks cannot conceal codec failures. Record cold and warm measurements separately. Unit tests verify job and revision invariants; one container integration test verifies actual model decoding.


<!-- METIS:END speech -->

<!-- METIS:START retrieval -->
## Memory and retrieval design

### Separation of responsibilities

The authoritative task/event database answers exact questions about deadlines, completion, cancellation, ownership and current versions. Semantic memory supplies supporting context: why a task exists, what was tried, what the user intended, and which prior thoughts relate to it. Retrieved memory must not override newer authoritative state.

Expose separate operations for `getCurrentTaskState`, `listEligibleObligations`, `getProjectHistory`, `lookupIdentifier`, and `retrieveSupportingMemories`. Keep retrieval providers behind a stable interface so a vector-store change does not rewrite planning logic.

### Ingestion

1. Persist the source and its version.
2. Extract typed records with source IDs and source spans.
3. Create short memory units by meaning: observation, attempt, decision, blocker, idea, intention or preference. Do not embed every JSON property as if it were prose.
4. Include enough surrounding source context to preserve negation, conditionals and the distinction between an idea and a decision.
5. Compute embeddings only for new or materially changed semantic text. Store embedding-model ID/version, dimensions, content hash and source version.
6. Index with server-enforced `user_id`, optional `project_id`, memory type, source/version and current/historical status metadata.
7. Treat indexing as asynchronous. The latest task record must remain usable before its vector update completes.

Keep raw source text available for citations. A memory summary cannot become the only surviving evidence. If chunking loses "do not" or "we abandoned this", repair the chunking design before changing search algorithms.

### Baseline retrieval

Use structured lookup when fields are known. Use project/time filters to scope history. Use dense semantic retrieval for paraphrased thought recall. Use explicit identifier fields and aliases for project names, filenames, error codes and task IDs; ordinary full-text tokenization may not preserve every literal identifier.

For the initial cloud semantic store, use direct S3 Vectors behind `MemoryStore`, with a Bedrock embedding model supported in the selected region. Direct indexing provides control over source updates. Bedrock Knowledge Bases with a chosen vector store is an alternative, not a second mandatory RAG engine to pay for alongside direct retrieval.

Self-managed Knowledge Bases backed by S3 Vectors support semantic retrieval; do not assume their hybrid-search or metadata capabilities equal every other store. AWS capabilities change: verify current official limits and APIs before coding. Represent a provider's restrictions in an adapter capability map rather than in vague comments.

Default candidate limits are project-tunable configuration, not claims of optimality: retrieve up to 20 candidate memory units, validate ownership/source versions/currentness, deduplicate related spans, and supply at most 6 evidence items per task. Enforce a total evidence token budget across the plan; avoid multiplying the per-task maximum by dozens of tasks.

### Query planning

"Plan my day" should not become one unscoped embedding search. Read availability and eligible obligations first. Select a bounded number of candidate focus tasks through deadline/dependency/explicit-priority rules. Retrieve evidence for tasks with missing resumption context. Reuse an already-current work packet where possible.

When the task is "resume Cerno", retrieve that project's current state and relevant chronological attempt history before searching broadly. If a new thought refers to "that caching approach" without a project name, use semantic search with the user's scope, then ask a targeted clarification if several projects fit.

Retrieval scores are ranking signals, not calibrated probabilities, factual certainty, urgency or user priority. Recency alone also does not establish validity: an older uncompleted deadline can be more important than a new casual idea.

### Evidence and conflict handling

- Every evidence item includes `source_id`, `source_version`, `span_ref`, `memory_id`, and a resolvable source link.
- Hydrate candidate IDs against authoritative source/task state. Drop deleted, inaccessible, superseded-as-current or mismatched-version candidates.
- Historical failed attempts can be retrieved as historical evidence, labeled accordingly; do not filter away all history needed to explain a blocker.
- Preserve a chain of superseding decisions. The current decision is explicit; past alternatives remain inspectable.
- If records conflict without a clear update relation, flag the conflict and ask a compact clarification instead of silently choosing.
- If evidence is insufficient, return a bounded answer with missing information. Do not add invented project details to make a plan look complete.
- Reject cross-user memory before constructing any model prompt, even if the store unexpectedly returns it.

### Retrieval experiments

Hybrid retrieval is a feature flag backed by an experiment. Compare filtered dense retrieval, lexical retrieval and hybrid fusion on the same labeled corpus. Compare a reranker only after candidate recall has been examined. A reranker cannot recover an item absent from its candidate list.

Start with 40 synthetic or consented real queries covering paraphrase, exact identifier, project/time lookup, obsolete decisions, ambiguous references, missing memory, and changed commitments. Split development questions from held-out evaluation questions. Fix generator settings and final evidence budgets for comparison; also report retrieval/reranking cost and latency so added computation is visible.

Report Recall@k, useful top-result ranking, stale-evidence admission, source-link validity, cross-user leakage, and downstream plan defects. Adopt hybrid or reranking only when repeatable improvements justify measured overhead. Do not copy a universal dense/sparse weight or cosine threshold from an unrelated benchmark.

### Preference feedback

Capture accepted, edited, deferred and rejected recommendations with reason where supplied. Use explicit rules first, such as the user preferring one morning focus block. A later trained ranking model is optional. Store feedback separately from raw private notes and provide deletion/export for both.
<!-- METIS:END retrieval -->

<!-- METIS:START interface -->
## Frontend and interaction design

### Stack and design direction

For a new project, use React, TypeScript and Vite with a PWA service worker and Tailwind CSS. Reuse a small accessible component library only if needed; do not install overlapping UI systems. Pin actual compatible versions after inspecting current official documentation and the repository. Keep provider calls, credentials and pipeline control off the client.

The visual direction is a calm, compact workspace with readable type, a clear daily outline, a prominent capture control and a small set of surfaces. Use a restrained neutral palette, one accent, deliberate spacing and useful hierarchy. Accessibility, processing clarity and recoverability matter more than ornamental animation. Prefer content-rich work packets and plans to a grid of generic statistics cards.

The exact phrase requested by the user is "code munch ponytail frontend design skills". It has not been resolved to installed skills in this preparation environment. In the implementation environment:

1. Inventory installed skills/MCP tools and repository-local skill files.
2. Find exact or confidently identified matches; read the actual `SKILL.md` before applying one.
3. Use an available frontend-design skill for interface work, consistent with this product specification.
4. jCodeMunch may be the intended code exploration tool; it is a symbol retrieval MCP tool, not a frontend-design skill. Use it only when installed and licensed appropriately for the project.
5. "Ponytail" remains unresolved. Do not silently relabel it Tailwind or claim it was used.
6. If an optional tool is missing, continue with the explicit design requirements and record the gap. Do not install unknown plugins, add a paid service, or replace the product architecture merely to satisfy an ambiguous tool name.

An official Anthropic frontend-design skill is listed in the sources as a possible reference; listing it is not a claim it is installed or that the user meant that exact package. Use the available environment's skill mechanism rather than copying arbitrary external instructions into product code.

### Main surfaces

**Today:**

- Display today's date in the selected user timezone.
- Show fixed appointments separately from flexible work.
- Show a sequenced daily outline with estimated durations, source-backed reasons and clear next actions.
- Present no more than the configured number of major focus tasks initially, with remaining work in a visible backlog/overflow area.
- Provide accept, adjust, defer, complete and regenerate-with-changes actions.
- Make missing availability explicit. Do not pretend free time has been discovered from an unconnected calendar.
- Expose the latest plan revision and explain material changes in one short sentence.

**Capture:**

- One primary microphone button with record, pause/cancel, stop and upload states.
- Typed capture is equally functional and remains available if audio fails or costs are capped.
- Show recording duration and the upload limit before the limit is reached.
- Show a visible queued/transcribing/extracting status instead of freezing the screen.
- Present finalized transcript corrections before committing uncertain extracted fields.
- Display extracted tasks, events, ideas and decisions in compact editable cards.
- Resolve missing fields with small contextual controls, not a large mandatory form for every note.
- Keep content local only as needed for recording/offline outbox; never silently discard unsent input.

**Projects / resume:**

- Project overview with current objective, active blocker, next action and pending decisions.
- A work-packet view with attempted approaches and completion criteria.
- A source-linked timeline of captured context and changes.
- "Resume for 30 minutes" and "Update what happened" actions.
- Memory links open the exact supporting note/span; do not send the user to an unrelated top-level folder.

**Inbox / clarify:**

- Captured items that need date, project, commitment or reminder clarification.
- Pending confirmations grouped by source.
- Missing information is distinct from processing errors.
- User can leave an idea unresolved without constant reminders to resolve it.

**Tasks and events:**

- List and detail views with status, deadlines, dependencies, project, source and version history.
- Complete, cancel, snooze, reschedule and archive controls with immediate visible state updates.
- Registration status and attendance status are separate.
- Fixed event times and flexible preparation tasks are visually distinguishable.

**Settings / cost:**

- Timezone, quiet hours, active hours, focus preferences and notification permission.
- Reminder channel and opt-in follow-up frequency.
- Current provider capabilities, local/cloud mode, speech model selection and cost limit.
- Usage in voice minutes, model tokens, job count and estimated spend; mark estimates explicitly.
- Export/delete source data and generated memories; explain any pending deletion job clearly.
- Technical internals remain in a diagnostics panel, not in ordinary product steps.

### Interaction state requirements

Implement and visibly distinguish: idle, microphone permission denied, recording, canceled recording, upload pending, offline, queued, transcribing, transcript ready, extraction pending, clarification required, saved, failed, retrying, provider unavailable, budget exceeded, notifications disabled, no relevant memory, and plan conflict.

Partial results must be labeled partial. Uploaded audio and a created record are different states. "Reminder scheduled" requires a confirmed schedule synchronization result; before that say "Scheduling reminder" or "Schedule needs retry".

Web push requires permission and service-worker/browser support. A closed PWA can receive push on supported devices, but delivery is not a universal platform guarantee. Detect unsupported environments, retain an in-app notification feed, and surface a delivery problem without claiming an undelivered message reached the user. Native OS notifications or email/SMS can be later opt-in adapters.

### Accessibility and responsive behavior

- Full keyboard operation, visible focus, accessible labels and properly announced asynchronous status.
- Do not make voice the only input or color the only status indicator.
- Honor reduced motion and avoid unnecessary audio playback.
- Mobile capture controls remain reachable with one hand and do not cover the transcript editor.
- Support small mobile, tablet and desktop widths without horizontal overflow.
- Use semantic structure and sufficient contrast; test dialogs, recording controls, task actions and date controls.
- Preserve the user's unfinished edit during background updates. Show a conflict if the underlying version changed rather than overwriting the edit.

### Offline and authentication

Local offline capture uses an outbox with stable client IDs and bounded storage. Sync with idempotency keys after reconnection. Never create duplicate tasks because an upload was retried. Protect locally retained personal data appropriately for the platform; clear it on sign-out when configured. Browser storage eviction is possible, so communicate unsynced status rather than promising durable storage forever.

Use authenticated user identity from the server session/token. Never trust a request-body `user_id` for ownership. A local fixture login is permitted only in explicit development mode; production must not fall back to it when authentication fails.
<!-- METIS:END interface -->

<!-- METIS:START cost -->
## Cost, credentials and runtime token controls

### Minimal external credentials

Use AWS SDK default credential resolution locally through a profile/SSO, and IAM roles in deployed services. Do not put AWS credentials or Bedrock calls in frontend code. V1 requires no Anthropic direct API key, no OpenAI key, no commercial speech API key, no Pinecone key, and no mandatory Google/Microsoft calendar key.

Authentication and web-push signing have configuration material. These are not additional paid AI provider keys. The infrastructure agent generates/configures them securely where required and documents their purpose. A push adapter can use standard VAPID signing; no separate paid push SaaS is required for the PWA prototype.

Local demonstration mode must run with fixture inference/transcription and a local deterministic store without any cloud account. Production mode must use real adapters and explicitly fail if a required capability is unavailable. A mock response must never masquerade as completed production transcription or delivery.

### Billing model

Do not hard-code historical prices as eternal facts or repeat unsupported claims that serverless means free. At implementation/deployment time, verify the selected AWS region, service generation, instance/task configuration, model ID, inference mode and free-tier conditions.

The monthly estimate must include:

`ASR task CPU + ASR task memory + image/model download/startup + language inference input/output + embeddings + vector upload/storage/query + task DB/storage/requests + source object storage/requests + queues/scheduler + API/frontend hosting + notifications + logs/metrics + network + optional agent hosting`.

Use a workload sheet with actual assumptions: recordings per day, recording lengths, task startup and processing time, model prompt/output lengths, plan requests, memory updates, retained audio days, vector count, query frequency and retry rate. No single-user label implies low cost by itself.

The previous commercial transcription estimate does not apply to the owned ASR implementation. Self-hosted ASR avoids a speech-provider per-minute fee but incurs cloud compute and startup charges. Benchmark CPU throughput and cold starts before claiming it is cheaper for the actual usage pattern.

Current AWS documentation distinguishes Bedrock Managed Knowledge Base from self-managed Knowledge Bases, and OpenSearch Serverless NextGen from Classic. Aurora Serverless can support auto-pause to zero on suitable configurations. Do not deploy an idle-cost service because of a stale example, or assume every service configuration scales to zero. S3 Vectors is the baseline candidate for this project, subject to actual feature/latency needs.

### Initial runtime limits

These are conservative proposed defaults and must be configurable, documented and measured:

| Operation | Initial limit / policy |
| --- | --- |
| Audio upload | 5 minutes, 25 MiB maximum; typical 10–120 seconds |
| Extraction | 1 model call, bounded source/context, up to 1 schema-repair retry |
| Ambiguous extraction escalation | At most 1 stronger-model call if enabled and budget reserved |
| Candidate evidence | Up to 20 candidates, at most 6 admitted items per selected task |
| Daily plan | At most 8 candidate tasks for expensive contextual preparation; obligations outside that set remain visible in the constraint/overflow input |
| Model input | Start with 4,000 tokens for extraction and 8,000 for planning; count actual model/provider tokens and handle oversize inputs deliberately |
| Model output | Start with 1,200 tokens for extraction and 2,000 for plans; cap output and handle truncation |
| Runtime tool loop | At most 4 iterations per planning request; deterministic worker path has no open-ended loop |
| Ordinary reminder | No LLM call needed for a templated notification |
| Semantic indexing | Only changed units; bounded asynchronous batch size |
| Retry | Bounded by error class, per-job attempt limit and DLQ; no unbounded retries |

Long notes may contain more obligations than a single output can represent. Never silently truncate them to satisfy token caps. Persist the original, split into semantically coherent source ranges with a bounded maximum batch count, deduplicate extractions, and visibly defer excess processing when a configured budget would be exceeded. Record per-range completion so retries do not restart all completed ranges.

Budgets must be enforced before requests, not only displayed afterwards. Reserve a conservative upper-bound cost/usage allocation atomically, then settle against actual usage. Concurrent workers must not all pass a stale read of the same remaining budget. Release only allocations known to be unconsumed, such as a request that was never started. A failed/timed-out request may still be billed: retain a conservative charge/reservation when usage is uncertain, and reconcile it through an idempotent path when authoritative usage becomes available. Track infrastructure cost estimates separately from exact token usage; the model-call cap is not an absolute cap on every AWS bill.

At the configured cap, keep manual task edits, source access, typed capture, existing reminders and deterministic fallback planning working. Defer optional embedding/replanning jobs with a visible status. Do not silently switch to an expensive provider or ignore a user cap.

### Reuse and caching

- Save unchanged embeddings keyed by semantic content hash and embedding model/version.
- Reuse a work packet or event briefing when its relevant source/task/preference versions have not changed.
- Reuse tool results with a TTL appropriate to freshness, including date-sensitive travel/event information.
- Use stable prompt prefixes for instructions, schemas and tool definitions where the selected model supports caching. Check model/API eligibility, minimum token length, TTL and cache-write/read prices.
- Measure cache hit rates. An infrequent personal workflow may receive little benefit from short-lived prefix caching.
- Include source/task versions, provider/model/prompt version and ownership scope in generated-result cache keys. Invalidate on material updates.
- Never share user-specific cached responses across users. Do not cache missing permissions as authorized access.

### Runtime measurement

Record per-operation model ID, input/output/cache tokens when available, prompt version, evidence count/token total, tool iterations, estimated cost, latency, retry count and outcome. Do not log raw private sources, audio, access tokens or credentials by default. Metrics must be sufficient to compare simple retrieval against hybrid/reranking and cold ASR against an always-warm alternative.

A small initial budget target can be configured, but every budget report must distinguish measured inference usage, estimated infrastructure cost, free-tier assumptions and unmeasured components. AWS budget alerts can be an additional account-level mechanism; they do not substitute for application-enforced usage limits.
<!-- METIS:END cost -->

<!-- METIS:START development -->
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


<!-- METIS:END development -->

<!-- METIS:START delivery -->
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
<!-- METIS:END delivery -->

<!-- METIS:START sources -->
## Primary references and capability verification

The architecture and project policies above are proposed Metis design decisions. The references below ground provider/tool capabilities, not a universal best architecture or a fixed future price. Relevant documentation was checked during preparation on 2026-10-05. Recheck a source when its capability materially affects the chosen implementation; avoid re-browsing every link on every agent turn.

### AWS inference, retrieval and infrastructure

- [Bedrock Agents Classic maintenance status](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-classic-maintenance-mode.html): new-project planning should account for the closure to new customers and optional AgentCore path.
- [Bedrock structured outputs](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html): model/API support and supported schema subset need explicit checks.
- [Titan text embeddings](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html): embedding model IDs, dimensions and limits.
- [S3 Vectors and Bedrock Knowledge Bases](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-bedrock-kb.html): integration capabilities and limits; direct vector retrieval and Knowledge Bases are alternative implementation paths.
- [S3 Vectors metadata filtering](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-metadata-filtering.html): ownership and project metadata filters, limits, and supported operations.
- [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/): distinguish managed and self-managed Knowledge Bases, model inference and optional features.
- [S3 pricing](https://aws.amazon.com/s3/pricing/): ordinary object storage and vector storage/query/upload have separate billing dimensions.
- [Lambda pricing](https://aws.amazon.com/lambda/pricing/): requests, execution duration and free-tier limits do not imply every personal app is free.
- [Lambda quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html): verify ordinary Lambda and any separately selected hosting modes independently.
- [EventBridge schedule types](https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html): one-time/recurring scheduling, timezones and invocation precision.
- [ECS/Fargate tasks through Step Functions](https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html): durable task completion; successful launch is not completed inference.
- [Fargate pricing](https://aws.amazon.com/fargate/pricing/) and [FAQ](https://aws.amazon.com/fargate/faqs/): CPU/memory runtime, startup billing, minimums and GPU availability.
- [OpenSearch pricing](https://aws.amazon.com/opensearch-service/pricing/): distinguish NextGen and Classic collection compute policies before considering an alternative store.
- [Aurora auto-pause](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html): supported configurations may pause compute; remaining charges and constraints still matter.

### Speech and browser input

- [Whisper model and implementation](https://github.com/openai/whisper): pretrained neural ASR, supported models and license.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper): CTranslate2 implementation, quantization and decoding parameters; published machine-specific benchmarks are not Metis performance guarantees.
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp): optional alternative only after a measured architectural decision.
- [MediaRecorder](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder): recording and platform-dependent media output behavior.

### Claude Code and optional development tools

- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents): role contexts, project agent definitions and supported delegation capabilities.
- [Claude Code agent teams](https://code.claude.com/docs/en/agent-teams): verify experimental availability, independent contexts and limitations.
- [Claude Code worktrees](https://code.claude.com/docs/en/worktrees): verify base selection and installed-version behavior; explicit Git worktrees remain a fallback.
- [Claude Code skills](https://code.claude.com/docs/en/skills): role-specific procedure loading and supporting references.
- [Claude Code cost management](https://code.claude.com/docs/en/costs): focused tasks, scoped context and aggregate multi-agent usage.
- [jCodeMunch](https://github.com/jgravelle/jcodemunch-mcp): optional symbol retrieval; check current license and installed tool names. Do not repeat marketing savings percentages as a guaranteed Metis result.
- [Anthropic frontend-design skill](https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md): optional design reference if installed/selected. It does not resolve the unidentified "Ponytail" skill name.

## Start now

Begin with repository inspection and the contracts-first bootstrap. Maintain the implementation ledger, three-minute safe checkpoint cadence and bounded agent assignments. Build and validate the complete Metis workflow through the phase gates. When blocked, finish independent authorized work and report the specific blocker. Keep actual implementation and measured evidence aligned with this specification; update an architecture decision when a justified change is required.
<!-- METIS:END sources -->
