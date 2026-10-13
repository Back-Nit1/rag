"""Authoritative Metis entities (schema version 1).

Every model here is owner-scoped and revisioned. Client-facing projections are
produced by dropping audit fields, never by relaxing these invariants.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .common import (
    ConfidenceHint,
    EntityId,
    Estimate,
    Evidence,
    Record,
    Revision,
    SourceRef,
    SourceSpan,
    StrictModel,
    TimezoneName,
)
from .enums import (
    AdapterMode,
    AttendanceStatus,
    DeliveryChannel,
    DeliveryStatus,
    ExtractedItemKind,
    FailureCategory,
    JobState,
    MemoryKind,
    MemoryValidity,
    OutboxState,
    PlanStatus,
    ProposalDisposition,
    RegistrationStatus,
    ReminderPurpose,
    ReminderState,
    RequestedAction,
    SourceKind,
    SourceLifecycle,
    StatusUpdateAction,
    TaskPurpose,
    TaskState,
)

Title = Annotated[str, Field(min_length=1, max_length=500)]
Body = Annotated[str | None, Field(default=None, max_length=20000)]


# --------------------------------------------------------------------------- #
# Sources and transcripts
# --------------------------------------------------------------------------- #


class TranscriptSegment(StrictModel):
    """ASR output segment. Timing is audio timing, never a semantic event date."""

    index: Annotated[int, Field(ge=0)]
    start_ms: Annotated[int, Field(ge=0)]
    end_ms: Annotated[int, Field(ge=0)]
    text: str


class SpeechMetadata(StrictModel):
    """Reproducibility record for one decode. Persisted with every result."""

    model_id: str
    artifact_revision: str
    library_version: str
    compute_type: str
    beam_size: Annotated[int, Field(ge=1, le=10)]
    temperature: Annotated[float, Field(ge=0.0, le=1.0)]
    vad_enabled: bool
    language: str
    language_detected: str | None = None
    adapter_mode: AdapterMode
    decode_seconds: Annotated[float, Field(ge=0.0)]
    audio_seconds: Annotated[float, Field(ge=0.0)]
    cold_start: bool | None = None


class TranscriptRevision(StrictModel):
    """One accepted text revision of a source. Audio bytes are unchanged by an edit."""

    source_version: Revision
    text: Annotated[str, Field(max_length=100000)]
    asr_text: Annotated[str | None, Field(default=None, max_length=100000)] = None
    edited_by_user: bool = False
    segments: list[TranscriptSegment] = Field(default_factory=list)
    speech: SpeechMetadata | None = None
    created_at: datetime


class ObjectRef(StrictModel):
    """Private object-storage reference. Never a public or presigned URL at rest."""

    bucket: str
    key: str
    size_bytes: Annotated[int | None, Field(default=None, ge=0)] = None
    checksum_sha256: Annotated[str | None, Field(default=None, min_length=64, max_length=64)] = None
    content_type: str | None = None


class SourceArtifact(Record):
    """Evidence of what the user said or imported. Immutable per source_version."""

    kind: SourceKind
    lifecycle: SourceLifecycle
    capture_timezone: TimezoneName
    captured_at: datetime
    """The instant of capture in the client's recorded timezone — relative
    phrases like "tomorrow" resolve against this, not a worker's clock."""
    object_ref: ObjectRef | None = None
    duration_seconds: Annotated[float | None, Field(default=None, ge=0)] = None
    current_version: Revision = 1
    transcripts: list[TranscriptRevision] = Field(default_factory=list)
    spans: list[SourceSpan] = Field(default_factory=list)
    retention_expires_at: datetime | None = None
    tombstoned_at: datetime | None = None
    client_capture_id: str | None = None
    """Stable client-generated id for the offline outbox, used for dedupe."""
    failure: FailureCategory | None = None
    error_message: Annotated[str | None, Field(default=None, max_length=1000)] = None
    project_id: EntityId | None = None
    """Project this capture was explicitly attached to by the user; never inferred."""

    def current_text(self) -> str | None:
        for t in self.transcripts:
            if t.source_version == self.current_version:
                return t.text
        return None

    @model_validator(mode="after")
    def _audio_needs_object(self) -> SourceArtifact:
        if self.kind in (SourceKind.AUDIO, SourceKind.IMAGE, SourceKind.DOCUMENT) and (
            self.lifecycle not in (SourceLifecycle.PENDING_UPLOAD, SourceLifecycle.TOMBSTONED)
            and self.object_ref is None
        ):
            raise ValueError(f"{self.kind} source past upload must carry an object_ref")
        return self


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


