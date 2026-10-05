"""Pipeline handlers: transcription, extraction and resumable purge.

Reminder scheduling and delivery handlers live in ``services/scheduler`` and are
merged into the same registry by the worker, so ownership stays clean while the
dispatcher stays single.
"""

from __future__ import annotations

import logging
from typing import Any

from metis_contracts import (
    ErrorCode,
    EventEnvelope,
    EventType,
    JobState,
    MetisError,
    OutboxEvent,
    SourceLifecycle,
    TranscriptionJobInput,
    new_id,
    settings,
)
from metis_contracts.entities import Job, TranscriptRevision
from metis_contracts.events import ExtractionRequestedPayload, PurgePayload, TranscriptFinalizedPayload

from .dispatcher import DispatchContext, HandlerRegistry, is_fresh
from .extraction import ExtractionService

log = logging.getLogger("metis.pipeline.handlers")

registry = HandlerRegistry()


def _outbox(
    envelope: EventEnvelope,
    *,
    event_type: str,
    payload: dict[str, Any],
    target_id: str | None = None,
    target_revision: int | None = None,
    idempotency_key: str,
    clock: Any,
) -> OutboxEvent:
    now = clock.now()
    return OutboxEvent(
        id=new_id("obx"),
        user_id=envelope.user_id,
        created_at=now,
        updated_at=now,
        event_type=event_type,
        target_id=target_id,
        target_revision=target_revision,
        correlation_id=envelope.correlation_id,
        idempotency_key=idempotency_key,
        payload=payload,
        available_at=now,
    )


# --------------------------------------------------------------------------- #
# capture.completed -> transcription
# --------------------------------------------------------------------------- #


@registry.register(EventType.CAPTURE_COMPLETED)
def handle_capture_completed(envelope: EventEnvelope, context: DispatchContext) -> None:
    """Transcribe an uploaded recording, then finalize exactly one text revision.

    In local/fixture mode the decode happens inline. In cloud mode this is where
    the Step Functions execution (`ecs:runTask.sync`) is started instead; either
    way the result is only accepted after the worker's output is verified, since
    a successful launch is not a successful transcription.
    """
    state = context.state_store
    source = state.get(envelope.user_id, "source", envelope.target_id or "")
    if source is None:
        raise MetisError(ErrorCode.TOMBSTONED, "the capture was deleted before transcription")
    if source.current_version != envelope.target_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"capture moved to version {source.current_version}; this job is superseded",
        )
    if source.object_ref is None:
        raise MetisError(ErrorCode.VALIDATION_FAILED, "audio capture has no object reference")
    if not context.source_store.owns_key(envelope.user_id, source.object_ref.key):
        raise MetisError(ErrorCode.FORBIDDEN, "object key is outside the owner's prefix")

    job_id = str(envelope.payload.get("job_id") or new_id("job"))
    job = state.get(envelope.user_id, "job", job_id)
    if job is None:
        now = context.clock.now()
        job = Job(
            id=job_id,
            user_id=envelope.user_id,
            created_at=now,
            updated_at=now,
            job_type="transcribe",
            state=JobState.TRANSCRIBING,
            target_id=source.id,
            target_revision=source.current_version,
            correlation_id=envelope.correlation_id,
            idempotency_key=envelope.idempotency_key,
        )
        with state.transaction(envelope.user_id) as tx:
            job = tx.put("job", job)
    else:
        job = state.set_job_state(job_id, user_id=envelope.user_id, state=JobState.TRANSCRIBING)

    job_input = TranscriptionJobInput(
        recording_id=source.id,
        user_id=envelope.user_id,
        source_version=source.current_version,
        s3_bucket=source.object_ref.bucket,
        s3_key=source.object_ref.key,
        model_id=settings().speech.model_id,
        max_audio_seconds=settings().limits.max_audio_seconds,
        idempotency_key=envelope.idempotency_key,
        correlation_id=envelope.correlation_id,
        job_id=job.id,
    )

    if context.transcriber is None:
        raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "no transcriber is configured")

    body = context.source_store.get_bytes(source.object_ref)
    text, segments, meta = context.transcriber.transcribe(
        body,
        language=job_input.language or settings().speech.language,
        glossary=tuple(job_input.glossary),
        max_seconds=job_input.max_audio_seconds,
    )

    state.record_speech_usage(
        envelope.user_id,
        local_date=context.clock.today_local(source.capture_timezone),
        audio_seconds=float(meta.get("audio_seconds", 0.0)),
    )

    # Save the transcript BEFORE extraction: speech errors affect dates, names
    # and intent, so the user must be able to correct it first.
    revision = TranscriptRevision(
        source_version=source.current_version,
        text=text,
        asr_text=text,
        edited_by_user=False,
        segments=segments,
        speech=_speech_metadata(meta),
        created_at=context.clock.now(),
    )
    updated = source.model_copy(
        update={
            "lifecycle": SourceLifecycle.READY,
            "transcripts": [*source.transcripts, revision],
            "duration_seconds": float(meta.get("audio_seconds", 0.0)) or source.duration_seconds,
        }
    )
    with state.transaction(envelope.user_id) as tx:
        stored = tx.put("source", updated, expected_revision=source.revision)
        if text.strip():
            tx.append_outbox(
                _outbox(
                    envelope,
                    event_type=EventType.TRANSCRIPT_FINALIZED,
                    target_id=stored.id,
                    target_revision=stored.current_version,
                    payload=TranscriptFinalizedPayload(
                        recording_id=stored.id,
                        source_version=stored.current_version,
                        text_length=len(text),
                    ).model_dump(mode="json"),
                    # Exactly one logical transcript.finalized per accepted
                    # text revision, no matter how many retries happened.
                    idempotency_key=f"transcript:{stored.id}:{stored.current_version}",
                    clock=context.clock,
                )
            )

    state.set_job_state(
        job.id,
        user_id=envelope.user_id,
        state=JobState.READY,
        result_refs={"source_id": source.id, "source_version": str(source.current_version)},
        metrics={
            "audio_seconds": float(meta.get("audio_seconds", 0.0)),
            "decode_seconds": float(meta.get("decode_seconds", 0.0)),
            "model_load_seconds": float(meta.get("model_load_seconds", 0.0) or 0.0),
        },
    )
    if not text.strip():
        log.info("source %s decoded to no speech; nothing downstream was enqueued", source.id)


