"""Internal event envelope and the event-type registry.

Queue messages carry references, never audio bytes, full transcripts or secrets.
An envelope grants no authorization by itself: every consumer re-checks the
authenticated owner and the target revision before any side effect.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from .common import EntityId, Revision, StrictModel, UserId, new_id, utcnow


class EventType:
    """Event names, as literal constants so a typo fails at import time."""

    CAPTURE_CREATED = "capture.created"
    CAPTURE_COMPLETED = "capture.completed"
    CAPTURE_DELETED = "capture.deleted"
    TRANSCRIPT_FINALIZED = "transcript.finalized"
    EXTRACTION_REQUESTED = "extraction.requested"
    EXTRACTION_COMPLETED = "extraction.completed"
    PROPOSAL_RESOLVED = "proposal.resolved"
    TASK_CHANGED = "task.changed"
    EVENT_CHANGED = "event.changed"
    REMINDER_REQUESTED = "reminder.requested"
    REMINDER_SCHEDULE_SYNC = "reminder.schedule_sync"
    REMINDER_DUE = "reminder.due"
    REMINDER_CANCEL_SYNC = "reminder.cancel_sync"
    NOTIFICATION_DISPATCH = "notification.dispatch"
    MEMORY_INDEX_REQUESTED = "memory.index_requested"
    MEMORY_PURGE_REQUESTED = "memory.purge_requested"
    PLAN_REQUESTED = "plan.requested"
    PLAN_INVALIDATED = "plan.invalidated"


ALL_EVENT_TYPES: frozenset[str] = frozenset(
    v for k, v in vars(EventType).items() if not k.startswith("_") and isinstance(v, str)
)

#: Events whose handler mutates state and therefore must verify target_revision
#: before acting. A stale envelope is acknowledged and dropped as superseded.
REVISION_SENSITIVE: frozenset[str] = frozenset(
    {
        EventType.TRANSCRIPT_FINALIZED,
        EventType.EXTRACTION_REQUESTED,
        EventType.REMINDER_SCHEDULE_SYNC,
        EventType.REMINDER_DUE,
        EventType.NOTIFICATION_DISPATCH,
        EventType.MEMORY_INDEX_REQUESTED,
        EventType.PLAN_REQUESTED,
    }
)


class EventEnvelope(StrictModel):
    """Canonical internal event. Payloads stay small and reference-only."""

    event_id: EntityId = Field(default_factory=lambda: new_id("evt"))
    event_type: str
    event_version: Annotated[int, Field(ge=1)] = 1
    user_id: UserId
    occurred_at: datetime = Field(default_factory=utcnow)
    correlation_id: str
    target_id: EntityId | None = None
    target_revision: Revision | None = None
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, _ctx: Any) -> None:
        if self.event_type not in ALL_EVENT_TYPES:
            raise ValueError(f"unknown event_type {self.event_type!r}")
        if self.event_type in REVISION_SENSITIVE and self.target_revision is None:
            raise ValueError(f"{self.event_type} requires target_revision")


# --------------------------------------------------------------------------- #
# Typed payloads for the events that cross a service boundary
# --------------------------------------------------------------------------- #


class TranscriptionJobInput(StrictModel):
    """Contract between the pipeline and the owned ASR worker.

    `user_id` is validated against the authenticated owner by the enqueuing
    service; the worker additionally rejects an s3_key outside that owner's
    prefix.
    """

    recording_id: EntityId
    user_id: UserId
    source_version: Revision
    s3_bucket: str
    s3_key: str
    language: str | None = None
    model_id: str = "small.en"
    beam_size: Annotated[int, Field(ge=1, le=10)] = 1
    vad_enabled: bool = True
    glossary: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list)
    max_audio_seconds: Annotated[int, Field(ge=1, le=3600)] = 300
    idempotency_key: str
    correlation_id: str
    job_id: EntityId


class TranscriptionJobResult(StrictModel):
    """What the worker persists. `ok=False` carries an actionable failure."""

    job_id: EntityId
    recording_id: EntityId
    source_version: Revision
    ok: bool
    text: str | None = None
    segments_ref: str | None = None
    """Object reference to the full segment list; segments are not inlined when
    large so the queue payload stays small."""
    failure: str | None = None
    error_message: Annotated[str | None, Field(default=None, max_length=1000)] = None


class TranscriptFinalizedPayload(StrictModel):
    recording_id: EntityId
    source_version: Revision
    transcript_object_ref: str | None = None
    text_length: Annotated[int, Field(ge=0)]
    edited_by_user: bool = False


class ExtractionRequestedPayload(StrictModel):
    source_id: EntityId
    source_version: Revision
    range_index: Annotated[int, Field(ge=0)] = 0
    range_count: Annotated[int, Field(ge=1)] = 1
    char_start: Annotated[int, Field(ge=0)] = 0
    char_end: Annotated[int | None, Field(default=None, ge=0)] = None


class ReminderSyncPayload(StrictModel):
    reminder_id: EntityId
    action: Literal["create", "update", "delete"]
    trigger_at: datetime | None = None
    scheduler_ref: str | None = None


class ReminderDuePayload(StrictModel):
    reminder_id: EntityId
    occurrence_key: str
    scheduled_for: datetime


class MemoryIndexPayload(StrictModel):
    memory_id: EntityId
    source_id: EntityId
    source_version: Revision
    content_hash: str


class PlanRequestedPayload(StrictModel):
    plan_id: EntityId
    local_date: str
    timezone: str
    time_budget_minutes: Annotated[int, Field(ge=0)]
    planning_epoch: Annotated[int, Field(ge=0)]


class PurgePayload(StrictModel):
    """Resumable derived-data deletion. `cursor` lets a retry continue."""

    target_id: EntityId
    target_kind: str
    cursor: str | None = None
    delete_commitments: bool = False


PAYLOAD_MODELS: dict[str, type[StrictModel]] = {
    EventType.TRANSCRIPT_FINALIZED: TranscriptFinalizedPayload,
    EventType.EXTRACTION_REQUESTED: ExtractionRequestedPayload,
    EventType.REMINDER_SCHEDULE_SYNC: ReminderSyncPayload,
    EventType.REMINDER_CANCEL_SYNC: ReminderSyncPayload,
    EventType.REMINDER_DUE: ReminderDuePayload,
    EventType.MEMORY_INDEX_REQUESTED: MemoryIndexPayload,
    EventType.MEMORY_PURGE_REQUESTED: PurgePayload,
    EventType.CAPTURE_DELETED: PurgePayload,
    EventType.PLAN_REQUESTED: PlanRequestedPayload,
}


def validate_payload(envelope: EventEnvelope) -> StrictModel | None:
    """Parse a payload against its registered model, if one exists."""
    model = PAYLOAD_MODELS.get(envelope.event_type)
    return model.model_validate(envelope.payload) if model else None
