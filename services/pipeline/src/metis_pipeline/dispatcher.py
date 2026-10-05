"""Outbox dispatcher: the only bridge from committed state to a side effect.

A database write and an external change (scheduler, index, push transport)
cannot be one atomic transaction. So the entity change and its *request* commit
together, and this dispatcher performs the external work afterwards with:

* **leases** — one worker per event, reclaimed after expiry if a worker dies
* **freshness** — a revision-sensitive event is dropped as superseded when the
  target has moved on; a stale action must not undo a newer one
* **bounded retries** — classified by error type, with jittered backoff and a
  dead-letter terminus carrying a user-visible reason
* **no exactly-once claim** — duplicates are made harmless by idempotency keys
  and occurrence claims, and the residual ambiguity is recorded, not denied
"""

from __future__ import annotations

import logging
import random
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from metis_contracts import (
    RETRYABLE_FAILURES,
    ErrorCode,
    EventEnvelope,
    FailureCategory,
    MetisError,
    OutboxEvent,
    settings,
)
from metis_contracts.ports import (
    Clock,
    Embedder,
    InferenceClient,
    MemoryStore,
    NotificationTransport,
    ScheduleStore,
    SourceStore,
    SpeechTranscriber,
    StateStore,
)

log = logging.getLogger("metis.pipeline.dispatcher")

#: A handler receives the envelope and the shared context, and returns nothing.
#: Raising ``MetisError`` classifies the failure; raising anything else is
#: treated as transient.
Handler = Callable[[EventEnvelope, "DispatchContext"], None]


@dataclass
class DispatchContext:
    """Everything a handler is allowed to touch, passed explicitly.

    Handlers receive this rather than reaching for globals, which is what makes
    the whole pipeline testable with a FixedClock and fixture adapters.
    """

    state_store: StateStore
    source_store: SourceStore
    clock: Clock
    inference: InferenceClient | None = None
    transcriber: SpeechTranscriber | None = None
    schedule_store: ScheduleStore | None = None
    notification_transports: dict[str, NotificationTransport] = field(default_factory=dict)
    memory_store: MemoryStore | None = None
    embedder: Embedder | None = None
    worker_id: str = field(default_factory=lambda: f"{socket.gethostname()}:{int(time.time())}")


class HandlerRegistry:
    """Maps event type -> handler. Owned per service, merged by the worker."""

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, event_type: str) -> Callable[[Handler], Handler]:
        def decorate(fn: Handler) -> Handler:
            if event_type in self._handlers:
                raise ValueError(f"handler for {event_type} is already registered")
            self._handlers[event_type] = fn
            return fn

        return decorate

    def merge(self, other: HandlerRegistry) -> None:
        for event_type, handler in other._handlers.items():
            if event_type in self._handlers:
                raise ValueError(f"duplicate handler for {event_type}")
            self._handlers[event_type] = handler

    def get(self, event_type: str) -> Handler | None:
        return self._handlers.get(event_type)

    @property
    def event_types(self) -> frozenset[str]:
        return frozenset(self._handlers)


def backoff_seconds(attempt: int, *, base: int = 5, cap: int = 900) -> int:
    """Exponential backoff with jitter, capped. Never unbounded."""
    ceiling = min(cap, base * (2 ** max(0, attempt - 1)))
    return int(random.uniform(ceiling / 2, ceiling))


def classify(exc: BaseException) -> FailureCategory:
    if isinstance(exc, MetisError):
        mapping = {
            ErrorCode.VALIDATION_FAILED: FailureCategory.VALIDATION,
            ErrorCode.SEMANTIC_VALIDATION_FAILED: FailureCategory.VALIDATION,
            ErrorCode.UNSUPPORTED_MEDIA: FailureCategory.UNSUPPORTED_MEDIA,
            ErrorCode.UPLOAD_TOO_LARGE: FailureCategory.LIMIT_EXCEEDED,
            ErrorCode.LIMIT_EXCEEDED: FailureCategory.LIMIT_EXCEEDED,
            ErrorCode.BUDGET_EXCEEDED: FailureCategory.BUDGET_EXCEEDED,
            ErrorCode.PROVIDER_UNAVAILABLE: FailureCategory.PROVIDER_UNAVAILABLE,
            ErrorCode.RATE_LIMITED: FailureCategory.THROTTLED,
            ErrorCode.FORBIDDEN: FailureCategory.PERMISSION_DENIED,
            ErrorCode.NOT_FOUND: FailureCategory.VALIDATION,
            ErrorCode.TOMBSTONED: FailureCategory.SUPERSEDED,
            ErrorCode.REVISION_CONFLICT: FailureCategory.SUPERSEDED,
        }
        return mapping.get(exc.code, FailureCategory.INTERNAL)
    return FailureCategory.TRANSIENT