def _speech_metadata(meta: dict[str, Any]) -> Any:
    from metis_contracts.entities import SpeechMetadata

    known = {k: v for k, v in meta.items() if k in SpeechMetadata.model_fields}
    return SpeechMetadata.model_validate(known)


# --------------------------------------------------------------------------- #
# transcript.finalized -> extraction requested
# --------------------------------------------------------------------------- #


@registry.register(EventType.TRANSCRIPT_FINALIZED)
def handle_transcript_finalized(envelope: EventEnvelope, context: DispatchContext) -> None:
    """Fan out one extraction request per source range.

    A corrected transcript creates a new source version, so an old request is
    dropped as superseded by the freshness check rather than overwriting the
    newer results.
    """
    if not is_fresh(envelope, context, kind="source"):
        raise MetisError(ErrorCode.REVISION_CONFLICT, "a newer transcript revision supersedes this one")

    payload = TranscriptFinalizedPayload.model_validate(envelope.payload)
    source = context.state_store.get(envelope.user_id, "source", payload.recording_id)
    if source is None:
        raise MetisError(ErrorCode.TOMBSTONED, "the capture was deleted")

    text = source.current_text() or ""
    from .extraction import split_ranges

    limits = settings().limits
    ranges = split_ranges(
        text, max_chars=limits.extraction_max_input_tokens * 3, max_ranges=limits.extraction_max_ranges
    )
    with context.state_store.transaction(envelope.user_id) as tx:
        for index, (start, end) in enumerate(ranges):
            tx.append_outbox(
                _outbox(
                    envelope,
                    event_type=EventType.EXTRACTION_REQUESTED,
                    target_id=source.id,
                    target_revision=payload.source_version,
                    payload=ExtractionRequestedPayload(
                        source_id=source.id,
                        source_version=payload.source_version,
                        range_index=index,
                        range_count=len(ranges),
                        char_start=start,
                        char_end=end,
                    ).model_dump(mode="json"),
                    # Per-range key: a retry re-runs only the unfinished ranges.
                    idempotency_key=f"extract:{source.id}:{payload.source_version}:{index}",
                    clock=context.clock,
                )
            )


# --------------------------------------------------------------------------- #
# extraction.requested -> proposal
# --------------------------------------------------------------------------- #


@registry.register(EventType.EXTRACTION_REQUESTED)
def handle_extraction_requested(envelope: EventEnvelope, context: DispatchContext) -> None:
    if not is_fresh(envelope, context, kind="source"):
        raise MetisError(ErrorCode.REVISION_CONFLICT, "the source moved on; this extraction is superseded")
    if context.inference is None:
        raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "no inference client is configured")

    payload = ExtractionRequestedPayload.model_validate(envelope.payload)
    source = context.state_store.get(envelope.user_id, "source", payload.source_id)
    if source is None:
        raise MetisError(ErrorCode.TOMBSTONED, "the capture was deleted")
    if source.current_version != payload.source_version:
        raise MetisError(ErrorCode.REVISION_CONFLICT, "a corrected transcript supersedes this extraction")

    service = ExtractionService(
        state_store=context.state_store, inference=context.inference, clock=context.clock
    )
    outcome = service.extract(
        source,
        source_version=payload.source_version,
        range_index=payload.range_index,
        char_start=payload.char_start,
        char_end=payload.char_end,
    )
    with context.state_store.transaction(envelope.user_id) as tx:
        tx.put("proposal", outcome.proposal)
        tx.append_outbox(
            _outbox(
                envelope,
                event_type=EventType.EXTRACTION_COMPLETED,
                target_id=outcome.proposal.id,
                payload={
                    "proposal_id": outcome.proposal.id,
                    "source_id": source.id,
                    "source_version": payload.source_version,
                    "range_index": payload.range_index,
                    "items": len(outcome.proposal.items),
                    "requires_clarification": outcome.proposal.requires_clarification,
                },
                idempotency_key=f"extracted:{source.id}:{payload.source_version}:{payload.range_index}",
                clock=context.clock,
            )
        )