class ExtractedItem(StrictModel):
    """One proposed structured record. Unknown fields stay None, never guessed."""

    kind: ExtractedItemKind
    title: Title
    body: Body = None
    project_hint: str | None = None
    # Task-ish fields
    deadline_local_date: date | None = None
    deadline_at: datetime | None = None
    duration_estimate: Estimate | None = None
    # Event-ish fields
    location_text: str | None = None
    starts_at: datetime | None = None
    local_date: date | None = None
    timezone: TimezoneName | None = None
    attendance_status: AttendanceStatus | None = None
    registration_status: RegistrationStatus | None = None
    # Reminder-ish fields
    remind_at: datetime | None = None
    remind_local_time: Annotated[str | None, Field(default=None, pattern=r"^\d{2}:\d{2}$")] = None
    requested_actions: list[RequestedAction] = Field(default_factory=list)
    # Status updates (kind=status_update): which record(s) the user means.
    update_action: StatusUpdateAction | None = None
    target_ids: list[EntityId] = Field(default_factory=list)
    """Candidate existing event/task ids proposed by the extractor and checked
    against the candidates it was shown. Exactly one is a resolved target; zero
    or several means the user is asked. Never chosen by the server."""
    # Provenance
    missing_fields: list[str] = Field(default_factory=list)
    evidence: list[SourceRef] = Field(default_factory=list)
    confidence: ConfidenceHint | None = None

    @model_validator(mode="after")
    def _evidence_required(self) -> ExtractedItem:
        if not self.evidence:
            raise ValueError("every extracted item must cite at least one source span")
        if (self.kind is ExtractedItemKind.STATUS_UPDATE) != (self.update_action is not None):
            raise ValueError("update_action is required for, and only valid on, status_update items")
        return self


class TargetChoice(StrictModel):
    """An ambiguous status update awaiting an explicit target.

    Recorded when a resolved proposal's status_update item had no single target.
    ``chosen_id`` stays null until the user picks one of ``candidate_ids``; the
    server never picks and never matches by title.
    """

    item_index: Annotated[int, Field(ge=0)]
    update_action: StatusUpdateAction
    candidate_ids: list[EntityId] = Field(default_factory=list)
    chosen_id: EntityId | None = None


class ExtractionProposal(Record):
    """Model output after schema + semantic validation. Not yet authoritative."""

    source_id: EntityId
    source_version: Revision
    items: list[ExtractedItem] = Field(default_factory=list)
    requires_clarification: bool = False
    disposition: ProposalDisposition = ProposalDisposition.PENDING
    extractor_version: str
    model_id: str
    adapter_mode: AdapterMode
    prompt_version: str
    range_index: Annotated[int, Field(ge=0)] = 0
    """Long sources split into ranges; each range completes independently so a
    retry never restarts finished ranges."""
    range_count: Annotated[int, Field(ge=1)] = 1
    created_entity_ids: list[EntityId] = Field(default_factory=list)
    unresolved_reason: Annotated[str | None, Field(default=None, max_length=500)] = None
    target_choices: list[TargetChoice] = Field(default_factory=list)
    """Ambiguous status updates left by resolution; answered via the target route."""


# --------------------------------------------------------------------------- #
# Commitments
# --------------------------------------------------------------------------- #