def is_fresh(event: EventEnvelope, context: DispatchContext, *, kind: str) -> bool:
    """True when the event's target is still at the revision it was raised for.

    This is what suppresses a superseded reminder that fires anyway, and what
    stops an old extraction overwriting a corrected transcript's results.
    """
    if event.target_id is None or event.target_revision is None:
        return True
    current = context.state_store.get(event.user_id, kind, event.target_id)
    if current is None:
        return False  # deleted or tombstoned
    return int(current.revision) >= int(event.target_revision)


@dataclass
class DispatchResult:
    processed: int = 0
    succeeded: int = 0
    superseded: int = 0
    retried: int = 0
    dead_lettered: int = 0
    unhandled: int = 0


class OutboxDispatcher:
    """Claims leased events and runs their handlers."""

    def __init__(self, registry: HandlerRegistry, context: DispatchContext) -> None:
        self._registry = registry
        self._context = context

    def run_once(self, *, limit: int | None = None) -> DispatchResult:
        limits = settings().limits
        batch = limit or limits.outbox_batch
        events = self._context.state_store.claim_outbox(
            owner=self._context.worker_id, limit=batch, lease_seconds=limits.job_lease_seconds
        )
        result = DispatchResult()
        for event in events:
            result.processed += 1
            self._handle(event, result)
        return result

    def _handle(self, record: OutboxEvent, result: DispatchResult) -> None:
        state = self._context.state_store
        try:
            envelope = EventEnvelope(
                event_id=record.id,
                event_type=record.event_type,
                event_version=record.event_version,
                user_id=record.user_id,
                occurred_at=record.created_at,
                correlation_id=record.correlation_id,
                target_id=record.target_id,
                target_revision=record.target_revision,
                idempotency_key=record.idempotency_key,
                payload=record.payload,
            )
        except Exception as exc:
            # A malformed envelope can never become valid; do not retry it.
            log.error("outbox event %s is malformed: %s", record.id, exc)
            state.complete_outbox(
                record.id,
                user_id=record.user_id,
                state="dead_letter",
                failure=FailureCategory.VALIDATION.value,
                error=f"malformed envelope: {exc}",
            )
            result.dead_lettered += 1
            return

        handler = self._registry.get(envelope.event_type)
        if handler is None:
            log.warning("no handler registered for %s; left pending", envelope.event_type)
            state.complete_outbox(
                envelope.event_id,
                user_id=envelope.user_id,
                state="pending",
                error="no handler registered",
                retry_in_seconds=60,
            )
            result.unhandled += 1
            return

        try:
            handler(envelope, self._context)
        except MetisError as exc:
            category = classify(exc)
            if category is FailureCategory.SUPERSEDED:
                # Not a failure: a newer revision already won.
                log.info("event %s superseded: %s", envelope.event_id, exc.message)
                state.complete_outbox(
                    envelope.event_id,
                    user_id=envelope.user_id,
                    state="superseded",
                    failure=FailureCategory.SUPERSEDED.value,
                    error=exc.message,
                )
                result.superseded += 1
                return
            self._fail(envelope, record, category, exc.message, result)
            return
        except Exception as exc:  # unexpected: treat as transient but bounded
            self._fail(envelope, record, FailureCategory.TRANSIENT, str(exc), result)
            return

        state.complete_outbox(envelope.event_id, user_id=envelope.user_id, state="done")
        result.succeeded += 1

    def _fail(
        self,
        envelope: EventEnvelope,
        record: OutboxEvent,
        category: FailureCategory,
        message: str,
        result: DispatchResult,
    ) -> None:
        state = self._context.state_store
        attempts = record.attempts
        max_attempts = settings().limits.job_max_attempts
        retryable = category in RETRYABLE_FAILURES and attempts < max_attempts
        if retryable:
            delay = backoff_seconds(attempts)
            log.warning(
                "event %s failed (%s), retry %d/%d in %ds: %s",
                envelope.event_id,
                category.value,
                attempts,
                max_attempts,
                delay,
                message,
            )
            state.complete_outbox(
                envelope.event_id,
                user_id=envelope.user_id,
                state="failed",
                failure=category.value,
                error=message,
                retry_in_seconds=delay,
            )
            result.retried += 1
            return
        log.error(
            "event %s dead-lettered after %d attempts (%s): %s",
            envelope.event_id,
            attempts,
            category.value,
            message,
        )
        state.complete_outbox(
            envelope.event_id,
            user_id=envelope.user_id,
            state="dead_letter",
            failure=category.value,
            error=message,
        )
        result.dead_lettered += 1
