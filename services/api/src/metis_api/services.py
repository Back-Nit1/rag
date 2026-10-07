"""Application services behind the HTTP layer.

These functions hold the behaviour; the route modules only parse, authorise and
serialise. The same functions run under Lambda, under uvicorn and in tests,
which is what keeps the local and cloud paths honest about each other.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from metis_contracts import (
    ErrorCode,
    Event,
    EventType,
    JobState,
    MetisError,
    OutboxEvent,
    Plan,
    PlanStatus,
    Preferences,
    SourceArtifact,
    SourceKind,
    SourceLifecycle,
    SourceSpan,
    Task,
    TaskState,
    new_id,
    settings,
)
from metis_contracts.api import (
    CaptureDetail,
    CompleteCaptureRequest,
    CompleteCaptureResponse,
    CreateCaptureRequest,
    CreateCaptureResponse,
    PatchEventRequest,
    PatchPreferencesRequest,
    PatchTaskRequest,
    PatchTranscriptRequest,
    TodayResponse,
    UploadInstructions,
)
from metis_contracts.common import Page
from metis_contracts.entities import ExtractionProposal, Job, TranscriptRevision
from metis_contracts.enums import TASK_OPEN_STATES

from .deps import RequestContext

log = logging.getLogger("metis.api.services")


def _outbox(
    ctx: RequestContext,
    *,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    target_id: str | None = None,
    target_revision: int | None = None,
) -> OutboxEvent:
    now = ctx.adapters.clock.now()
    return OutboxEvent(
        id=new_id("obx"),
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        event_type=event_type,
        target_id=target_id,
        target_revision=target_revision,
        correlation_id=ctx.correlation_id,
        idempotency_key=idempotency_key,
        payload=payload,
        available_at=now,
    )


def _job(
    ctx: RequestContext, *, job_type: str, target_id: str, target_revision: int, idempotency_key: str
) -> Job:
    now = ctx.adapters.clock.now()
    return Job(
        id=new_id("job"),
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        job_type=job_type,
        state=JobState.QUEUED,
        target_id=target_id,
        target_revision=target_revision,
        correlation_id=ctx.correlation_id,
        idempotency_key=idempotency_key,
        adapter_mode=ctx.adapters.inference.mode,
    )


# --------------------------------------------------------------------------- #
# captures
# --------------------------------------------------------------------------- #


def create_capture(ctx: RequestContext, request: CreateCaptureRequest) -> CreateCaptureResponse:
    """Save the source immediately. Audio additionally gets an upload target.

    A text capture is complete on arrival and goes straight to extraction. An
    audio capture is only `pending_upload` — nothing downstream runs until the
    completion endpoint has verified the stored bytes.
    """
    limits = settings().limits
    clock = ctx.adapters.clock
    now = clock.now()

    if request.kind is SourceKind.AUDIO:
        if not limits.audio_type_allowed(request.content_type):
            raise MetisError(
                ErrorCode.UNSUPPORTED_MEDIA,
                f"{request.content_type} is not a supported recording format",
            )
        if (request.expected_bytes or 0) > limits.max_upload_bytes:
            raise MetisError(
                ErrorCode.UPLOAD_TOO_LARGE,
                f"the recording would be {request.expected_bytes} bytes, over {limits.max_upload_bytes}",
            )
        if (request.duration_seconds or 0) > limits.max_audio_seconds:
            raise MetisError(
                ErrorCode.LIMIT_EXCEEDED,
                f"recordings are limited to {limits.max_audio_seconds} seconds",
            )
    if request.kind is SourceKind.TEXT and len(request.text or "") > limits.max_text_chars:
        raise MetisError(ErrorCode.UPLOAD_TOO_LARGE, "the note is too long")
    if request.kind in (SourceKind.IMAGE, SourceKind.DOCUMENT):
        # Stated plainly rather than faking support.
        raise MetisError(
            ErrorCode.UNSUPPORTED_MEDIA,
            f"{request.kind.value} capture is not implemented yet; text and audio are supported",
        )

    # The offline outbox retries with a stable client id; one source results.
    if request.client_capture_id:
        existing = _find_by_client_id(ctx, request.client_capture_id)
        if existing is not None:
            return CreateCaptureResponse(capture_id=existing.id, revision=existing.revision)

    capture_id = new_id("src")
    source = SourceArtifact(
        id=capture_id,
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        kind=request.kind,
        lifecycle=SourceLifecycle.PENDING_UPLOAD
        if request.kind is SourceKind.AUDIO
        else SourceLifecycle.READY,
        capture_timezone=request.capture_timezone,
        captured_at=request.captured_at,
        duration_seconds=request.duration_seconds,
        client_capture_id=request.client_capture_id,
        current_version=1,
        retention_expires_at=None,
    )

    upload: UploadInstructions | None = None
    if request.kind is SourceKind.AUDIO:
        ref, instructions = ctx.adapters.source_store.upload_target(
            ctx.user_id,
            capture_id=capture_id,
            content_type=request.content_type or "audio/webm",
            max_bytes=limits.max_upload_bytes,
            ttl_seconds=limits.upload_url_ttl_seconds,
        )
        source = source.model_copy(update={"object_ref": ref})
        upload = UploadInstructions.model_validate(instructions)

    if request.kind is SourceKind.TEXT:
        text = request.text or ""
        source = source.model_copy(
            update={
                "transcripts": [TranscriptRevision(source_version=1, text=text, created_at=now)],
                "spans": [
                    SourceSpan(
                        span_ref="span_full",
                        source_id=capture_id,
                        source_version=1,
                        start_char=0,
                        end_char=len(text),
                    )
                ],
            }
        )

    with ctx.state.transaction(ctx.user_id) as tx:
        stored = tx.put("source", source)
        if request.kind is SourceKind.TEXT:
            tx.append_outbox(
                _outbox(
                    ctx,
                    event_type=EventType.TRANSCRIPT_FINALIZED,
                    target_id=stored.id,
                    target_revision=1,
                    payload={
                        "recording_id": stored.id,
                        "source_version": 1,
                        "text_length": len(request.text or ""),
                        "edited_by_user": False,
                    },
                    idempotency_key=f"transcript:{stored.id}:1",
                )
            )

    return CreateCaptureResponse(capture_id=stored.id, revision=stored.revision, upload=upload)


def _find_by_client_id(ctx: RequestContext, client_capture_id: str) -> SourceArtifact | None:
    # ponytail: no public "find a source by client capture id" read exists on
    # StateStore yet, so this uses the store's private pager. Promote it to a
    # port method when a second caller needs it.
    lister: Callable[..., Page[SourceArtifact]] | None = getattr(ctx.state, "_page", None)
    if lister is None:
        return None
    cursor = None
    while True:
        page = lister("source", ctx.user_id, "", [], cursor=cursor, limit=200)
        for source in page.items:
            if source.client_capture_id == client_capture_id:
                return source
        if not page.next_cursor:
            return None
        cursor = page.next_cursor


def complete_capture(
    ctx: RequestContext, capture_id: str, request: CompleteCaptureRequest
) -> CompleteCaptureResponse:
    """Verify the stored object, then enqueue exactly one transcription job.

    A duplicate completion call returns the first job: the stored idempotent
    response is replayed by the route, and the outbox dedupe key collapses the
    work regardless.
    """
    source = ctx.state.get(ctx.user_id, "source", capture_id)
    if source is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"capture {capture_id} not found")
    if source.revision != request.expected_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"capture {capture_id} is at revision {source.revision}",
            current_revision=source.revision,
        )
    if source.object_ref is None:
        raise MetisError(ErrorCode.VALIDATION_FAILED, "this capture has no upload to complete")
    if not ctx.adapters.source_store.owns_key(ctx.user_id, source.object_ref.key):
        raise MetisError(ErrorCode.FORBIDDEN, "the object key is outside your prefix")

    stored_object = ctx.adapters.source_store.head(source.object_ref)
    if stored_object is None:
        raise MetisError(ErrorCode.OBJECT_MISSING, "the recording has not been uploaded")
    limits = settings().limits
    if (stored_object.size_bytes or 0) > limits.max_upload_bytes:
        raise MetisError(
            ErrorCode.UPLOAD_TOO_LARGE,
            f"the stored recording is {stored_object.size_bytes} bytes, over {limits.max_upload_bytes}",
        )
    if (stored_object.size_bytes or 0) == 0:
        raise MetisError(ErrorCode.UNSUPPORTED_MEDIA, "the uploaded recording is empty")

    idempotency_key = f"transcribe:{source.id}:{source.current_version}"
    job = _job(
        ctx,
        job_type="transcribe",
        target_id=source.id,
        target_revision=source.current_version,
        idempotency_key=idempotency_key,
    )
    updated = source.model_copy(update={"lifecycle": SourceLifecycle.PROCESSING, "object_ref": stored_object})
    with ctx.state.transaction(ctx.user_id) as tx:
        stored_source = tx.put("source", updated, expected_revision=source.revision)
        stored_job = tx.put("job", job)
        tx.append_outbox(
            _outbox(
                ctx,
                event_type=EventType.CAPTURE_COMPLETED,
                target_id=stored_source.id,
                target_revision=stored_source.current_version,
                payload={"job_id": stored_job.id, "capture_id": stored_source.id},
                idempotency_key=idempotency_key,
            )
        )
    return CompleteCaptureResponse(
        capture_id=stored_source.id, job_id=stored_job.id, revision=stored_source.revision
    )


def patch_transcript(ctx: RequestContext, capture_id: str, request: PatchTranscriptRequest) -> CaptureDetail:
    """Create a corrected transcript revision and supersede downstream work.

    Pending proposals from the previous version are marked superseded, and
    not-yet-dispatched reminders that depend on a disputed date are suspended
    pending clarification. Explicitly user-edited task and event fields are
    never overwritten.
    """
    source = ctx.state.get(ctx.user_id, "source", capture_id)
    if source is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"capture {capture_id} not found")
    if source.revision != request.expected_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"capture {capture_id} is at revision {source.revision}",
            current_revision=source.revision,
        )

    now = ctx.adapters.clock.now()
    new_version = source.current_version + 1
    previous = source.current_text() or ""
    revision = TranscriptRevision(
        source_version=new_version,
        text=request.text,
        asr_text=next(
            (t.asr_text for t in source.transcripts if t.source_version == source.current_version),
            None,
        ),
        edited_by_user=True,
        segments=[],
        created_at=now,
    )
    updated = source.model_copy(
        update={
            "current_version": new_version,
            "transcripts": [*source.transcripts, revision],
            "spans": [
                *source.spans,
                SourceSpan(
                    span_ref=f"span_full_v{new_version}",
                    source_id=source.id,
                    source_version=new_version,
                    start_char=0,
                    end_char=len(request.text),
                ),
            ],
        }
    )

    superseded = _supersede_proposals(ctx, capture_id, source.current_version)
    suspended = _suspend_dependent_reminders(ctx, capture_id, previous, request.text)

    with ctx.state.transaction(ctx.user_id) as tx:
        stored = tx.put("source", updated, expected_revision=source.revision)
        for proposal in superseded:
            tx.put("proposal", proposal["record"], expected_revision=proposal["revision"])
        tx.append_outbox(
            _outbox(
                ctx,
                event_type=EventType.TRANSCRIPT_FINALIZED,
                target_id=stored.id,
                target_revision=new_version,
                payload={
                    "recording_id": stored.id,
                    "source_version": new_version,
                    "text_length": len(request.text),
                    "edited_by_user": True,
                },
                idempotency_key=f"transcript:{stored.id}:{new_version}",
            )
        )
    if suspended:
        log.info("suspended %d reminder(s) pending clarification of a corrected date", len(suspended))
    return get_capture(ctx, capture_id)


def _supersede_proposals(ctx: RequestContext, source_id: str, old_version: int) -> list[dict[str, Any]]:
    from metis_contracts import ProposalDisposition

    lister = getattr(ctx.state, "_page", None)
    if lister is None:
        return []
    out: list[dict[str, Any]] = []
    cursor = None
    while True:
        page = lister("proposal", ctx.user_id, "", [], cursor=cursor, limit=200)
        for proposal in page.items:
            if (
                proposal.source_id == source_id
                and proposal.source_version == old_version
                and proposal.disposition is ProposalDisposition.PENDING
            ):
                out.append(
                    {
                        "record": proposal.model_copy(update={"disposition": ProposalDisposition.SUPERSEDED}),
                        "revision": proposal.revision,
                    }
                )
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    return out


def _suspend_dependent_reminders(
    ctx: RequestContext, source_id: str, previous_text: str, new_text: str
) -> list[str]:
    """Hold reminders whose critical date may have been misheard.

    Only not-yet-dispatched reminders are suspended, and only when the
    correction actually touched date-like text.
    """
    from metis_contracts import REMINDER_ACTIVE_STATES
    from metis_scheduler import suspend_reminder
    from metis_speech.evaluate import DATE_PATTERN

    before = {m.group(0).lower() for m in DATE_PATTERN.finditer(previous_text)}
    after = {m.group(0).lower() for m in DATE_PATTERN.finditer(new_text)}
    if before == after:
        return []

    suspended: list[str] = []
    cursor = None
    while True:
        page = ctx.state.list_tasks(ctx.user_id, cursor=cursor, limit=200)
        for task in page.items:
            if not any(ref.source_id == source_id for ref in task.source_refs):
                continue
            for reminder in ctx.state.list_reminders_for_target(ctx.user_id, task.id):
                if reminder.state in REMINDER_ACTIVE_STATES:
                    suspend_reminder(
                        ctx.state,
                        reminder,
                        reason="a corrected transcript changed a date; awaiting clarification",
                        clock=ctx.adapters.clock,
                    )
                    suspended.append(reminder.id)
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    return suspended


def get_capture(ctx: RequestContext, capture_id: str) -> CaptureDetail:
    source = ctx.state.get(ctx.user_id, "source", capture_id)
    if source is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"capture {capture_id} not found")
    current = next((t for t in source.transcripts if t.source_version == source.current_version), None)
    proposals: list[ExtractionProposal] = []
    lister: Callable[..., Page[ExtractionProposal]] | None = getattr(ctx.state, "_page", None)
    if lister is not None:
        cursor = None
        while True:
            page = lister("proposal", ctx.user_id, "", [], cursor=cursor, limit=200)
            proposals.extend(p for p in page.items if p.source_id == capture_id)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
    created_tasks = [
        t.id
        for t in ctx.state.list_tasks(ctx.user_id, limit=200).items
        if any(r.source_id == capture_id for r in t.source_refs)
    ]
    created_events = [
        e.id
        for e in ctx.state.list_events(ctx.user_id, limit=200).items
        if any(r.source_id == capture_id for r in e.source_refs)
    ]
    jobs: list[Job] = []
    cursor = None
    while True:
        job_page = ctx.state.list_jobs(ctx.user_id, cursor=cursor, limit=200)
        jobs.extend(j for j in job_page.items if j.target_id == capture_id)
        if not job_page.next_cursor:
            break
        cursor = job_page.next_cursor
    return CaptureDetail(
        source=source,
        transcript_text=current.text if current else None,
        segments=current.segments if current else [],
        proposals=sorted(proposals, key=lambda p: (p.source_version, p.range_index)),
        jobs=sorted(jobs, key=lambda j: j.created_at),
        created_task_ids=created_tasks,
        created_event_ids=created_events,
    )


def delete_capture(
    ctx: RequestContext, capture_id: str, *, delete_commitments: bool | None = None
) -> dict[str, Any]:
    """Tombstone now; purge derived data asynchronously and resumably."""
    source = ctx.state.get(ctx.user_id, "source", capture_id)
    if source is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"capture {capture_id} not found")
    preferences = ctx.state.get_preferences(ctx.user_id)
    cascade = preferences.delete_commitments_with_source if delete_commitments is None else delete_commitments
    purge_job = _job(
        ctx,
        job_type="purge_capture",
        target_id=capture_id,
        target_revision=source.revision,
        idempotency_key=f"purge:{capture_id}",
    )
    ctx.state.tombstone(ctx.user_id, "source", capture_id)
    with ctx.state.transaction(ctx.user_id) as tx:
        stored_job = tx.put("job", purge_job)
        tx.append_outbox(
            _outbox(
                ctx,
                event_type=EventType.CAPTURE_DELETED,
                target_id=capture_id,
                payload={
                    "target_id": capture_id,
                    "target_kind": "source",
                    "delete_commitments": bool(cascade),
                },
                idempotency_key=f"purge:{capture_id}",
            )
        )
    return {"capture_id": capture_id, "tombstoned": True, "purge_job_id": stored_job.id}


# --------------------------------------------------------------------------- #
# tasks and events
# --------------------------------------------------------------------------- #


def patch_task(ctx: RequestContext, task_id: str, request: PatchTaskRequest) -> Task:
    """Apply a validated patch. Omitted fields are unchanged; null clears."""
    from metis_contracts import TASK_TRANSITIONS

    task = ctx.require("task", task_id, Task)
    if task.revision != request.expected_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"task {task_id} is at revision {task.revision}",
            current_revision=task.revision,
        )

    patch = request.model_dump(exclude_unset=True, exclude={"expected_revision"})
    now = ctx.adapters.clock.now()

    if "state" in patch and patch["state"] is not None:
        target_state = TaskState(patch["state"])
        if target_state is not task.state and target_state not in TASK_TRANSITIONS[task.state]:
            raise MetisError(
                ErrorCode.INVALID_STATE_TRANSITION,
                f"a task cannot move from {task.state.value} to {target_state.value}",
            )
        if target_state is TaskState.DONE:
            patch["completed_at"] = now
        elif task.state is TaskState.DONE:
            patch["completed_at"] = None
        if target_state is TaskState.CANCELED:
            patch["canceled_at"] = now
        elif task.state is TaskState.CANCELED:
            patch["canceled_at"] = None

    if patch.get("depends_on"):
        _reject_cycles(ctx, task_id, list(patch["depends_on"]))

    # Fields the user edits explicitly are protected from later automatic
    # reconciliation of a corrected transcript.
    edited = set(task.user_edited_fields) | {k for k in patch if k != "state"}
    patch["user_edited_fields"] = sorted(edited)

    updated = task.model_copy(update=patch)
    with ctx.state.transaction(ctx.user_id) as tx:
        stored = tx.put("task", updated, expected_revision=task.revision)
        tx.append_outbox(
            _outbox(
                ctx,
                event_type=EventType.TASK_CHANGED,
                target_id=stored.id,
                target_revision=stored.revision,
                payload={"task_id": stored.id, "state": stored.state.value},
                idempotency_key=f"task_changed:{stored.id}:{stored.revision}",
            )
        )
    _reconcile_reminders_for_target(ctx, stored, kind="task")
    return stored


def _reject_cycles(ctx: RequestContext, task_id: str, dependencies: list[str]) -> None:
    """A dependency graph change must not create a cycle."""
    seen: set[str] = set()
    frontier = list(dependencies)
    while frontier:
        current = frontier.pop()
        if current == task_id:
            raise MetisError(ErrorCode.DEPENDENCY_CYCLE, "this change would create a dependency cycle")
        if current in seen:
            continue
        seen.add(current)
        other = ctx.state.get(ctx.user_id, "task", current)
        if other is not None:
            frontier.extend(other.depends_on)


def patch_event(ctx: RequestContext, event_id: str, request: PatchEventRequest) -> Event:
    event = ctx.require("event", event_id, Event)
    if event.revision != request.expected_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"event {event_id} is at revision {event.revision}",
            current_revision=event.revision,
        )
    patch = request.model_dump(exclude_unset=True, exclude={"expected_revision"})
    if patch.get("attendance_status") == "canceled":
        patch["canceled_at"] = ctx.adapters.clock.now()
    patch["user_edited_fields"] = sorted(set(event.user_edited_fields) | set(patch))
    updated = event.model_copy(update=patch)
    with ctx.state.transaction(ctx.user_id) as tx:
        stored = tx.put("event", updated, expected_revision=event.revision)
        tx.append_outbox(
            _outbox(
                ctx,
                event_type=EventType.EVENT_CHANGED,
                target_id=stored.id,
                target_revision=stored.revision,
                payload={"event_id": stored.id, "attendance_status": stored.attendance_status.value},
                idempotency_key=f"event_changed:{stored.id}:{stored.revision}",
            )
        )
    _reconcile_reminders_for_target(ctx, stored, kind="event")
    _invalidate_plans(ctx)
    return stored


def _reconcile_reminders_for_target(ctx: RequestContext, target: Any, *, kind: str) -> None:
    """Re-point or cancel reminders after their target changed.

    A completed task or canceled event cancels its pending reminders; a moved
    date re-points them. Both go through the scheduler's own state machine.
    """
    from metis_contracts import REMINDER_ACTIVE_STATES
    from metis_scheduler import cancel_reminder, retarget_reminder

    state_value = getattr(getattr(target, "state", None), "value", None)
    attendance = getattr(getattr(target, "attendance_status", None), "value", None)
    dead = state_value in ("done", "canceled") or attendance in ("canceled", "declined")

    for reminder in ctx.state.list_reminders_for_target(ctx.user_id, target.id):
        if reminder.state not in REMINDER_ACTIVE_STATES:
            continue
        if dead:
            cancel_reminder(
                ctx.state,
                reminder,
                clock=ctx.adapters.clock,
                correlation_id=ctx.correlation_id,
                reason=f"the target is {state_value or attendance}",
            )
        elif reminder.target_revision != target.revision:
            retarget_reminder(
                ctx.state,
                reminder,
                trigger_at=reminder.trigger_at,
                target_revision=target.revision,
                clock=ctx.adapters.clock,
                correlation_id=ctx.correlation_id,
            )


def _invalidate_plans(ctx: RequestContext) -> None:
    """Mark proposed plans stale once their inputs moved."""
    lister = getattr(ctx.state, "_page", None)
    if lister is None:
        return
    epoch = ctx.state.get_planning_epoch(ctx.user_id)
    cursor = None
    while True:
        page = lister("plan", ctx.user_id, "", [], cursor=cursor, limit=100)
        for plan in page.items:
            if plan.status is PlanStatus.PROPOSED and plan.planning_epoch != epoch:
                with ctx.state.transaction(ctx.user_id) as tx:
                    tx.put(
                        "plan",
                        plan.model_copy(
                            update={
                                "status": PlanStatus.INVALIDATED,
                                "change_summary": "Your commitments changed after this outline was generated.",
                            }
                        ),
                        expected_revision=plan.revision,
                    )
        if not page.next_cursor:
            break
        cursor = page.next_cursor


# --------------------------------------------------------------------------- #
# today
# --------------------------------------------------------------------------- #


def get_today(ctx: RequestContext, local_date: date | None = None) -> TodayResponse:
    """The deterministic authoritative view. No model, no vector store.

    This endpoint must keep working during any provider outage, which is why it
    only reads authoritative state.
    """
    preferences = ctx.state.get_preferences(ctx.user_id)
    timezone_name = preferences.timezone
    day = local_date or ctx.adapters.clock.today_local(timezone_name)
    retrieval = ctx.adapters.retrieval
    scan = retrieval.list_eligible_obligations(ctx.user_id, local_date=day)
    now = ctx.adapters.clock.now()

    from metis_pipeline.timeref import is_overdue

    overdue = [
        t
        for t in scan.tasks
        if t.deadline_local_date and is_overdue(t.deadline_local_date, t.timezone or timezone_name, now=now)
    ]
    overdue_ids = {t.id for t in overdue}
    open_tasks = [t for t in scan.tasks if t.state in TASK_OPEN_STATES and t.id not in overdue_ids]
    fixed_events = [e for e in scan.events if e.local_date == day]
    availability = ctx.state.list_availability(ctx.user_id, day)
    epoch = ctx.state.get_planning_epoch(ctx.user_id)

    plan = _latest_plan(ctx, day)
    stale = bool(plan and plan.planning_epoch != epoch)
    return TodayResponse(
        local_date=day,
        timezone=timezone_name,
        fixed_events=fixed_events,
        open_tasks=open_tasks,
        overdue_tasks=overdue,
        availability=availability,
        plan=plan,
        plan_stale=stale,
        plan_stale_reason=("Your commitments changed after this outline was generated." if stale else None),
        planning_epoch=epoch,
        inputs_complete=scan.complete,
        availability_configured=bool([b for b in availability if b.kind == "available"]),
    )


def _latest_plan(ctx: RequestContext, day: date) -> Plan | None:
    lister = getattr(ctx.state, "_page", None)
    if lister is None:
        return None
    best: Plan | None = None
    cursor = None
    while True:
        page = lister("plan", ctx.user_id, "AND local_date = ?", [day.isoformat()], cursor=cursor, limit=100)
        for plan in page.items:
            if plan.status is PlanStatus.INVALIDATED:
                continue
            if best is None or plan.updated_at > best.updated_at:
                best = plan
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    return best


def patch_preferences(ctx: RequestContext, request: PatchPreferencesRequest) -> Preferences:
    current = ctx.state.get_preferences(ctx.user_id)
    if current.revision != request.expected_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"preferences are at revision {current.revision}",
            current_revision=current.revision,
        )
    patch = request.model_dump(exclude_unset=True, exclude={"expected_revision"})
    if patch.get("timezone"):
        from metis_contracts import resolve_timezone

        resolve_timezone(patch["timezone"])  # raises on an unknown identifier
    existing = ctx.state.get(ctx.user_id, "preferences", current.id)
    updated = current.model_copy(update=patch)
    with ctx.state.transaction(ctx.user_id) as tx:
        return tx.put("preferences", updated, expected_revision=current.revision if existing else None)