class Task(Record):
    title: Title
    body: Body = None
    project_id: EntityId | None = None
    state: TaskState = TaskState.INBOX
    deadline_local_date: date | None = None
    deadline_at: datetime | None = None
    timezone: TimezoneName | None = None
    duration_estimate: Estimate | None = None
    priority: Annotated[int, Field(ge=0, le=3)] = 1
    depends_on: list[EntityId] = Field(default_factory=list)
    event_id: EntityId | None = None
    """The event this task serves (registration/preparation follow-ups)."""
    purpose: TaskPurpose | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    completed_at: datetime | None = None
    canceled_at: datetime | None = None
    user_edited_fields: list[str] = Field(default_factory=list)
    """Fields the user confirmed or typed. Automatic reconciliation of a
    corrected transcript must never overwrite these."""
    source_unavailable: bool = False
    """Set when the originating capture was deleted but the task was kept."""

    @model_validator(mode="after")
    def _no_self_dependency(self) -> Task:
        if self.id in self.depends_on:
            raise ValueError("task cannot depend on itself")
        if self.state == TaskState.DONE and self.completed_at is None:
            raise ValueError("done task requires completed_at")
        if self.state == TaskState.CANCELED and self.canceled_at is None:
            raise ValueError("canceled task requires canceled_at")
        return self


class Event(Record):
    title: Title
    body: Body = None
    project_id: EntityId | None = None
    location_text: str | None = None
    venue_address: str | None = None
    meeting_link: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    local_date: date | None = None
    timezone: TimezoneName | None = None
    attendance_status: AttendanceStatus = AttendanceStatus.TENTATIVE
    registration_status: RegistrationStatus = RegistrationStatus.UNKNOWN
    registration_deadline_local_date: date | None = None
    external_ref: str | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    canceled_at: datetime | None = None
    user_edited_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _interval(self) -> Event:
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.starts_at and not self.timezone:
            raise ValueError("a precise start requires a timezone for local rendering")
        return self


class Decision(Record):
    """A chosen direction. Superseding preserves the prior decision."""

    title: Title
    body: Body = None
    project_id: EntityId | None = None
    supersedes_id: EntityId | None = None
    decided_at: datetime
    source_refs: list[SourceRef] = Field(default_factory=list)
    validity: MemoryValidity = MemoryValidity.CURRENT


class Attempt(StrictModel):
    """A tried-and-failed approach. Preserved so it is not re-suggested."""

    summary: Annotated[str, Field(max_length=2000)]
    outcome: Literal["failed", "partial", "abandoned", "succeeded"]
    recorded_at: datetime
    source_refs: list[SourceRef] = Field(default_factory=list)


class Project(Record):
    title: Title
    aliases: list[str] = Field(default_factory=list)
    """Literal lookup keys — project names, repo names, error codes — because
    ordinary tokenisation loses identifiers."""
    objective: Body = None
    current_blocker_ref: SourceRef | None = None
    next_action_ref: SourceRef | None = None
    current_decision_ids: list[EntityId] = Field(default_factory=list)
    pinned_context: Body = None
    archived: bool = False
    summary_version: Annotated[int, Field(ge=0)] = 0


class WorkPacket(Record):
    """Enough context to resume an engineering task. Derived and cacheable."""

    project_id: EntityId
    task_id: EntityId | None = None
    objective: Annotated[str, Field(max_length=2000)]
    current_state: Annotated[str, Field(max_length=4000)]
    attempts: list[Attempt] = Field(default_factory=list)
    blocker: Annotated[str | None, Field(default=None, max_length=2000)] = None
    next_action: Annotated[str, Field(max_length=2000)]
    completion_criterion: Annotated[str, Field(max_length=2000)]
    estimate: Estimate | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    input_revisions: dict[str, int] = Field(default_factory=dict)
    """entity_id -> revision. Reuse the packet only while these all match."""
    adapter_mode: AdapterMode = AdapterMode.FIXTURE
    generated_at: datetime


class Memory(Record):
    """Searchable, source-backed context. Never authoritative for task status."""

    kind: MemoryKind
    content: Annotated[str, Field(max_length=8000)]
    project_id: EntityId | None = None
    entity_refs: list[EntityId] = Field(default_factory=list)
    validity: MemoryValidity = MemoryValidity.CURRENT
    supersedes_id: EntityId | None = None
    effective_at: datetime
    source_refs: list[SourceRef] = Field(default_factory=list)
    content_hash: Annotated[str, Field(min_length=64, max_length=64)]
    embedding_model_id: str | None = None
    embedding_dimensions: Annotated[int | None, Field(default=None, ge=1)] = None
    indexed_source_version: Revision | None = None
    indexed_at: datetime | None = None

    @model_validator(mode="after")
    def _needs_source(self) -> Memory:
        if not self.source_refs:
            raise ValueError("a memory must cite its source; summaries cannot be the only evidence")
        return self


