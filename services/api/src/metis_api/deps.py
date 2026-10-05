"""Shared request context: adapters, principal, idempotency and rate limiting.

Adapters are built once per process and injected, so a test can swap any of
them for a fixture and the whole API behaves deterministically.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from metis_contracts import ErrorCode, MetisError, SystemClock, new_id, settings
from metis_contracts.common import Record
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

from .auth import Principal, principal_from_header

log = logging.getLogger("metis.api.deps")


@dataclass
class Adapters:
    """Everything the routes are allowed to touch.

    Typed against the port protocols in `metis_contracts.ports`, so an adapter
    that drifts from its published interface fails the typecheck here instead
    of at runtime in a route.
    """

    clock: Clock
    state_store: StateStore
    source_store: SourceStore
    inference: InferenceClient
    transcriber: SpeechTranscriber | None = None
    schedule_store: ScheduleStore | None = None
    memory_store: MemoryStore | None = None
    embedder: Embedder | None = None
    notification_transports: dict[str, NotificationTransport] = field(default_factory=dict)

    @property
    def retrieval(self) -> Any:
        from metis_retrieval import RetrievalService

        if self.memory_store is None or self.embedder is None:
            raise MetisError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "memory retrieval is unavailable: the retrieval adapters are not configured",
            )
        return RetrievalService(
            state_store=self.state_store,
            memory_store=self.memory_store,
            embedder=self.embedder,
            clock=self.clock,
        )

    @property
    def planner(self) -> Any:
        from metis_planner import PlannerService

        return PlannerService(
            state_store=self.state_store,
            retrieval=self.retrieval,
            inference=self.inference,
            clock=self.clock,
        )

    def describe(self) -> dict[str, str]:
        """Adapter modes, surfaced through /v1/health so the UI can be honest."""

        def mode_of(adapter: Any) -> str:
            return str(getattr(getattr(adapter, "mode", None), "value", getattr(adapter, "mode", "none")))

        return {
            "state_store": mode_of(self.state_store),
            "source_store": mode_of(self.source_store),
            "inference": mode_of(self.inference),
            "transcriber": mode_of(self.transcriber) if self.transcriber else "none",
            "schedule_store": mode_of(self.schedule_store) if self.schedule_store else "none",
            "memory_store": mode_of(self.memory_store) if self.memory_store else "none",
            "embedder": mode_of(self.embedder) if self.embedder else "none",
        }


def build_adapters() -> Adapters:
    """Wire adapters for the configured mode."""
    from metis_pipeline.inference import build_inference_client
    from metis_state import build_source_store, build_state_store

    clock = SystemClock()
    state_store = build_state_store(clock=clock)
    adapters = Adapters(
        clock=clock,
        state_store=state_store,
        source_store=build_source_store(clock=clock),
        inference=build_inference_client(),
    )
    try:
        from metis_speech import build_transcriber

        adapters.transcriber = build_transcriber()
    except ImportError:
        log.warning("metis_speech is unavailable; audio capture will report provider_unavailable")
    try:
        from metis_scheduler import build_notification_transports, build_schedule_store

        adapters.schedule_store = build_schedule_store(state_store=state_store, clock=clock)
        adapters.notification_transports = build_notification_transports(state_store, clock)
    except ImportError:
        log.warning("metis_scheduler is unavailable; reminders cannot be scheduled")
    try:
        from metis_retrieval import build_embedder, build_memory_store

        adapters.memory_store = build_memory_store()
        adapters.embedder = build_embedder()
    except ImportError:
        log.warning("metis_retrieval is unavailable; search will report degraded")
    return adapters


class RateLimiter:
    """Fixed-window per-principal limiter.

    ponytail: an in-process deque per user. Enough for a personal-scale API; a
    shared limiter belongs at the API gateway, not in this process.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window_seconds: int = 60) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                raise MetisError(
                    ErrorCode.RATE_LIMITED,
                    "too many requests",
                    retry_after_seconds=int(window_seconds - (now - hits[0])) + 1,
                )
            hits.append(now)


_limiter = RateLimiter()


@dataclass
class RequestContext:
    """One request's resolved identity, adapters and correlation id."""

    principal: Principal
    adapters: Adapters
    correlation_id: str
    idempotency_key: str | None = None

    @property
    def user_id(self) -> str:
        return self.principal.user_id

    @property
    def state(self) -> StateStore:
        return self.adapters.state_store

    def require[R: Record](self, kind: str, entity_id: str, model: type[R]) -> R:
        """Load an owned entity or raise 404.

        The kind-to-model mapping is the store's, so the isinstance check is
        real: a kind that resolves to the wrong model is a storage defect, not
        a client error.
        """
        record = self.state.get(self.user_id, kind, entity_id)
        if record is None:
            raise MetisError(ErrorCode.NOT_FOUND, f"{kind} {entity_id} not found")
        if not isinstance(record, model):
            raise MetisError(
                ErrorCode.INTERNAL,
                f"{kind} {entity_id} is stored as {type(record).__name__}, not {model.__name__}",
            )
        return record

    def require_idempotency_key(self) -> str:
        if not self.idempotency_key:
            raise MetisError(
                ErrorCode.VALIDATION_FAILED,
                "this operation creates work and requires an Idempotency-Key header",
            )
        return self.idempotency_key

    def replay(self) -> dict[str, Any] | None:
        """A repeated successful mutation returns its stored response."""
        if not self.idempotency_key:
            return None
        return self.state.recall_idempotent(self.user_id, self.idempotency_key)

    def remember(self, response: dict[str, Any]) -> None:
        if self.idempotency_key:
            self.state.remember_idempotent(self.user_id, self.idempotency_key, response)


def get_adapters(request: Request) -> Adapters:
    adapters = getattr(request.app.state, "adapters", None)
    if not isinstance(adapters, Adapters):
        raise MetisError(ErrorCode.INTERNAL, "the application adapters are not initialised")
    return adapters


def get_context(
    request: Request,
    adapters: Annotated[Adapters, Depends(get_adapters)],
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
) -> RequestContext:
    principal = principal_from_header(authorization)
    _limiter.check(principal.user_id, limit=settings().limits.api_rate_per_minute)
    correlation_id = x_correlation_id or new_id("run")
    request.state.correlation_id = correlation_id
    request.state.user_id = principal.user_id
    return RequestContext(
        principal=principal,
        adapters=adapters,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )


Context = Annotated[RequestContext, Depends(get_context)]
