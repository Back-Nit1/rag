"""Local worker tick raises daily plans through the local schedule store."""

from __future__ import annotations

from datetime import UTC, datetime

from metis_contracts import EventEnvelope, EventType, FixedClock
from metis_pipeline.dispatcher import DispatchContext
from metis_pipeline.local_worker import _fire_due_daily_plans
from metis_scheduler import LocalScheduleStore
from metis_scheduler.handlers import handle_daily_schedule_sync
from metis_state.sqlite_store import SqliteStateStore


def _plan_requests(state) -> int:
    events = state.claim_outbox(owner="t", limit=100, lease_seconds=60)
    return sum(e.event_type == EventType.PLAN_REQUESTED for e in events)


def test_tick_enqueues_daily_plan_once(tmp_path):
    clock = FixedClock(datetime(2026, 10, 5, 10, 0, tzinfo=UTC))
    state = SqliteStateStore(str(tmp_path / "s.sqlite"), clock=clock)
    store = LocalScheduleStore(state_store=state, clock=clock)
    ctx = DispatchContext(state_store=state, source_store=None, clock=clock, schedule_store=store)

    prefs = state.get_preferences("u1").model_copy(
        update={"daily_planning_enabled": True, "timezone": "UTC", "daily_planning_local_time": "11:00"}
    )
    with state.transaction("u1") as tx:
        prefs = tx.put("preferences", prefs, expected_revision=None)
    handle_daily_schedule_sync(
        EventEnvelope(
            event_type=EventType.DAILY_PLAN_SCHEDULE_SYNC,
            user_id="u1",
            correlation_id="c",
            target_id=prefs.id,
            target_revision=prefs.revision,
            idempotency_key="k",
            payload={},
        ),
        ctx,
    )

    assert _fire_due_daily_plans(ctx) == 0  # before 11:00
    clock.advance(minutes=61)
    assert _fire_due_daily_plans(ctx) == 1
    assert _fire_due_daily_plans(ctx) == 0  # same occurrence is deduped
    assert _plan_requests(state) == 1


def test_tick_without_schedule_store_is_a_noop(tmp_path):
    clock = FixedClock(datetime(2026, 10, 5, 10, 0, tzinfo=UTC))
    ctx = DispatchContext(state_store=None, source_store=None, clock=clock)
    assert _fire_due_daily_plans(ctx) == 0
