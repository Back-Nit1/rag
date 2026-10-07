"""Daily-plan schedule sync: time, timezone, DST, disable/re-enable, staleness, failure."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from metis_contracts import ErrorCode, EventEnvelope, EventType, FixedClock, MetisError
from metis_pipeline.dispatcher import DispatchContext
from metis_scheduler import EventBridgeScheduleStore, LocalScheduleStore, daily_schedule_name
from metis_scheduler.handlers import handle_daily_schedule_sync
from metis_state.sqlite_store import SqliteStateStore

NY = "America/New_York"


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def ctx(tmp_path, now=utc(2026, 10, 5, 10, 0), sched=None):
    clock = FixedClock(now)
    state = SqliteStateStore(str(tmp_path / "s.sqlite"), clock=clock)
    sched = sched or LocalScheduleStore(state_store=state, clock=clock)
    return DispatchContext(state_store=state, source_store=None, clock=clock, schedule_store=sched), clock


def set_prefs(c, user="u1", **kw):
    p = c.state_store.get_preferences(user).model_copy(update=kw)
    stored = c.state_store.get(user, "preferences", p.id) is not None
    with c.state_store.transaction(user) as tx:
        return tx.put("preferences", p, expected_revision=p.revision if stored else None)


def event(user="u1", rev=None, **payload):
    return EventEnvelope(
        event_type=EventType.DAILY_PLAN_SCHEDULE_SYNC,
        user_id=user,
        correlation_id="c",
        target_id=f"pref_{user}",
        target_revision=rev or 1,
        idempotency_key="k",
        payload=payload,
    )


def sync(c, **kw):
    handle_daily_schedule_sync(event(**kw), c)


def test_sync_uses_preferences_and_is_idempotent(tmp_path):
    c, _ = ctx(tmp_path)
    set_prefs(c, timezone=NY, daily_planning_local_time="08:00")
    sync(c)
    sync(c)
    assert list(c.schedule_store.daily) == ["u1"]
    assert c.schedule_store.daily["u1"][:2] == ("08:00", NY)


def test_time_and_zone_change_updates_in_place(tmp_path):
    c, _ = ctx(tmp_path)
    set_prefs(c, timezone=NY)
    sync(c)
    set_prefs(c, timezone="Asia/Tokyo", daily_planning_local_time="06:30")
    sync(c, rev=int(c.state_store.get_preferences("u1").revision))
    assert c.schedule_store.daily["u1"][:2] == ("06:30", "Asia/Tokyo")


def test_stale_event_is_superseded(tmp_path):
    c, _ = ctx(tmp_path)
    set_prefs(c)
    set_prefs(c, daily_planning_local_time="09:00")
    with pytest.raises(MetisError) as e:
        sync(c, rev=1)
    assert e.value.code is ErrorCode.REVISION_CONFLICT
    assert "u1" not in c.schedule_store.daily


def test_disable_then_reenable(tmp_path):
    c, _ = ctx(tmp_path)
    set_prefs(c)
    sync(c)
    sync(c, enabled=False)
    sync(c, enabled=False)  # idempotent delete
    assert c.schedule_store.daily == {}
    sync(c)
    assert "u1" in c.schedule_store.daily


def test_provider_failure_raises_and_retry_recovers(tmp_path):
    class Flaky(LocalScheduleStore):
        n = 1

        def upsert_daily(self, **kw):
            if self.n:
                self.n -= 1
                raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "down")
            return super().upsert_daily(**kw)

    c, _ = ctx(tmp_path, sched=Flaky())
    set_prefs(c)
    with pytest.raises(MetisError):
        sync(c)
    assert c.schedule_store.daily == {}
    sync(c)
    assert "u1" in c.schedule_store.daily


def test_invalid_zone_or_time_rejected():
    s = LocalScheduleStore()
    for t, z in (("25:00", NY), ("08:00", "Mars/Base")):
        with pytest.raises(MetisError):
            s.upsert_daily(user_id="u", local_time=t, timezone=z, payload={})


def test_local_fires_at_local_time_not_utc():
    clock = FixedClock(utc(2026, 10, 5, 0, 0))
    s = LocalScheduleStore(clock=clock)
    s.upsert_daily(user_id="u", local_time="08:00", timezone=NY, payload={})
    assert s.list_due_daily(now=utc(2026, 10, 5, 11, 59)) == []  # 07:59 EDT
    assert s.list_due_daily(now=utc(2026, 10, 5, 12, 0)) == [("u", "2026-10-05")]
    # a zone ahead of UTC: 08:00 Tokyo is 23:00Z the previous day
    s.upsert_daily(user_id="t", local_time="08:00", timezone="Asia/Tokyo", payload={})
    assert ("t", "2026-10-06") in s.list_due_daily(now=utc(2026, 10, 5, 23, 0))


def test_created_after_todays_time_waits_for_tomorrow():
    s = LocalScheduleStore(clock=FixedClock(utc(2026, 10, 5, 15, 0)))
    s.upsert_daily(user_id="u", local_time="08:00", timezone=NY, payload={})
    assert s.list_due_daily(now=utc(2026, 10, 5, 16, 0)) == []
    assert s.list_due_daily(now=utc(2026, 10, 6, 12, 0)) == [("u", "2026-10-06")]


def test_retry_does_not_rearm_a_passed_occurrence():
    clock = FixedClock(utc(2026, 10, 5, 0, 0))
    s = LocalScheduleStore(clock=clock)
    s.upsert_daily(user_id="u", local_time="08:00", timezone=NY, payload={})
    clock.advance(hours=13)
    s.upsert_daily(user_id="u", local_time="08:00", timezone=NY, payload={})
    assert s.list_due_daily(now=utc(2026, 10, 5, 13, 0)) == [("u", "2026-10-05")]


def test_dst_spring_forward_fires_once_shifted():
    # 2026-03-08: 02:30 NY does not exist.
    s = LocalScheduleStore(clock=FixedClock(utc(2026, 3, 7, 0, 0)))
    s.upsert_daily(user_id="u", local_time="02:30", timezone=NY, payload={})
    assert s.list_due_daily(now=utc(2026, 3, 8, 7, 29)) == []
    assert s.list_due_daily(now=utc(2026, 3, 8, 7, 30)) == [("u", "2026-03-08")]
    # next day is plain 02:30 EDT = 06:30Z
    assert s.list_due_daily(now=utc(2026, 3, 9, 6, 30)) == [("u", "2026-03-09")]


def test_dst_fall_back_ambiguous_time_one_local_date():
    # 2026-11-01: 01:30 NY happens twice; first is 05:30Z.
    s = LocalScheduleStore(clock=FixedClock(utc(2026, 10, 31, 0, 0)))
    s.upsert_daily(user_id="u", local_time="01:30", timezone=NY, payload={})
    assert s.list_due_daily(now=utc(2026, 11, 1, 5, 29)) == []
    assert s.list_due_daily(now=utc(2026, 11, 1, 5, 30)) == [("u", "2026-11-01")]
    assert s.list_due_daily(now=utc(2026, 11, 1, 6, 30)) == [("u", "2026-11-01")]  # same date, deduped by API


def _eb(monkeypatch):
    cfg = SimpleNamespace(
        scheduler_group="g", scheduler_role_arn="r", reminder_target_arn="t", region="us-east-1"
    )
    monkeypatch.setattr("metis_scheduler.schedule_store.settings", lambda: SimpleNamespace(aws=cfg))
    client = MagicMock()
    client.exceptions.ConflictException = type("Conflict", (Exception,), {})
    client.exceptions.ResourceNotFoundException = type("NF", (Exception,), {})
    monkeypatch.setitem(__import__("sys").modules, "boto3", SimpleNamespace(client=lambda *a, **k: client))
    return EventBridgeScheduleStore(), client


def test_eventbridge_cron_in_user_zone(monkeypatch):
    s, client = _eb(monkeypatch)
    ref = s.upsert_daily(user_id="u|1", local_time="08:05", timezone=NY, payload={"user_id": "u|1"})
    req = client.create_schedule.call_args.kwargs
    assert req["ScheduleExpression"] == "cron(5 8 * * ? *)"
    assert req["ScheduleExpressionTimezone"] == NY
    assert req["Name"] == daily_schedule_name("u|1") and "|" not in req["Name"]
    assert json.loads(req["Target"]["Input"])["scheduled_time"] == "<aws.scheduler.scheduled-time>"
    assert ref.endswith(req["Name"])


def test_eventbridge_conflict_updates_not_duplicates(monkeypatch):
    s, client = _eb(monkeypatch)
    client.create_schedule.side_effect = client.exceptions.ConflictException()
    s.upsert_daily(user_id="u", local_time="08:00", timezone=NY, payload={})
    client.update_schedule.assert_called_once()


def test_eventbridge_failures_and_missing_delete(monkeypatch):
    s, client = _eb(monkeypatch)
    client.create_schedule.side_effect = RuntimeError("boom")
    with pytest.raises(MetisError) as e:
        s.upsert_daily(user_id="u", local_time="08:00", timezone=NY, payload={})
    assert e.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    client.delete_schedule.side_effect = client.exceptions.ResourceNotFoundException()
    s.delete_daily(user_id="u", scheduler_ref=None)  # already gone: fine
    client.delete_schedule.side_effect = RuntimeError("boom")
    with pytest.raises(MetisError):
        s.delete_daily(user_id="u", scheduler_ref=None)
