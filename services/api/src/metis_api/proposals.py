"""Proposal resolution: the only path from a proposal to a real commitment.

Accepting an item is where a tentative extraction becomes authoritative state,
so this is also where the product's restraint lives: a tentative idea stays
tentative, registration is never inferred, and a reminder is only created when
its local date and time actually resolve.
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any

from metis_contracts import (
    AttendanceStatus,
    DeliveryChannel,
    ErrorCode,
    Event,
    EventType,
    ExtractedItem,
    ExtractedItemKind,
    MetisError,
    ProposalDisposition,
    RegistrationStatus,
    RequestedAction,
    SourceRef,
    Task,
    TaskState,
    new_id,
)
from metis_contracts.api import ClarificationRequest, ResolveProposalRequest, ResolveProposalResponse
from metis_pipeline.timeref import to_instant

log = logging.getLogger("metis.api.proposals")


def resolve_proposal(
    ctx: Any, proposal_id: str, request: ResolveProposalRequest
) -> ResolveProposalResponse:
    proposal = ctx.state.get(ctx.user_id, "proposal", proposal_id)
    if proposal is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"proposal {proposal_id} not found")
    if proposal.revision != request.expected_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"proposal {proposal_id} is at revision {proposal.revision}",
            current_revision=proposal.revision,
        )
    if proposal.disposition is ProposalDisposition.SUPERSEDED:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            "this proposal came from an earlier transcript revision and was superseded",
        )

    source = ctx.state.get(ctx.user_id, "source", proposal.source_id)
    if source is None:
        raise MetisError(ErrorCode.TOMBSTONED, "the originating capture was deleted")

    clock = ctx.adapters.clock
    now = clock.now()
    created_tasks: list[str] = []
    created_events: list[str] = []
    created_reminders: list[str] = []
    clarifications: list[ClarificationRequest] = []
    edited = False

    for decision in request.decisions:
        if decision.item_index >= len(proposal.items):
            raise MetisError(ErrorCode.VALIDATION_FAILED, f"no item at index {decision.item_index}")
        if decision.action == "dismiss":
            continue
        item = decision.edited if decision.action == "edit" else proposal.items[decision.item_index]
        if decision.action == "edit":
            edited = True

        if item.kind is ExtractedItemKind.TASK:
            task = _task_from(ctx, item, source, now)
            with ctx.state.transaction(ctx.user_id) as tx:
                stored = tx.put("task", task)
            created_tasks.append(stored.id)
            reminder_id, clarification = _maybe_reminder(ctx, item, stored, kind="task")
            if reminder_id:
                created_reminders.append(reminder_id)
            if clarification:
                clarifications.append(clarification)

        elif item.kind is ExtractedItemKind.EVENT_INTENT:
            event = _event_from(ctx, item, source, now)
            with ctx.state.transaction(ctx.user_id) as tx:
                stored = tx.put("event", event)
            created_events.append(stored.id)
            if RequestedAction.REMIND_TO_REGISTER in item.requested_actions:
                registration_task = Task(
                    id=new_id("task"),
                    user_id=ctx.user_id,
                    created_at=now,
                    updated_at=now,
                    title=f"Register for {item.title}",
                    state=TaskState.READY,
                    deadline_local_date=item.deadline_local_date,
                    timezone=item.timezone,
                    source_refs=[_ref(item, source)],
                )
                with ctx.state.transaction(ctx.user_id) as tx:
                    stored_task = tx.put("task", registration_task)
                created_tasks.append(stored_task.id)
                reminder_id, clarification = _maybe_reminder(ctx, item, stored_task, kind="task")
                if reminder_id:
                    created_reminders.append(reminder_id)
                if clarification:
                    clarifications.append(clarification)
            if RequestedAction.PREPARE_FOR_EVENT in item.requested_actions:
                prep = Task(
                    id=new_id("task"),
                    user_id=ctx.user_id,
                    created_at=now,
                    updated_at=now,
                    title=f"Prepare for {item.title}",
                    # A candidate, not an obligation: it starts in the inbox.
                    state=TaskState.INBOX,
                    source_refs=[_ref(item, source)],
                )
                with ctx.state.transaction(ctx.user_id) as tx:
                    created_tasks.append(tx.put("task", prep).id)
            if item.missing_fields:
                clarifications.append(
                    ClarificationRequest(
                        clarification_id=new_id("clar"),
                        source_id=source.id,
                        target_id=stored.id,
                        question=_question_for(item.missing_fields, item.title),
                        missing_fields=list(item.missing_fields),
                        blocking=False,
                    )
                )

        else:
            # Thoughts, decisions and work logs become memory, not obligations.
            _record_memory(ctx, item, source, now)

    disposition = (
        ProposalDisposition.EDITED
        if edited
        else ProposalDisposition.ACCEPTED
        if any(d.action in ("accept", "edit") for d in request.decisions)
        else ProposalDisposition.DISMISSED
    )
    with ctx.state.transaction(ctx.user_id) as tx:
        stored_proposal = tx.put(
            "proposal",
            proposal.model_copy(
                update={
                    "disposition": disposition,
                    "created_entity_ids": [*created_tasks, *created_events],
                }
            ),
            expected_revision=proposal.revision,
        )

    # One active clarification thread per captured item.
    deduped: dict[str, ClarificationRequest] = {}
    for clarification in clarifications:
        key = clarification.target_id or clarification.source_id
        if key not in deduped:
            deduped[key] = clarification

    return ResolveProposalResponse(
        proposal=stored_proposal,
        created_task_ids=created_tasks,
        created_event_ids=created_events,
        created_reminder_ids=created_reminders,
        clarifications=list(deduped.values()),
    )


def _ref(item: ExtractedItem, source: Any) -> SourceRef:
    first = item.evidence[0] if item.evidence else None
    return SourceRef(
        source_id=source.id,
        source_version=first.source_version if first else source.current_version,
        span_ref=first.span_ref if first else None,
    )


def _task_from(ctx: Any, item: ExtractedItem, source: Any, now: datetime) -> Task:
    return Task(
        id=new_id("task"),
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        title=item.title,
        body=item.body,
        # An unresolved item lands in the inbox rather than becoming a
        # ready commitment the user never confirmed.
        state=TaskState.INBOX if item.missing_fields else TaskState.READY,
        deadline_local_date=item.deadline_local_date,
        deadline_at=item.deadline_at,
        timezone=item.timezone,
        duration_estimate=item.duration_estimate,
        source_refs=[_ref(item, source)],
    )


def _event_from(ctx: Any, item: ExtractedItem, source: Any, now: datetime) -> Event:
    return Event(
        id=new_id("event"),
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        title=item.title,
        body=item.body,
        location_text=item.location_text,
        starts_at=item.starts_at,
        local_date=item.local_date,
        timezone=item.timezone,
        # Interest in attending is never promoted to a commitment, and
        # registration is never inferred from that interest.
        attendance_status=item.attendance_status or AttendanceStatus.TENTATIVE,
        registration_status=item.registration_status or RegistrationStatus.UNKNOWN,
        source_refs=[_ref(item, source)],
    )


def _maybe_reminder(
    ctx: Any, item: ExtractedItem, target: Any, *, kind: str
) -> tuple[str | None, ClarificationRequest | None]:
    """Create a reminder only when its local date AND time actually resolve."""
    from metis_scheduler import request_reminder

    wants_reminder = bool(
        {RequestedAction.REMIND_TO_REGISTER, RequestedAction.REMIND_GENERIC}
        & set(item.requested_actions)
    )
    if not wants_reminder:
        return None, None

    timezone_name = item.timezone or ctx.state.get_preferences(ctx.user_id).timezone
    local_date = item.local_date or item.deadline_local_date
    local_time = item.remind_local_time

    if item.remind_at is not None:
        trigger_at = item.remind_at
    elif local_date and local_time:
        hour, minute = (int(part) for part in local_time.split(":"))
        resolution = to_instant(local_date, time(hour, minute), timezone_name)
        if resolution.instant is None or resolution.dst != "ok":
            return None, ClarificationRequest(
                clarification_id=new_id("clar"),
                source_id=_ref(item, ctx.state.get(ctx.user_id, "source", item.evidence[0].source_id)).source_id
                if item.evidence
                else "",
                target_id=target.id,
                question=(
                    "That local time is ambiguous or does not exist on that date "
                    "because of a daylight-saving change. Which time did you mean?"
                ),
                missing_fields=["registration_reminder_time"],
                blocking=True,
            )
        trigger_at = resolution.instant
    else:
        # No guessed placeholder date is ever used as a real schedule.
        return None, ClarificationRequest(
            clarification_id=new_id("clar"),
            source_id=item.evidence[0].source_id if item.evidence else "",
            target_id=target.id,
            question="When would you like to be reminded? A date and time, please.",
            missing_fields=[f for f in ("event_date_time", "registration_reminder_time")],
            blocking=True,
        )

    reminder = request_reminder(
        ctx.state,
        user_id=ctx.user_id,
        target_id=target.id,
        target_kind=kind,
        target_revision=target.revision,
        trigger_at=trigger_at,
        timezone_name=timezone_name,
        channel=DeliveryChannel.IN_APP,
        clock=ctx.adapters.clock,
        correlation_id=ctx.correlation_id,
    )
    return reminder.id, None


def _record_memory(ctx: Any, item: ExtractedItem, source: Any, now: datetime) -> None:
    """Persist a thought/decision/work log as memory and request indexing."""
    from metis_contracts import Memory, OutboxEvent
    from metis_retrieval import KIND_MAP, content_hash

    kind = KIND_MAP.get(item.kind)
    if kind is None:
        return
    content = " ".join(filter(None, [item.title, item.body or ""]))[:8000]
    if not content.strip():
        return
    ref = _ref(item, source)
    embedder = ctx.adapters.embedder
    memory = Memory(
        id=new_id("mem"),
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        kind=kind,
        content=content,
        effective_at=source.captured_at,
        source_refs=[ref],
        content_hash=content_hash(content),
        embedding_model_id=getattr(embedder, "model_id", None),
        embedding_dimensions=getattr(embedder, "dimensions", None),
    )
    with ctx.state.transaction(ctx.user_id) as tx:
        stored = tx.put("memory", memory)
        tx.append_outbox(
            OutboxEvent(
                id=new_id("obx"),
                user_id=ctx.user_id,
                created_at=now,
                updated_at=now,
                event_type=EventType.MEMORY_INDEX_REQUESTED,
                target_id=stored.id,
                target_revision=ref.source_version,
                correlation_id=ctx.correlation_id,
                idempotency_key=f"index:{stored.id}:{stored.content_hash}",
                payload={
                    "memory_id": stored.id,
                    "source_id": ref.source_id,
                    "source_version": ref.source_version,
                    "content_hash": stored.content_hash,
                },
                available_at=now,
            )
        )


def _question_for(missing_fields: list[str], title: str) -> str:
    """One compact question that a single reply can resolve."""
    labels = {
        "event_date_time": "the date and time",
        "registration_reminder_time": "when to remind you to register",
        "deadline_local_date": "the deadline",
        "registration_status": "whether you have registered",
    }
    parts = [labels.get(f, f.replace("_", " ")) for f in missing_fields]
    if len(parts) == 1:
        return f"For '{title}', what is {parts[0]}?"
    return f"For '{title}', what are {', '.join(parts[:-1])} and {parts[-1]}?"
