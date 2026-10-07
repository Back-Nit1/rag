"""Daily planning: overflow, invalidation, scheduled idempotency, fixed events."""

from __future__ import annotations

from datetime import UTC, datetime

from e2e_support import NY, T0, USER, key, make_task, patch

TODAY = "2026-10-05"


def _window(h, start="11:00", end="12:00", block_id="avail_1", kind="available"):
    response = h.client.post(
        "/v1/availability",
        json={
            "id": block_id, "user_id": "ignored", "created_at": T0.isoformat(),
            "updated_at": T0.isoformat(), "local_date": TODAY, "timezone": NY,
            "window": {"start": start, "end": end}, "kind": kind,
        },
    )
    assert response.status_code == 201, response.status_code
    return response.json()


def _ready_task(h, title, minutes, deadline="2026-10-07"):
    return make_task(
        h, title, state="ready", deadline_local_date=deadline,
        duration_estimate={"minutes": minutes, "provenance": "user"},
    )


def _plan(h):
    response = h.client.post(
        "/v1/plans", json={"local_date": TODAY, "timezone": NY}, headers={"Idempotency-Key": key("plan")}
    )
    assert response.status_code == 202
    h.tick()
    return h.client.get(f"/v1/plans/{response.json()['plan_id']}").json()


def _minutes(block):
    start = datetime.fromisoformat(block["starts_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(block["ends_at"].replace("Z", "+00:00"))
    return (end - start).total_seconds() / 60


def test_insufficient_capacity_returns_overflow_not_invented_time(h):
    _window(h, "11:00", "12:00")  # 60 minutes of real capacity
    tasks = [_ready_task(h, f"Deep work {i}", 45) for i in range(3)]
    plan = _plan(h)
    assert plan["validation"]["capacity_minutes"] == 60
    assert sum(_minutes(b) for b in plan["blocks"]) <= 60, "never fitted into invented free time"
    ids = {t["id"] for t in tasks}
    scheduled = {b["task_id"] for b in plan["blocks"]}
    assert ids - scheduled <= set(plan["overflow_task_ids"])
    assert plan["overflow_task_ids"], "infeasible work is exposed"
    assert plan["validation"]["checked_by"] == "application"
    assert plan["assumptions"] or plan["validation"]["issues"] is not None


def test_overflow_is_explained_to_the_user(h):
    _window(h, "11:00", "11:30")
    _ready_task(h, "Long task", 120)
    plan = _plan(h)
    assert plan["overflow_task_ids"]
    issues = {i["code"]: i["message"] for i in plan["validation"]["issues"]}
    assert "overflow_present" in issues, "the plan must say work did not fit"
    assert plan["validation"]["capacity_minutes"] == 30 and plan["validation"]["scheduled_minutes"] == 0


def test_dated_task_is_retained_in_the_plan_or_overflow_whatever_its_note_says(h):
    _window(h, "11:00", "17:00")
    task = make_task(
        h, "Renew passport", body="completely unrelated to anything else here",
        state="ready", deadline_local_date="2026-10-08",
        duration_estimate={"minutes": 30, "provenance": "user"},
    )
    plan = _plan(h)
    assert task["id"] in {b["task_id"] for b in plan["blocks"]} | set(plan["overflow_task_ids"])


def test_fixed_event_is_preserved_and_not_double_booked(h):
    _window(h, "11:00", "17:00")
    event = h.client.post(
        "/v1/events",
        json={"title": "Team sync", "starts_at": "2026-10-05T16:00:00Z", "ends_at": "2026-10-05T17:00:00Z",
              "local_date": TODAY, "timezone": NY, "attendance_status": "committed"},
        headers={"Idempotency-Key": key("ev")},
    )
    assert event.status_code == 201, event.status_code
    _ready_task(h, "Write report", 60)
    plan = _plan(h)
    work = [b for b in plan["blocks"] if not b["fixed"]]
    fixed_start = datetime.fromisoformat("2026-10-05T16:00:00+00:00")
    fixed_end = datetime.fromisoformat("2026-10-05T17:00:00+00:00")
    for block in work:
        s = datetime.fromisoformat(block["starts_at"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(block["ends_at"].replace("Z", "+00:00"))
        assert e <= fixed_start or s >= fixed_end, "a work block overlaps a fixed event"


def test_task_revision_after_generation_invalidates_the_plan_before_acceptance(h):
    _window(h)
    task = _ready_task(h, "Submit the application", 30)
    plan = _plan(h)
    assert plan["status"] in ("proposed", "fallback")
    patch(h, f"/v1/tasks/{task['id']}", task, priority=3)
    today = h.client.get("/v1/today").json()
    assert today["plan_stale"] is True and today["plan_stale_reason"]
    refused = h.client.post(
        f"/v1/plans/{plan['id']}/accept",
        json={"expected_revision": plan["revision"], "expected_planning_epoch": plan["planning_epoch"]},
    )
    assert refused.status_code == 409 and refused.json()["code"] == "epoch_conflict"
    assert h.client.get(f"/v1/plans/{plan['id']}").json()["status"] == "invalidated"


def test_availability_delete_makes_an_existing_plan_stale(h):
    block = _window(h)
    _plan(h)
    h.client.delete(f"/v1/availability/{block['id']}?expected_revision={block['revision']}")
    assert h.client.get("/v1/today").json()["plan_stale"] is True


def test_scheduled_daily_planning_runs_once_per_day(h):
    prefs = h.client.get("/v1/preferences").json()
    changed = h.client.patch(
        "/v1/preferences", json={"expected_revision": prefs["revision"], "daily_planning_local_time": "07:30"}
    )
    assert changed.status_code == 200
    assert changed.json()["daily_schedule_state"] == "pending_schedule", "not scheduled until synced"
    h.tick()
    assert h.client.get("/v1/preferences").json()["daily_schedule_state"] == "scheduled"
    # The schedule was armed after today's 07:30 had passed: today's occurrence is not back-filled.
    h.tick()
    assert h.state.list_plans(USER, limit=50).items == []
    h.clock.set(datetime(2026, 10, 6, 12, 0, tzinfo=UTC))  # 08:00 New York next day
    for _ in range(3):
        h.tick()
    plans = h.state.list_plans(USER, limit=50).items
    assert [p.local_date.isoformat() for p in plans] == ["2026-10-06"], "one plan however many ticks"
    assert plans[0].status.value in ("proposed", "fallback")
    h.clock.set(datetime(2026, 10, 7, 12, 0, tzinfo=UTC))
    for _ in range(2):
        h.tick()
    days = sorted(p.local_date.isoformat() for p in h.state.list_plans(USER, limit=50).items)
    assert days == ["2026-10-06", "2026-10-07"]
