<!-- Extracted from prompt (1).md; block `architecture`. Do not edit here; edit the source spec. -->

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