# --------------------------------------------------------------------------- #
# Scheduling and delivery
# --------------------------------------------------------------------------- #


class Reminder(Record):
    target_id: EntityId
    target_kind: Literal["task", "event"]
    target_revision: Revision
    """The revision the reminder was created against. A due handler re-reads the
    target and suppresses itself when this no longer matches."""
    trigger_at: datetime
    timezone: TimezoneName
    state: ReminderState = ReminderState.PENDING_SCHEDULE
    channel: DeliveryChannel = DeliveryChannel.IN_APP
    purpose: ReminderPurpose = ReminderPurpose.GENERAL
    scheduler_ref: str | None = None
    occurrence_key: str
    """Stable per-occurrence dedupe key; retained across retries."""
    requires_generation: bool = False
    snoozed_until: datetime | None = None
    attempts: Annotated[int, Field(ge=0)] = 0
    last_error: FailureCategory | None = None
    suspended_reason: Annotated[str | None, Field(default=None, max_length=300)] = None
    """Set while a disputed critical date is awaiting clarification."""
    canceled_at: datetime | None = None


class Notification(Record):
    reminder_id: EntityId | None = None
    job_id: EntityId | None = None
    title: Title
    body: Annotated[str, Field(max_length=4000)]
    entity_links: list[EntityId] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    channel: DeliveryChannel
    dedupe_key: str
    delivery_status: DeliveryStatus = DeliveryStatus.PENDING
    delivery_detail: Annotated[str | None, Field(default=None, max_length=500)] = None
    """Evidence from the transport. Acceptance is not proof the user saw it."""
    accepted_at: datetime | None = None
    read_at: datetime | None = None
    dismissed_at: datetime | None = None
    generated: bool = False


class PushSubscription(Record):
    endpoint: Annotated[str, Field(min_length=10, max_length=2000)]
    p256dh: str
    auth: str
    user_agent: Annotated[str | None, Field(default=None, max_length=300)] = None
    disabled_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Availability, preferences, plans
# --------------------------------------------------------------------------- #


class TimeWindow(StrictModel):
    """Local wall-clock window, e.g. 09:00-17:00. Not an instant."""

    start: Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]
    end: Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]


class Budgets(StrictModel):
    """Enforced before paid calls, not merely displayed afterwards."""

    daily_micro_usd: Annotated[int, Field(ge=0)] = 500_000
    monthly_micro_usd: Annotated[int, Field(ge=0)] = 10_000_000
    max_extraction_input_tokens: Annotated[int, Field(ge=256)] = 4000
    max_extraction_output_tokens: Annotated[int, Field(ge=128)] = 1200
    max_planning_input_tokens: Annotated[int, Field(ge=512)] = 8000
    max_planning_output_tokens: Annotated[int, Field(ge=128)] = 2000
    max_tool_iterations: Annotated[int, Field(ge=1, le=8)] = 4
    max_audio_seconds: Annotated[int, Field(ge=1, le=3600)] = 300
    max_upload_bytes: Annotated[int, Field(ge=1024)] = 25 * 1024 * 1024
    max_speech_concurrency: Annotated[int, Field(ge=1, le=8)] = 1
    allow_escalation_model: bool = False


class DictionaryEntry(StrictModel):
    """Preferred spelling plus the spoken/misheard forms that map to it."""

    term: Annotated[str, Field(min_length=1, max_length=64)]
    aliases: Annotated[list[Annotated[str, Field(min_length=1, max_length=64)]], Field(max_length=8)] = Field(
        default_factory=list
    )


class Snippet(StrictModel):
    """Spoken trigger phrase that is replaced verbatim by ``expansion``."""

    trigger: Annotated[str, Field(min_length=2, max_length=64)]
    expansion: Annotated[str, Field(min_length=1, max_length=2000)]


