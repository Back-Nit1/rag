"""Every closed value set in Metis, in one place.

Transition tables live beside the enum they govern so a worker cannot invent a
state move that the state store would reject.
"""

from __future__ import annotations

from enum import StrEnum


class SourceKind(StrEnum):
    AUDIO = "audio"
    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"


class SourceLifecycle(StrEnum):
    PENDING_UPLOAD = "pending_upload"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    TOMBSTONED = "tombstoned"


class JobState(StrEnum):
    """Shared by capture/transcription/extraction/index/plan jobs."""

    UPLOADING = "uploading"
    QUEUED = "queued"
    STARTING = "starting"
    TRANSCRIBING = "transcribing"
    EXTRACTING = "extracting"
    INDEXING = "indexing"
    PLANNING = "planning"
    READY = "ready"
    FAILED = "failed"
    CANCELED = "canceled"


TERMINAL_JOB_STATES = frozenset({JobState.READY, JobState.FAILED, JobState.CANCELED})


class FailureCategory(StrEnum):
    """Drives retry policy. Only TRANSIENT and THROTTLED are retryable."""

    TRANSIENT = "transient"
    THROTTLED = "throttled"
    VALIDATION = "validation"
    UNSUPPORTED_MEDIA = "unsupported_media"
    LIMIT_EXCEEDED = "limit_exceeded"
    BUDGET_EXCEEDED = "budget_exceeded"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PERMISSION_DENIED = "permission_denied"
    SUPERSEDED = "superseded"
    INTERNAL = "internal"


RETRYABLE_FAILURES = frozenset(
    {FailureCategory.TRANSIENT, FailureCategory.THROTTLED, FailureCategory.PROVIDER_UNAVAILABLE}
)


class TaskState(StrEnum):
    INBOX = "inbox"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELED = "canceled"


TASK_OPEN_STATES = frozenset(
    {TaskState.INBOX, TaskState.READY, TaskState.IN_PROGRESS, TaskState.BLOCKED}
)

TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.INBOX: frozenset({TaskState.READY, TaskState.IN_PROGRESS, TaskState.BLOCKED, TaskState.DONE, TaskState.CANCELED}),
    TaskState.READY: frozenset({TaskState.IN_PROGRESS, TaskState.BLOCKED, TaskState.DONE, TaskState.CANCELED, TaskState.INBOX}),
    TaskState.IN_PROGRESS: frozenset({TaskState.BLOCKED, TaskState.DONE, TaskState.CANCELED, TaskState.READY}),
    TaskState.BLOCKED: frozenset({TaskState.READY, TaskState.IN_PROGRESS, TaskState.DONE, TaskState.CANCELED}),
    TaskState.DONE: frozenset({TaskState.IN_PROGRESS, TaskState.READY}),  # reopen is allowed
    TaskState.CANCELED: frozenset({TaskState.INBOX, TaskState.READY}),  # un-cancel is allowed
}


class AttendanceStatus(StrEnum):
    TENTATIVE = "tentative"
    COMMITTED = "committed"
    DECLINED = "declined"
    CANCELED = "canceled"


class RegistrationStatus(StrEnum):
    """Never inferred from an intention to attend."""

    UNKNOWN = "unknown"
    NOT_REGISTERED = "not_registered"
    REGISTERED = "registered"


class MemoryKind(StrEnum):
    IDEA = "idea"
    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"
    DECISION = "decision"
    COMMITMENT = "commitment"
    WORK_LOG = "work_log"
    ATTEMPT = "attempt"
    BLOCKER = "blocker"
    PREFERENCE = "preference"


class MemoryValidity(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    UNCERTAIN = "uncertain"


class ReminderState(StrEnum):
    PENDING_SCHEDULE = "pending_schedule"
    SCHEDULED = "scheduled"
    DISPATCHING = "dispatching"
    DELIVERED = "delivered"
    SCHEDULE_FAILED = "schedule_failed"
    DELIVERY_FAILED = "delivery_failed"
    SNOOZED = "snoozed"
    CANCELED = "canceled"
    SUPERSEDED = "superseded"


REMINDER_TRANSITIONS: dict[ReminderState, frozenset[ReminderState]] = {
    ReminderState.PENDING_SCHEDULE: frozenset(
        {ReminderState.SCHEDULED, ReminderState.SCHEDULE_FAILED, ReminderState.CANCELED, ReminderState.SUPERSEDED}
    ),
    ReminderState.SCHEDULE_FAILED: frozenset(
        {ReminderState.PENDING_SCHEDULE, ReminderState.SCHEDULED, ReminderState.CANCELED, ReminderState.SUPERSEDED}
    ),
    ReminderState.SCHEDULED: frozenset(
        {ReminderState.DISPATCHING, ReminderState.SNOOZED, ReminderState.CANCELED, ReminderState.SUPERSEDED, ReminderState.PENDING_SCHEDULE}
    ),
    ReminderState.DISPATCHING: frozenset(
        {ReminderState.DELIVERED, ReminderState.DELIVERY_FAILED, ReminderState.SUPERSEDED}
    ),
    ReminderState.DELIVERY_FAILED: frozenset(
        {ReminderState.DISPATCHING, ReminderState.CANCELED, ReminderState.SUPERSEDED}
    ),
    ReminderState.DELIVERED: frozenset({ReminderState.SNOOZED}),
    ReminderState.SNOOZED: frozenset(
        {ReminderState.PENDING_SCHEDULE, ReminderState.SCHEDULED, ReminderState.CANCELED, ReminderState.SUPERSEDED}
    ),
    ReminderState.CANCELED: frozenset(),
    ReminderState.SUPERSEDED: frozenset(),
}

REMINDER_ACTIVE_STATES = frozenset(
    {
        ReminderState.PENDING_SCHEDULE,
        ReminderState.SCHEDULED,
        ReminderState.DISPATCHING,
        ReminderState.SCHEDULE_FAILED,
        ReminderState.DELIVERY_FAILED,
        ReminderState.SNOOZED,
    }
)


class DeliveryChannel(StrEnum):
    IN_APP = "in_app"
    WEB_PUSH = "web_push"


class DeliveryStatus(StrEnum):
    """Transport acceptance and a user read receipt are separate facts."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class ProposalDisposition(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EDITED = "edited"
    DISMISSED = "dismissed"
    SUPERSEDED = "superseded"
    UNRESOLVED = "unresolved"


class ExtractedItemKind(StrEnum):
    TASK = "task"
    EVENT_INTENT = "event_intent"
    THOUGHT = "thought"
    DECISION = "decision"
    WORK_LOG = "work_log"
    REMINDER_REQUEST = "reminder_request"
    PREFERENCE = "preference"
    PROJECT_HINT = "project_hint"


class RequestedAction(StrEnum):
    REMIND_TO_REGISTER = "remind_to_register"
    PREPARE_FOR_EVENT = "prepare_for_event"
    REMIND_GENERIC = "remind_generic"
    PLAN_TIME = "plan_time"


class PlanStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    INVALIDATED = "invalidated"
    FAILED = "failed"
    FALLBACK = "fallback"


class OutboxState(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    SUPERSEDED = "superseded"


class AdapterMode(StrEnum):
    """Visible in every job result. A fixture must never claim production."""

    FIXTURE = "fixture"
    LOCAL = "local"
    CLOUD = "cloud"


__all__ = [n for n in dir() if n[0].isupper() or n.isupper()]
