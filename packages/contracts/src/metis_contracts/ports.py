"""Port interfaces every Metis adapter implements.

These live in the contracts package because they are contracts: the fixture,
local and cloud implementations of each port must be interchangeable, and
`tests/` exercises all of them through these signatures.

Rules that are easier to state once here than to re-derive per adapter:

* ``user_id`` arguments are always the authenticated subject resolved by the
  API or worker. No implementation may take ownership from a payload.
* Every list/scan operation is bounded and returns a ``Page`` whose
  ``complete`` flag is ``False`` when a configured read limit truncated it.
* Every mutation takes an ``expected_revision`` and raises
  ``MetisError(REVISION_CONFLICT)`` rather than silently overwriting.
* Entity change + outbox append happen inside one ``transaction()`` block.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from .common import Page
from .entities import (
    AvailabilityBlock,
    Event,
    ExtractionProposal,
    Job,
    Memory,
    Notification,
    ObjectRef,
    OutboxEvent,
    Plan,
    Preferences,
    Project,
    Reminder,
    SourceArtifact,
    Task,
    TranscriptSegment,
    UsageDay,
)
from .enums import AdapterMode, DeliveryStatus, JobState, TaskState


@runtime_checkable
class Clock(Protocol):
    """Injected everywhere so tests can advance time deterministically."""

    def now(self) -> datetime: ...

    def today_local(self, timezone_name: str) -> date: ...


@runtime_checkable
class Transaction(Protocol):
    """A unit of work spanning entity writes, outbox appends and epoch bumps."""

    def put(self, kind: str, record: Any, *, expected_revision: int | None = None) -> Any: ...

    def append_outbox(self, event: OutboxEvent) -> None: ...

    def bump_planning_epoch(self, user_id: str) -> int: ...


@runtime_checkable
class StateStore(Protocol):
    """Authoritative state. SQLite locally, DynamoDB in the cloud."""

    mode: AdapterMode

    def transaction(self, user_id: str) -> Any:
        """Context manager yielding a :class:`Transaction`."""
        ...

    # --- reads -------------------------------------------------------------
    def get(self, user_id: str, kind: str, entity_id: str) -> Any | None: ...

    def list_tasks(
        self,
        user_id: str,
        *,
        states: Sequence[TaskState] | None = None,
        project_id: str | None = None,
        due_before: date | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> Page[Task]: ...

    def list_events(
        self,
        user_id: str,
        *,
        local_date: date | None = None,
        until: date | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> Page[Event]: ...

    def list_availability(self, user_id: str, local_date: date) -> list[AvailabilityBlock]: ...

    def list_notifications(
        self, user_id: str, *, cursor: str | None = None, limit: int = 50
    ) -> Page[Notification]: ...

    def list_reminders_for_target(self, user_id: str, target_id: str) -> list[Reminder]: ...

    def get_preferences(self, user_id: str) -> Preferences: ...

    def get_planning_epoch(self, user_id: str) -> int: ...

    # --- jobs and outbox ---------------------------------------------------
    def claim_outbox(self, *, owner: str, limit: int, lease_seconds: int) -> list[OutboxEvent]: ...

    def complete_outbox(
        self, event_id: str, *, user_id: str, state: str, error: str | None = None
    ) -> None: ...

    def claim_job(self, job_id: str, *, user_id: str, owner: str, lease_seconds: int) -> Job | None: ...

    def set_job_state(
        self,
        job_id: str,
        *,
        user_id: str,
        state: JobState,
        failure: str | None = None,
        error_message: str | None = None,
        result_refs: dict[str, str] | None = None,
        metrics: dict[str, float] | None = None,
    ) -> Job: ...

    # --- idempotency -------------------------------------------------------
    def remember_idempotent(self, user_id: str, key: str, response: dict[str, Any]) -> None: ...

    def recall_idempotent(self, user_id: str, key: str) -> dict[str, Any] | None: ...

    # --- budget ------------------------------------------------------------
    def reserve_budget(
        self, user_id: str, *, local_date: date, micro_usd: int, operation: str, correlation_id: str
    ) -> str:
        """Atomically reserve an upper-bound cost. Raises BUDGET_EXCEEDED when
        the reservation would cross the configured cap. Two concurrent workers
        must not both pass on the same remaining budget."""
        ...

    def settle_budget(
        self,
        reservation_id: str,
        *,
        user_id: str,
        actual_micro_usd: int | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        uncertain: bool = False,
    ) -> None:
        """Settle a reservation. ``uncertain=True`` retains a conservative
        charge because a timed-out call may still have been billed."""
        ...

    def get_usage(self, user_id: str, local_date: date) -> UsageDay: ...


@runtime_checkable
class SourceStore(Protocol):
    """Private object storage for recordings, transcripts and exports."""

    mode: AdapterMode

    def upload_target(
        self, user_id: str, *, capture_id: str, content_type: str, max_bytes: int, ttl_seconds: int
    ) -> tuple[ObjectRef, dict[str, Any]]:
        """Return the server-chosen owner-prefixed key plus signed instructions."""
        ...

    def head(self, ref: ObjectRef) -> ObjectRef | None:
        """Actual stored size/checksum, or None. Never trusts a client header."""
        ...

    def get_bytes(self, ref: ObjectRef) -> bytes: ...

    def put_bytes(self, ref: ObjectRef, body: bytes, *, content_type: str) -> ObjectRef: ...

    def delete(self, ref: ObjectRef) -> None: ...

    def owns_key(self, user_id: str, key: str) -> bool:
        """Reject any key outside this owner's prefix before reading it."""
        ...