class FlowSettings(StrictModel):
    """Dictation ("Flow") behaviour. Cleanup is deterministic: no model call,
    so dictation has no spend path."""

    dictionary: Annotated[list[DictionaryEntry], Field(max_length=200)] = Field(default_factory=list)
    snippets: Annotated[list[Snippet], Field(max_length=100)] = Field(default_factory=list)
    #: app id/name substring (lowercase) -> style. Unmatched apps use ``standard``.
    app_styles: Annotated[dict[str, Literal["standard", "casual", "literal"]], Field(max_length=50)] = Field(
        default_factory=dict
    )
    remove_fillers: bool = True
    #: Keep each dictation as a Metis source. Off by default: dictating into
    #: another app is not the same as capturing a thought.
    keep_history: bool = False


class Preferences(Record):
    timezone: TimezoneName = "America/New_York"
    work_hours: list[TimeWindow] = Field(default_factory=lambda: [TimeWindow(start="09:00", end="18:00")])
    quiet_hours: list[TimeWindow] = Field(default_factory=lambda: [TimeWindow(start="22:00", end="08:00")])
    break_minutes: Annotated[int, Field(ge=0, le=120)] = 10
    transition_minutes: Annotated[int, Field(ge=0, le=120)] = 0
    daily_planning_local_time: Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")] = "08:00"
    daily_planning_enabled: bool = True
    #: Provider sync state of the daily schedule. "scheduled" requires a
    #: confirmed provider call, same rule as reminders.
    daily_schedule_state: Literal["disabled", "pending_schedule", "scheduled", "schedule_failed"] = (
        "pending_schedule"
    )
    max_focus_tasks: Annotated[int, Field(ge=1, le=10)] = 3
    planning_horizon_days: Annotated[int, Field(ge=1, le=30)] = 7
    channels: list[DeliveryChannel] = Field(default_factory=lambda: [DeliveryChannel.IN_APP])
    speech_model_id: str = "small.en"
    budgets: Budgets = Field(default_factory=Budgets)
    source_retention_days: Annotated[int, Field(ge=1, le=3650)] = 30
    delete_commitments_with_source: bool = False
    flow: FlowSettings = Field(default_factory=FlowSettings)


class AvailabilityBlock(Record):
    """Manually captured fixed block. No external calendar provider required."""

    local_date: date
    timezone: TimezoneName
    window: TimeWindow
    kind: Literal["available", "busy"]
    label: Annotated[str | None, Field(default=None, max_length=200)] = None


class PlanBlock(StrictModel):
    task_id: EntityId | None = None
    event_id: EntityId | None = None
    starts_at: datetime
    ends_at: datetime
    title: Title
    estimate: Estimate | None = None
    next_steps: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list)
    completion_criterion: Annotated[str | None, Field(default=None, max_length=1000)] = None
    rationale: Annotated[str | None, Field(default=None, max_length=1000)] = None
    evidence: list[Evidence] = Field(default_factory=list)
    pinned: bool = False
    fixed: bool = False
    """True for an actual appointment. A fixed block is never silently moved."""

    @model_validator(mode="after")
    def _interval_and_target(self) -> PlanBlock:
        if self.ends_at <= self.starts_at:
            raise ValueError("plan block must have positive duration")
        if (self.task_id is None) == (self.event_id is None):
            raise ValueError("plan block references exactly one of task_id / event_id")
        return self


class ValidationIssue(StrictModel):
    code: str
    message: Annotated[str, Field(max_length=500)]
    block_index: Annotated[int | None, Field(default=None, ge=0)] = None
    severity: Literal["error", "warning"] = "error"


class PlanValidation(StrictModel):
    """Produced by application code. The model cannot mark its own plan valid."""

    valid: bool
    checked_by: Literal["application"] = "application"
    issues: list[ValidationIssue] = Field(default_factory=list)
    capacity_minutes: Annotated[int, Field(ge=0)]
    scheduled_minutes: Annotated[int, Field(ge=0)]
    inputs_complete: bool = True
    """False when a bounded read limit truncated the obligation scan — the plan
    may then not claim complete coverage."""


