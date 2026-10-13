"""Request and response contracts for every `/v1` route.

Patch models use `None` for "clear this field" and omit the key for
"unchanged" — hence `exclude_unset=True` on the server side. A missing field
must never erase a stored value.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .common import (
    EntityId,
    Estimate,
    Evidence,
    Page,
    Revision,
    SourceRef,
    StrictModel,
    TimezoneName,
)
from .entities import (
    AvailabilityBlock,
    Budgets,
    Event,
    ExtractedItem,
    ExtractionProposal,
    Job,
    Notification,
    Plan,
    PlanBlock,
    Project,
    Reminder,
    SourceArtifact,
    Task,
    TimeWindow,
    TranscriptSegment,
    WorkPacket,
)
from .enums import (
    AttendanceStatus,
    DeliveryChannel,
    JobState,
    MemoryKind,
    RegistrationStatus,
    ReminderPurpose,
    SourceKind,
    TaskState,
)

# --------------------------------------------------------------------------- #
# Captures
# --------------------------------------------------------------------------- #


class CreateCaptureRequest(StrictModel):
    kind: SourceKind
    text: Annotated[str | None, Field(default=None, max_length=100000)] = None
    capture_timezone: TimezoneName
    captured_at: datetime
    """Client capture instant; relative dates resolve against this."""
    content_type: str | None = None
    expected_bytes: Annotated[int | None, Field(default=None, ge=1)] = None
    duration_seconds: Annotated[float | None, Field(default=None, ge=0)] = None
    client_capture_id: Annotated[str | None, Field(default=None, max_length=128)] = None
    """Stable id from the offline outbox so a retry yields one source."""

    @model_validator(mode="after")
    def _kind_requirements(self) -> CreateCaptureRequest:
        if self.kind == SourceKind.TEXT and not self.text:
            raise ValueError("text capture requires text")
        if self.kind != SourceKind.TEXT and self.text:
            raise ValueError("non-text capture must not inline text")
        if self.kind == SourceKind.AUDIO and self.expected_bytes is None:
            raise ValueError("audio capture must declare expected_bytes for the signed policy")
        return self


class UploadInstructions(StrictModel):
    """Short-lived, owner-prefixed upload permission. The server picks the key."""

    url: str
    method: Literal["PUT", "POST"] = "PUT"
    fields: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    object_key: str
    expires_at: datetime
    max_bytes: Annotated[int, Field(ge=1)]


class CreateCaptureResponse(StrictModel):
    capture_id: EntityId
    revision: Revision
    upload: UploadInstructions | None = None
    job_id: EntityId | None = None


class CompleteCaptureRequest(StrictModel):
    expected_revision: Revision
    actual_bytes: Annotated[int | None, Field(default=None, ge=0)] = None
    checksum_sha256: Annotated[str | None, Field(default=None, min_length=64, max_length=64)] = None


class CompleteCaptureResponse(StrictModel):
    capture_id: EntityId
    job_id: EntityId
    revision: Revision


class PatchTranscriptRequest(StrictModel):
    expected_revision: Revision
    text: Annotated[str, Field(min_length=1, max_length=100000)]


class CaptureDetail(StrictModel):
    source: SourceArtifact
    transcript_text: str | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
    proposals: list[ExtractionProposal] = Field(default_factory=list)
    jobs: list[Job] = Field(default_factory=list)
    created_task_ids: list[EntityId] = Field(default_factory=list)
    created_event_ids: list[EntityId] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Proposals
# --------------------------------------------------------------------------- #


class ResolveProposalRequest(StrictModel):
    expected_revision: Revision
    decisions: list[ItemDecision]

    @model_validator(mode="after")
    def _unique_indexes(self) -> ResolveProposalRequest:
        seen = {d.item_index for d in self.decisions}
        if len(seen) != len(self.decisions):
            raise ValueError("duplicate item_index in decisions")
        return self


class ItemDecision(StrictModel):
    item_index: Annotated[int, Field(ge=0)]
    action: Literal["accept", "edit", "dismiss"]
    edited: ExtractedItem | None = None

    @model_validator(mode="after")
    def _edit_payload(self) -> ItemDecision:
        if self.action == "edit" and self.edited is None:
            raise ValueError("edit requires the edited item")
        if self.action != "edit" and self.edited is not None:
            raise ValueError("edited item only valid for action=edit")
        return self


class ChooseTargetRequest(StrictModel):
    """Answer an ambiguous status_update: pick one of the recorded candidates."""

    expected_revision: Revision
    item_index: Annotated[int, Field(ge=0)]
    target_id: EntityId


class ResolveProposalResponse(StrictModel):
    proposal: ExtractionProposal
    created_task_ids: list[EntityId] = Field(default_factory=list)
    created_event_ids: list[EntityId] = Field(default_factory=list)
    created_reminder_ids: list[EntityId] = Field(default_factory=list)
    clarifications: list[ClarificationRequest] = Field(default_factory=list)


class ClarificationRequest(StrictModel):
    """At most one active thread per captured item, with batched fields."""

    clarification_id: EntityId
    source_id: EntityId
    target_id: EntityId | None = None
    question: Annotated[str, Field(max_length=500)]
    missing_fields: list[str]
    blocking: bool = False
    """True when a reminder is suspended pending this answer."""
    candidate_ids: list[EntityId] = Field(default_factory=list)
    """For an ambiguous status update: the records the user may mean. Answer by
    editing the item so ``target_ids`` holds exactly one."""


# --------------------------------------------------------------------------- #
# Tasks / events
# --------------------------------------------------------------------------- #


class PatchTaskRequest(StrictModel):
    """Omitted key = unchanged. Explicit null = clear."""

    expected_revision: Revision
    title: str | None = None
    body: str | None = None
    project_id: EntityId | None = None
    state: TaskState | None = None
    deadline_local_date: date | None = None
    deadline_at: datetime | None = None
    timezone: TimezoneName | None = None
    duration_estimate: Estimate | None = None
    priority: Annotated[int | None, Field(default=None, ge=0, le=3)] = None
    depends_on: list[EntityId] | None = None


class PatchEventRequest(StrictModel):
    expected_revision: Revision
    title: str | None = None
    body: str | None = None
    project_id: EntityId | None = None
    location_text: str | None = None
    venue_address: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    local_date: date | None = None
    timezone: TimezoneName | None = None
    attendance_status: AttendanceStatus | None = None
    registration_status: RegistrationStatus | None = None
    registration_deadline_local_date: date | None = None


class PatchProjectRequest(StrictModel):
    """Omitted key = unchanged. Explicit null clears objective/pinned_context."""

    expected_revision: Revision
    title: Annotated[str | None, Field(default=None, min_length=1, max_length=500)] = None
    aliases: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = None
    objective: Annotated[str | None, Field(default=None, max_length=20000)] = None
    pinned_context: Annotated[str | None, Field(default=None, max_length=20000)] = None
    archived: bool | None = None

    @model_validator(mode="after")
    def _required_not_null(self) -> PatchProjectRequest:
        for name in ("title", "aliases", "archived"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be cleared")
        return self


class AttachCaptureProjectRequest(StrictModel):
    expected_revision: Revision
    project_id: EntityId


class CreateTaskRequest(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    body: Annotated[str | None, Field(default=None, max_length=20000)] = None
    project_id: EntityId | None = None
    state: TaskState = TaskState.INBOX
    deadline_local_date: date | None = None
    timezone: TimezoneName | None = None
    duration_estimate: Estimate | None = None
    priority: Annotated[int, Field(ge=0, le=3)] = 1
    source_refs: list[SourceRef] = Field(default_factory=list)


class CreateEventRequest(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    location_text: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    local_date: date | None = None
    timezone: TimezoneName | None = None
    attendance_status: AttendanceStatus = AttendanceStatus.TENTATIVE
    registration_status: RegistrationStatus = RegistrationStatus.UNKNOWN
    source_refs: list[SourceRef] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Reminders / notifications
# --------------------------------------------------------------------------- #


class CreateReminderRequest(StrictModel):
    target_id: EntityId
    target_kind: Literal["task", "event"]
    expected_target_revision: Revision
    trigger_at: datetime | None = None
    local_date: date | None = None
    local_time: Annotated[str | None, Field(default=None, pattern=r"^\d{2}:\d{2}$")] = None
    timezone: TimezoneName
    channel: DeliveryChannel = DeliveryChannel.IN_APP
    requires_generation: bool = False
    purpose: ReminderPurpose = ReminderPurpose.GENERAL

    @model_validator(mode="after")
    def _resolvable_trigger(self) -> CreateReminderRequest:
        if self.trigger_at is None and not (self.local_date and self.local_time):
            raise ValueError("reminder needs trigger_at or a resolvable local_date + local_time")
        return self


class SnoozeReminderRequest(StrictModel):
    expected_revision: Revision
    snooze_minutes: Annotated[int, Field(ge=1, le=60 * 24 * 7)]


class RescheduleReminderRequest(StrictModel):
    """Move a reminder. The result is `pending_schedule` until the sync confirms."""

    expected_revision: Revision
    trigger_at: datetime | None = None
    local_date: date | None = None
    local_time: Annotated[str | None, Field(default=None, pattern=r"^\d{2}:\d{2}$")] = None
    """Resolved in the reminder's own timezone."""

    @model_validator(mode="after")
    def _resolvable_trigger(self) -> RescheduleReminderRequest:
        if self.trigger_at is None and not (self.local_date and self.local_time):
            raise ValueError("reschedule needs trigger_at or a resolvable local_date + local_time")
        return self


