"""Scheduled daily planning: sync events, suppression, idempotency, worker path."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from metis_api.lambda_entry import daily_planning
from metis_api.planning import handle_daily_due, run_local_daily_due
from metis_contracts import ErrorCode, EventType, MetisError, Plan, reset_settings_cache

USER = "demo_authenticated_subject"
NY = "America/New_York"
NOON = "2026-10-05T12:00:00+00:00"  # 08:00 in New York (EDT); clock is 14:00Z


def patch_prefs(client, **fields):
    current = client.get("/v1/preferences").json()
    response = client.patch("/v1/preferences", json={"expected_revision": current["revision"], **fields})
    assert response.status_code == 200, response.text
    return response.json()


def sync_events(adapters):
    return [
        e
        for e in adapters.state_store.claim_outbox(owner="t", limit=100, lease_seconds=1)
        if e.event_type == EventType.DAILY_PLAN_SCHEDULE_SYNC
    ]


def due(adapters, scheduled_time=NOON, **kw):
    return handle_daily_due(
        adapters.state_store, adapters.clock, user_id=USER, scheduled_time=scheduled_time, **kw
    )


# --- schedule sync ---------------------------------------------------------- #


def test_preference_change_raises_sync_in_same_transaction(client, adapters):
    prefs = patch_prefs(client, daily_planning_local_time="07:30")
    assert prefs["daily_schedule_state"] == "pending_schedule"
    (event,) = sync_events(adapters)
    assert event.target_id == prefs["id"]
    assert event.target_revision == prefs["revision"]
    assert event.payload == {"enabled": True}
    stored = client.get("/v1/preferences").json()
    assert stored["daily_schedule_state"] == "pending_schedule"
    assert stored["revision"] == event.target_revision


def test_disable_syncs_enabled_false_and_unrelated_patch_does_not(client, adapters):
    patch_prefs(client, break_minutes=15)  # first write: schedules once
    off = patch_prefs(client, daily_planning_enabled=False)
    assert off["daily_schedule_state"] == "pending_schedule"
    prefs = patch_prefs(client, break_minutes=20)
    assert prefs["daily_schedule_state"] == "pending_schedule"  # unrelated patch leaves state alone
    events = sorted(sync_events(adapters), key=lambda e: e.target_revision)
    assert [e.payload["enabled"] for e in events] == [True, False]


# --- scheduled invocation --------------------------------------------------- #


def test_due_enqueues_one_plan_and_worker_generates_it(client, adapters, worker):
    patch_prefs(client)
    result = due(adapters)
    assert result["enqueued"] == 1
    worker()
    plan = adapters.state_store.get(USER, "plan", result["plan_id"])
    assert isinstance(plan, Plan)
    assert plan.local_date.isoformat() == "2026-10-05"
    assert plan.status.value in ("proposed", "fallback")
    assert plan.planning_epoch == adapters.state_store.get_planning_epoch(USER)


def test_duplicate_invocation_is_suppressed(client, adapters):
    patch_prefs(client)
    first = due(adapters)
    second = due(adapters)
    assert (first["enqueued"], second["enqueued"], second["reason"]) == (1, 0, "duplicate")
    assert second["plan_id"] == first["plan_id"]


def test_payload_cannot_choose_owner_or_date(client, adapters):
    patch_prefs(client)
    assert due(adapters, scheduled_time="2026-10-04T12:00:00+00:00")["reason"] == "stale_date"
    assert due(adapters, scheduled_time="2026-10-06T12:00:00+00:00")["reason"] == "invalid_scheduled_time"
    other = handle_daily_due(
        adapters.state_store, adapters.clock, user_id="someone_else", scheduled_time=NOON
    )
    assert other["reason"] == "no_preferences"


def test_disabled_suppresses(client, adapters):
    patch_prefs(client, daily_planning_enabled=False)
    assert due(adapters) == {"enqueued": 0, "reason": "disabled"}


def test_time_and_timezone_changes_make_old_schedule_stale(client, adapters):
    patch_prefs(client, daily_planning_local_time="09:00")
    assert due(adapters)["reason"] == "stale_schedule"  # 08:00 fire after move to 09:00
    patch_prefs(client, daily_planning_local_time="08:00", timezone="Europe/London")
    assert due(adapters)["reason"] == "stale_schedule"  # 12:00Z is 13:00 in London


def test_dst_transitions(client, adapters, clock):
    patch_prefs(client, daily_planning_local_time="08:00")
    clock.set(datetime(2026, 3, 9, 13, 0, tzinfo=UTC))  # EDT again; 08:00 EDT = 12:00Z
    assert due(adapters, "2026-03-09T12:00:00Z")["enqueued"] == 1
    # A fixed-UTC schedule left over from winter (13:00Z) is 09:00 EDT: stale.
    clock.set(datetime(2026, 3, 10, 14, 0, tzinfo=UTC))
    assert due(adapters, "2026-03-10T13:00:00Z")["reason"] == "stale_schedule"


def test_dst_gap_time_fires_once_shifted(client, adapters, clock):
    patch_prefs(client, daily_planning_local_time="02:30")
    clock.set(datetime(2026, 3, 8, 8, 0, tzinfo=UTC))  # 04:00 EDT, gap day
    assert due(adapters, "2026-03-08T07:30:00Z")["enqueued"] == 1  # 03:30 EDT
    assert due(adapters, "2026-03-08T07:30:00Z")["reason"] == "duplicate"


def test_local_timer_path(client, adapters, clock):
    patch_prefs(client)
    assert adapters.schedule_store.upsert_daily(
        user_id=USER, local_time="08:00", timezone=NY, payload={"user_id": USER}
    )
    clock.set(datetime(2026, 10, 5, 14, 0, tzinfo=UTC))
    adapters.schedule_store.daily[USER] = (
        "08:00",
        NY,
        datetime(2026, 10, 1, tzinfo=UTC),
        {},
    )
    (first,) = run_local_daily_due(adapters.state_store, adapters.clock, adapters.schedule_store)
    assert first["enqueued"] == 1
    (again,) = run_local_daily_due(adapters.state_store, adapters.clock, adapters.schedule_store)
    assert again["reason"] == "duplicate"


def test_lambda_entry_uses_server_side_state(client, adapters, monkeypatch):
    patch_prefs(client)
    monkeypatch.setattr("metis_api.lambda_entry._worker_context", lambda: _Ctx(adapters))
    out = daily_planning({"user_id": USER, "scheduled_time": NOON}, None)
    assert out["enqueued"] == 1


class _Ctx:
    def __init__(self, a):
        self.state_store, self.clock = a.state_store, a.clock


# --- worker-path degradation ------------------------------------------------- #


def _ready_job_plan(adapters, result):
    plan = adapters.state_store.get(USER, "plan", result["plan_id"])
    job = adapters.state_store.get(USER, "job", result["job_id"])
    return plan, job


def test_missing_availability_still_yields_honest_plan(client, adapters, worker):
    patch_prefs(client)
    result = due(adapters)
    worker()
    plan, job = _ready_job_plan(adapters, result)
    assert job.state.value == "ready"
    assert plan.validation is not None
    assert "availability_assumed" in [i.code for i in plan.validation.issues]


def test_budget_exhaustion_falls_back_without_failing(client, adapters, worker, monkeypatch):
    monkeypatch.setenv("METIS_DAILY_MICRO_USD", "0")
    reset_settings_cache()
    client.post(
        "/v1/tasks",
        json={"title": "Submit the application", "deadline_local_date": "2026-10-12", "timezone": NY},
        headers={"Idempotency-Key": "t1"},
    )
    patch_prefs(client)
    result = due(adapters)
    worker()
    plan, job = _ready_job_plan(adapters, result)
    assert job.state.value == "ready"
    assert plan.status.value in ("proposed", "fallback")


def test_provider_failure_falls_back_without_failing(client, adapters, worker, monkeypatch):
    def boom(**_):
        raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "down")

    monkeypatch.setattr(adapters.inference, "complete_json", boom)
    client.post(
        "/v1/tasks",
        json={"title": "Submit the application", "deadline_local_date": "2026-10-12", "timezone": NY},
        headers={"Idempotency-Key": "t1"},
    )
    patch_prefs(client)
    result = due(adapters)
    worker()
    plan, job = _ready_job_plan(adapters, result)
    assert job.state.value == "ready"
    assert plan.id == result["plan_id"]


@pytest.mark.parametrize("bad", ["not-a-time", "2026-10-05T12:00:00"])
def test_malformed_scheduled_time_is_rejected(client, adapters, bad):
    patch_prefs(client)
    assert due(adapters, bad)["reason"] == "invalid_scheduled_time"