class Plan(Record):
    local_date: date
    timezone: TimezoneName
    planning_epoch: Annotated[int, Field(ge=0)]
    input_revisions: dict[str, int] = Field(default_factory=dict)
    time_budget_minutes: Annotated[int, Field(ge=0)]
    status: PlanStatus = PlanStatus.PENDING
    blocks: list[PlanBlock] = Field(default_factory=list)
    overflow_task_ids: list[EntityId] = Field(default_factory=list)
    assumptions: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list)
    validation: PlanValidation | None = None
    adapter_mode: AdapterMode = AdapterMode.FIXTURE
    model_id: str | None = None
    prompt_version: str | None = None
    accepted_at: datetime | None = None
    change_summary: Annotated[str | None, Field(default=None, max_length=500)] = None


# --------------------------------------------------------------------------- #
# Jobs, outbox, budget ledger
# --------------------------------------------------------------------------- #


class Job(Record):
    job_type: str
    state: JobState = JobState.QUEUED
    target_id: EntityId | None = None
    target_revision: Revision | None = None
    correlation_id: str
    idempotency_key: str
    attempts: Annotated[int, Field(ge=0)] = 0
    max_attempts: Annotated[int, Field(ge=1, le=20)] = 5
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    failure: FailureCategory | None = None
    error_message: Annotated[str | None, Field(default=None, max_length=1000)] = None
    result_refs: dict[str, str] = Field(default_factory=dict)
    adapter_mode: AdapterMode = AdapterMode.FIXTURE
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metrics: dict[str, float] = Field(default_factory=dict)


class OutboxEvent(Record):
    """Written in the same transaction as the entity change it describes."""

    event_type: str
    event_version: Annotated[int, Field(ge=1)] = 1
    target_id: EntityId | None = None
    target_revision: Revision | None = None
    correlation_id: str
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    state: OutboxState = OutboxState.PENDING
    attempts: Annotated[int, Field(ge=0)] = 0
    available_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    failure: FailureCategory | None = None
    error_message: Annotated[str | None, Field(default=None, max_length=1000)] = None


class BudgetReservation(Record):
    """Atomic hold taken before a paid call, settled against actual usage."""

    operation: str
    model_id: str | None = None
    reserved_micro_usd: Annotated[int, Field(ge=0)]
    actual_micro_usd: Annotated[int | None, Field(default=None, ge=0)] = None
    state: Literal["reserved", "settled", "released", "retained_uncertain"] = "reserved"
    input_tokens: Annotated[int | None, Field(default=None, ge=0)] = None
    output_tokens: Annotated[int | None, Field(default=None, ge=0)] = None
    cache_read_tokens: Annotated[int | None, Field(default=None, ge=0)] = None
    correlation_id: str
    local_date: date
    settled_at: datetime | None = None


class UsageDay(Record):
    """Per-user daily rollup used for the pre-call budget check."""

    local_date: date
    reserved_micro_usd: Annotated[int, Field(ge=0)] = 0
    settled_micro_usd: Annotated[int, Field(ge=0)] = 0
    model_calls: Annotated[int, Field(ge=0)] = 0
    audio_seconds: Annotated[float, Field(ge=0)] = 0.0
    speech_jobs: Annotated[int, Field(ge=0)] = 0

    @property
    def committed_micro_usd(self) -> int:
        """Conservative figure the budget gate compares against."""
        return self.reserved_micro_usd + self.settled_micro_usd


class Tombstone(Record):
    """Immediate access removal; async purge of derived copies follows."""

    target_id: EntityId
    target_kind: str
    reason: Literal["user_delete", "retention", "admin"]
    purge_job_id: EntityId | None = None
    purge_completed_at: datetime | None = None


ENTITY_MODELS: dict[str, type[Record]] = {
    "source": SourceArtifact,
    "proposal": ExtractionProposal,
    "task": Task,
    "event": Event,
    "decision": Decision,
    "project": Project,
    "work_packet": WorkPacket,
    "memory": Memory,
    "reminder": Reminder,
    "notification": Notification,
    "push_subscription": PushSubscription,
    "preferences": Preferences,
    "availability": AvailabilityBlock,
    "plan": Plan,
    "job": Job,
    "outbox": OutboxEvent,
    "budget_reservation": BudgetReservation,
    "usage_day": UsageDay,
    "tombstone": Tombstone,
}