class RegisterPushRequest(StrictModel):
    endpoint: Annotated[str, Field(min_length=10, max_length=2000)]
    p256dh: str
    auth: str
    user_agent: Annotated[str | None, Field(default=None, max_length=300)] = None


# --------------------------------------------------------------------------- #
# Today / plans
# --------------------------------------------------------------------------- #


class TodayResponse(StrictModel):
    """Deterministic authoritative view. Usable with no model or vector store."""

    local_date: date
    timezone: TimezoneName
    fixed_events: list[Event] = Field(default_factory=list)
    open_tasks: list[Task] = Field(default_factory=list)
    overdue_tasks: list[Task] = Field(default_factory=list)
    availability: list[AvailabilityBlock] = Field(default_factory=list)
    plan: Plan | None = None
    plan_stale: bool = False
    plan_stale_reason: Annotated[str | None, Field(default=None, max_length=300)] = None
    planning_epoch: Annotated[int, Field(ge=0)]
    inputs_complete: bool = True
    availability_configured: bool = False
    """False means free time is unknown — never presented as discovered."""


class CreatePlanRequest(StrictModel):
    local_date: date
    timezone: TimezoneName
    time_budget_minutes: Annotated[int | None, Field(default=None, ge=0)] = None
    focus_task_ids: list[EntityId] = Field(default_factory=list)
    notes: Annotated[str | None, Field(default=None, max_length=1000)] = None


