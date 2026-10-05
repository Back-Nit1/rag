"""Dispatcher behaviour: leases, freshness, classification, retry and DLQ."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from metis_contracts import (
    ErrorCode,
    EventEnvelope,
    EventType,
    FailureCategory,
    FixedClock,
    MetisError,
    OutboxEvent,
    Task,
    new_id,
    reset_settings_cache,
)
from metis_pipeline.dispatcher import (
    DispatchContext,
    HandlerRegistry,
    OutboxDispatcher,
    backoff_seconds,
    classify,
    is_fresh,
)
from metis_state.sqlite_store import SqliteStateStore

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
USER = "user_a"


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture()
def store(tmp_path, clock, monkeypatch) -> SqliteStateStore:
    monkeypatch.setenv("METIS_JOB_MAX_ATTEMPTS", "3")
    reset_settings_cache()
    yield SqliteStateStore(str(tmp_path / "s.sqlite"), clock=clock)
    reset_settings_cache()


@pytest.fixture()
def context(store, clock) -> DispatchContext:
    return DispatchContext(state_store=store, source_store=None, clock=clock, worker_id="w1")


def enqueue(
    store, clock, *, event_type=EventType.TASK_CHANGED, target_id=None, target_revision=None, key=None
) -> str:
    event = OutboxEvent(
        id=new_id("obx"),
        user_id=USER,
        created_at=clock.now(),
        updated_at=clock.now(),
        event_type=event_type,
        target_id=target_id,
        target_revision=target_revision,
        correlation_id="corr_1",
        idempotency_key=key or new_id("idem"),
        available_at=clock.now(),
    )
    with store.transaction(USER) as tx:
        tx.append_outbox(event)
    return event.id


def test_a_successful_handler_marks_the_event_done(store, clock, context):
    seen: list[EventEnvelope] = []
    registry = HandlerRegistry()
    registry.register(EventType.TASK_CHANGED)(lambda env, ctx: seen.append(env))
    enqueue(store, clock)

    result = OutboxDispatcher(registry, context).run_once()
    assert (result.processed, result.succeeded) == (1, 1)
    assert len(seen) == 1
    assert OutboxDispatcher(registry, context).run_once().processed == 0


def test_an_unregistered_event_type_is_left_pending_not_lost(store, clock, context):
    enqueue(store, clock)
    result = OutboxDispatcher(HandlerRegistry(), context).run_once()
    assert result.unhandled == 1 and result.dead_lettered == 0
    clock.advance(seconds=120)
    assert store.claim_outbox(owner="w2", limit=5, lease_seconds=60), "the event must still be retrievable"


def test_a_validation_failure_is_dead_lettered_without_retrying(store, clock, context):
    registry = HandlerRegistry()

    def boom(env, ctx):
        raise MetisError(ErrorCode.VALIDATION_FAILED, "the payload is wrong")

    registry.register(EventType.TASK_CHANGED)(boom)
    enqueue(store, clock)
    result = OutboxDispatcher(registry, context).run_once()
    assert result.dead_lettered == 1 and result.retried == 0
    assert [e.event_type for e in store.list_dead_letters()] == [EventType.TASK_CHANGED]


def test_a_transient_failure_retries_then_dead_letters_at_the_cap(store, clock, context):
    registry = HandlerRegistry()

    def flaky(env, ctx):
        raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "bedrock is down")

    registry.register(EventType.TASK_CHANGED)(flaky)
    enqueue(store, clock)
    dispatcher = OutboxDispatcher(registry, context)

    outcomes = []
    for _ in range(4):
        outcomes.append(dispatcher.run_once())
        clock.advance(seconds=1000)
    # 3 total attempts (METIS_JOB_MAX_ATTEMPTS): two retries, then the terminus.
    assert sum(o.retried for o in outcomes) == 2
    assert sum(o.dead_lettered for o in outcomes) == 1
    assert store.list_dead_letters(), "a user-visible terminus exists"


def test_a_superseded_event_is_not_a_failure(store, clock, context):
    registry = HandlerRegistry()

    def stale(env, ctx):
        raise MetisError(ErrorCode.REVISION_CONFLICT, "a newer revision won")

    registry.register(EventType.TASK_CHANGED)(stale)
    enqueue(store, clock)
    result = OutboxDispatcher(registry, context).run_once()
    assert result.superseded == 1
    assert result.retried == 0 and result.dead_lettered == 0


def test_a_malformed_envelope_is_dead_lettered_immediately(store, clock, context):
    # reminder.due is revision-sensitive, so an envelope without a target
    # revision can never become valid.
    enqueue(store, clock, event_type=EventType.REMINDER_DUE, target_id="rem_1", target_revision=None)
    registry = HandlerRegistry()
    registry.register(EventType.REMINDER_DUE)(lambda env, ctx: None)
    result = OutboxDispatcher(registry, context).run_once()
    assert result.dead_lettered == 1


def test_handler_registration_rejects_a_duplicate():
    registry = HandlerRegistry()
    registry.register(EventType.TASK_CHANGED)(lambda env, ctx: None)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(EventType.TASK_CHANGED)(lambda env, ctx: None)


def test_merging_registries_rejects_an_overlap():
    a, b = HandlerRegistry(), HandlerRegistry()
    a.register(EventType.TASK_CHANGED)(lambda env, ctx: None)
    b.register(EventType.TASK_CHANGED)(lambda env, ctx: None)
    with pytest.raises(ValueError, match="duplicate handler"):
        a.merge(b)


# --- freshness -------------------------------------------------------------- #


def envelope(target_id: str, revision: int | None) -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.TASK_CHANGED,
        user_id=USER,
        correlation_id="c",
        idempotency_key="k",
        target_id=target_id,
        target_revision=revision,
    )


def test_freshness_passes_when_the_target_is_at_or_past_the_event_revision(store, clock, context):
    task = Task(id="task_1", user_id=USER, created_at=T0, updated_at=T0, title="Register")
    with store.transaction(USER) as tx:
        stored = tx.put("task", task)
    assert is_fresh(envelope("task_1", stored.revision), context, kind="task") is True


def test_freshness_fails_when_the_event_refers_to_a_future_revision(store, clock, context):
    task = Task(id="task_1", user_id=USER, created_at=T0, updated_at=T0, title="Register")
    with store.transaction(USER) as tx:
        tx.put("task", task)
    assert is_fresh(envelope("task_1", 99), context, kind="task") is False


def test_freshness_fails_for_a_deleted_target(store, clock, context):
    assert is_fresh(envelope("task_missing", 1), context, kind="task") is False


def test_an_event_with_no_target_is_always_fresh(store, clock, context):
    assert is_fresh(envelope("task_1", None), context, kind="task") is True


# --- classification and backoff --------------------------------------------- #


@pytest.mark.parametrize(
    "code,expected",
    [
        (ErrorCode.VALIDATION_FAILED, FailureCategory.VALIDATION),
        (ErrorCode.UNSUPPORTED_MEDIA, FailureCategory.UNSUPPORTED_MEDIA),
        (ErrorCode.BUDGET_EXCEEDED, FailureCategory.BUDGET_EXCEEDED),
        (ErrorCode.PROVIDER_UNAVAILABLE, FailureCategory.PROVIDER_UNAVAILABLE),
        (ErrorCode.RATE_LIMITED, FailureCategory.THROTTLED),
        (ErrorCode.FORBIDDEN, FailureCategory.PERMISSION_DENIED),
        (ErrorCode.REVISION_CONFLICT, FailureCategory.SUPERSEDED),
        (ErrorCode.TOMBSTONED, FailureCategory.SUPERSEDED),
    ],
)
def test_error_codes_map_to_failure_categories(code, expected):
    assert classify(MetisError(code, "x")) is expected


def test_an_unexpected_exception_is_transient():
    assert classify(RuntimeError("boom")) is FailureCategory.TRANSIENT


def test_backoff_grows_and_stays_capped():
    assert backoff_seconds(1) <= backoff_seconds(5) <= 900
    assert all(backoff_seconds(n) <= 900 for n in range(1, 20))
    assert backoff_seconds(1) >= 2