@runtime_checkable
class SpeechTranscriber(Protocol):
    """Owned ASR. A deterministic adapter, never an agent reasoning over audio."""

    mode: AdapterMode

    def transcribe(
        self,
        audio: bytes | ObjectRef,
        *,
        language: str | None = None,
        glossary: Sequence[str] = (),
        max_seconds: int = 300,
    ) -> tuple[str, list[TranscriptSegment], dict[str, Any]]:
        """Return (text, ordered segments, speech metadata dict).

        Raises MetisError(UNSUPPORTED_MEDIA) for corrupt or unsupported input
        and MetisError(LIMIT_EXCEEDED) when decoded duration exceeds
        ``max_seconds`` — metadata duration is treated as untrusted.
        """
        ...


@runtime_checkable
class InferenceClient(Protocol):
    """Bedrock or fixture. Schema-constrained output plus one bounded repair."""

    mode: AdapterMode
    model_id: str

    def complete_json(
        self,
        *,
        prompt_version: str,
        system: str,
        user_content: str,
        json_schema: dict[str, Any],
        max_input_tokens: int,
        max_output_tokens: int,
        timeout_seconds: float = 30.0,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return (parsed object, usage dict with token counts and model id).

        Tools are disabled: ``user_content`` is data, never an instruction.
        """
        ...

    def estimate_micro_usd(self, *, input_tokens: int, output_tokens: int) -> int: ...


@runtime_checkable
class Embedder(Protocol):
    mode: AdapterMode
    model_id: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@runtime_checkable
class MemoryStore(Protocol):
    """Semantic memory. Ownership filtering is server-enforced, not advisory."""

    mode: AdapterMode
    supports_metadata_filter: bool
    supports_lexical: bool

    def upsert(
        self, user_id: str, memories: Sequence[Memory], vectors: Sequence[Sequence[float]]
    ) -> None: ...

    def query(
        self,
        user_id: str,
        *,
        vector: Sequence[float] | None = None,
        text: str | None = None,
        project_id: str | None = None,
        kinds: Sequence[str] = (),
        include_historical: bool = False,
        limit: int = 20,
    ) -> list[tuple[Memory, float]]:
        """Return (memory, score) pairs. Scores rank; they are not probabilities.
        A result for another user is a bug and must be dropped before any
        prompt is constructed."""
        ...

    def delete(self, user_id: str, memory_ids: Sequence[str]) -> None: ...

    def health(self) -> tuple[bool, str | None]:
        """(available, degraded_reason). Planning must survive False."""
        ...


@runtime_checkable
class ScheduleStore(Protocol):
    """EventBridge Scheduler or a local timer table. Not transactional with the DB."""

    mode: AdapterMode

    def upsert_schedule(
        self, *, reminder_id: str, user_id: str, trigger_at: datetime, payload: dict[str, Any]
    ) -> str:
        """Return the provider schedule reference. Idempotent on reminder_id."""
        ...

    def delete_schedule(self, *, reminder_id: str, scheduler_ref: str | None) -> None:
        """Best effort. A superseded schedule may still fire, so the due
        handler re-validates freshness independently."""
        ...

    def list_due(self, *, now: datetime, limit: int = 50) -> list[str]:
        """Local-mode only: reminder ids whose trigger has passed."""
        ...


@runtime_checkable
class NotificationTransport(Protocol):
    """In-app feed writer, web push, or a fixture sink."""

    mode: AdapterMode
    channel: str

    def supported(self, user_id: str) -> bool: ...

    def send(self, notification: Notification) -> tuple[DeliveryStatus, str | None]:
        """Return (status, transport detail). ACCEPTED means the transport took
        it, never that the user saw it; a lost acknowledgement stays ambiguous."""
        ...


@runtime_checkable
class ExtractionService(Protocol):
    """Transcript -> validated proposal. Owned by services/pipeline."""

    def extract(
        self, source: SourceArtifact, *, source_version: int, char_start: int = 0, char_end: int | None = None
    ) -> ExtractionProposal: ...


@runtime_checkable
class PlannerService(Protocol):
    """Context assembly + generation + deterministic validation."""

    def build_plan(
        self, user_id: str, *, local_date: date, timezone_name: str, time_budget_minutes: int
    ) -> Plan: ...


@runtime_checkable
class RetrievalService(Protocol):
    """The narrow, server-controlled operations the planner may call."""

    def list_eligible_obligations(
        self, user_id: str, *, local_date: date, horizon_days: int
    ) -> tuple[list[Task], list[Event], bool]:
        """(tasks, events, complete). ``complete=False`` forbids claiming a
        plan covers every obligation."""
        ...

    def get_project_history(self, user_id: str, project_id: str, *, limit: int = 20) -> list[Memory]: ...

    def retrieve_supporting_memories(
        self, user_id: str, *, query: str, project_id: str | None, limit: int
    ) -> list[tuple[Memory, float]]: ...

    def lookup_identifier(self, user_id: str, identifier: str) -> list[Project]: ...

    def get_availability(self, user_id: str, local_date: date) -> list[AvailabilityBlock]: ...


PortName = Literal[
    "clock",
    "state_store",
    "source_store",
    "speech_transcriber",
    "inference_client",
    "embedder",
    "memory_store",
    "schedule_store",
    "notification_transport",
]

ALL_PORTS: tuple[PortName, ...] = (
    "clock",
    "state_store",
    "source_store",
    "speech_transcriber",
    "inference_client",
    "embedder",
    "memory_store",
    "schedule_store",
    "notification_transport",
)