class CreatePlanResponse(StrictModel):
    plan_id: EntityId
    job_id: EntityId
    status: str


class AcceptPlanRequest(StrictModel):
    expected_revision: Revision
    expected_planning_epoch: Annotated[int, Field(ge=0)]


class PatchPlanRequest(StrictModel):
    expected_revision: Revision
    blocks: list[PlanBlock] | None = None
    pinned_block_indexes: list[Annotated[int, Field(ge=0)]] | None = None


# --------------------------------------------------------------------------- #
# Search / projects
# --------------------------------------------------------------------------- #


class SearchRequest(StrictModel):
    query: Annotated[str, Field(min_length=1, max_length=1000)]
    project_id: EntityId | None = None
    kinds: list[MemoryKind] = Field(default_factory=list)
    since: date | None = None
    until: date | None = None
    include_historical: bool = False
    mode: Literal["auto", "structured", "semantic", "lexical", "hybrid"] = "auto"
    limit: Annotated[int, Field(ge=1, le=50)] = 20


class SearchResult(StrictModel):
    memory_id: EntityId
    kind: MemoryKind
    content: str
    score: Annotated[float, Field(ge=0.0)]
    """Ranking signal only — not a probability, priority or factual confidence."""
    evidence: Evidence
    project_id: EntityId | None = None


