<!-- Extracted from prompt (1).md; block `interface`. Do not edit here; edit the source spec. -->

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