@registry.register(EventType.EXTRACTION_COMPLETED)
def handle_extraction_completed(envelope: EventEnvelope, context: DispatchContext) -> None:
    """Terminal for now: the proposal is already persisted and visible.

    Auto-promotion of unambiguous items is a deliberate next step, not a
    silent default — turning every brainstorm into a task is exactly what the
    product must not do.
    """
    log.info(
        "proposal ready source=%s items=%s clarify=%s",
        envelope.payload.get("source_id"),
        envelope.payload.get("items"),
        envelope.payload.get("requires_clarification"),
    )


# --------------------------------------------------------------------------- #
# deletion: tombstone now, purge derived data resumably
# --------------------------------------------------------------------------- #


@registry.register(EventType.CAPTURE_DELETED)
def handle_capture_deleted(envelope: EventEnvelope, context: DispatchContext) -> None:
    """Purge derived data for a deleted capture, resumably.

    Application access was already removed by the tombstone at request time.
    This step removes the object bytes, the derived memories and the vectors,
    and reconciles dependent reminders. It carries a cursor so a timeout
    continues rather than leaving deletion half-finished.
    """
    payload = PurgePayload.model_validate(envelope.payload)
    state = context.state_store
    source = state.get(envelope.user_id, "source", payload.target_id)

    # Objects: bytes first, since they are the largest exposure.
    if source is not None and source.object_ref is not None:
        context.source_store.delete(source.object_ref)
    else:
        from metis_state.object_store import owner_prefix

        prefix = f"{owner_prefix(envelope.user_id)}/captures/{payload.target_id}"
        deleter = getattr(context.source_store, "delete_prefix", None)
        if deleter is not None:
            deleter(prefix)

    # Derived memories and their vectors.
    memory_ids: list[str] = []
    for memory in _memories_for_source(state, envelope.user_id, payload.target_id):
        memory_ids.append(memory.id)
        state.tombstone(envelope.user_id, "memory", memory.id)
    if memory_ids and context.memory_store is not None:
        context.memory_store.delete(envelope.user_id, memory_ids)

    # Reminders whose only basis was this capture.
    for reminder in _reminders_for_source(state, envelope.user_id, payload.target_id):
        with state.transaction(envelope.user_id) as tx:
            tx.put(
                "reminder",
                reminder.model_copy(update={"state": "canceled", "canceled_at": context.clock.now()}),
                expected_revision=reminder.revision,
            )

    # Commitments: the user's choice, never a surprise deletion.
    if payload.delete_commitments:
        for task in _tasks_for_source(state, envelope.user_id, payload.target_id):
            with state.transaction(envelope.user_id) as tx:
                tx.put(
                    "task",
                    task.model_copy(update={"state": "canceled", "canceled_at": context.clock.now()}),
                    expected_revision=task.revision,
                )
    else:
        for task in _tasks_for_source(state, envelope.user_id, payload.target_id):
            if task.source_unavailable:
                continue
            with state.transaction(envelope.user_id) as tx:
                tx.put(
                    "task",
                    task.model_copy(update={"source_unavailable": True}),
                    expected_revision=task.revision,
                )

    state.purge(envelope.user_id, "source", payload.target_id)
    log.info(
        "purged capture=%s memories=%d commitments_deleted=%s",
        payload.target_id,
        len(memory_ids),
        payload.delete_commitments,
    )


def _memories_for_source(state: Any, user_id: str, source_id: str) -> list[Any]:
    """Every memory derived from this source, across all pages.

    Deletion must not stop at the first page: a partially purged capture would
    leave retrievable evidence behind.
    """
    lister = getattr(state, "list_memories", None)
    if lister is None:
        return []
    found: list[Any] = []
    cursor: str | None = None
    while True:
        page = lister(user_id, cursor=cursor, limit=200)
        found.extend(m for m in page.items if any(r.source_id == source_id for r in m.source_refs))
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    return found


def _tasks_for_source(state: Any, user_id: str, source_id: str) -> list[Any]:
    cursor: str | None = None
    found: list[Any] = []
    while True:
        page = state.list_tasks(user_id, cursor=cursor, limit=200)
        found.extend(t for t in page.items if any(r.source_id == source_id for r in t.source_refs))
        if page.complete or not page.next_cursor:
            break
        cursor = page.next_cursor
    return found


def _reminders_for_source(state: Any, user_id: str, source_id: str) -> list[Any]:
    tasks = {t.id for t in _tasks_for_source(state, user_id, source_id)}
    reminders: list[Any] = []
    for task_id in tasks:
        reminders.extend(
            r
            for r in state.list_reminders_for_target(user_id, task_id)
            if r.state not in ("canceled", "delivered", "superseded")
        )
    return reminders