class SearchResponse(StrictModel):
    results: list[SearchResult] = Field(default_factory=list)
    mode_used: str
    index_available: bool = True
    index_stale: bool = False
    degraded_reason: Annotated[str | None, Field(default=None, max_length=300)] = None


class ProjectDetail(StrictModel):
    project: Project
    open_tasks: list[Task] = Field(default_factory=list)
    recent_memories: list[SearchResult] = Field(default_factory=list)
    work_packet: WorkPacket | None = None
    work_packet_stale: bool = False


class ResumeProjectRequest(StrictModel):
    minutes: Annotated[int, Field(ge=5, le=480)] = 30


# --------------------------------------------------------------------------- #
# Preferences / usage / export
# --------------------------------------------------------------------------- #


class PatchPreferencesRequest(StrictModel):
    expected_revision: Revision
    timezone: TimezoneName | None = None
    work_hours: list[TimeWindow] | None = None
    quiet_hours: list[TimeWindow] | None = None
    break_minutes: Annotated[int | None, Field(default=None, ge=0, le=120)] = None
    transition_minutes: Annotated[int | None, Field(default=None, ge=0, le=120)] = None
    daily_planning_local_time: Annotated[str | None, Field(default=None, pattern=r"^\d{2}:\d{2}$")] = None
    daily_planning_enabled: bool | None = None
    max_focus_tasks: Annotated[int | None, Field(default=None, ge=1, le=10)] = None
    channels: list[DeliveryChannel] | None = None
    speech_model_id: str | None = None
    budgets: Budgets | None = None
    source_retention_days: Annotated[int | None, Field(default=None, ge=1, le=3650)] = None
    delete_commitments_with_source: bool | None = None


class UsageResponse(StrictModel):
    """Measured inference usage is reported separately from infra estimates."""

    local_date: date
    measured_model_micro_usd: int
    reserved_micro_usd: int
    estimated_infra_micro_usd: int | None = None
    unmeasured_components: list[str] = Field(default_factory=list)
    model_calls: int
    audio_seconds: float
    speech_jobs: int
    daily_limit_micro_usd: int
    budget_exhausted: bool = False


class CreateExportRequest(StrictModel):
    include_sources: bool = True
    include_memories: bool = True
    include_commitments: bool = True


class ExportStatusResponse(StrictModel):
    """Owner-scoped export state. ``download_url`` exists only once the archive
    is durably stored and verified; queued/running/failed never carry one."""

    job_id: EntityId
    state: JobState
    created_at: datetime
    finished_at: datetime | None = None
    byte_size: Annotated[int | None, Field(default=None, ge=0)] = None
    sha256: Annotated[str | None, Field(default=None, pattern=r"^[0-9a-f]{64}$")] = None
    download_url: str | None = None
    download_expires_at: datetime | None = None
    error_message: Annotated[str | None, Field(default=None, max_length=1000)] = None


class JobAccepted(StrictModel):
    job_id: EntityId
    state: str


class DeleteCaptureResponse(StrictModel):
    capture_id: EntityId
    tombstoned: bool
    purge_job_id: EntityId


class HealthResponse(StrictModel):
    status: Literal["ok", "degraded"]
    mode: str
    adapters: dict[str, str]
    contract_version: int
    build: str | None = None


ResolveProposalRequest.model_rebuild()

TaskPage = Page[Task]
EventPage = Page[Event]
NotificationPage = Page[Notification]
ReminderPage = Page[Reminder]
ProjectPage = Page[Project]
CapturePage = Page[SourceArtifact]
