<!-- Extracted from prompt (1).md; block `product`. Do not edit here; edit the source spec. -->

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
