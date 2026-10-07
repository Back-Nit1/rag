"""Reminder lifecycle through the API, the local worker loop and a controllable clock."""

from __future__ import annotations

from e2e_support import (
    DUE,
    USER,
    add_reminder,
    fire_stale,
    make_task,
    notifications,
    patch,
    reminder,
)


def _scheduled_reminder(h, **task_fields):
    task = make_task(h, title="Register for the event", **task_fields)
    rem = add_reminder(h, task)
    h.tick()
    rem = reminder(h, rem["id"])
    assert rem["state"] == "scheduled", "schedule sync confirmed by the local schedule store"
    return task, rem


def test_due_reminder_delivers_once_with_transport_evidence(h):
    _, rem = _scheduled_reminder(h)
    assert notifications(h) == [], "nothing is delivered before the trigger time"
    h.clock.set(DUE)
    h.tick()
    h.tick()  # a repeated timer pass must not deliver again
    (note,) = notifications(h)
    assert note["reminder_id"] == rem["id"]
    assert note["delivery_status"] in ("accepted", "unsupported"), "status comes from the adapter"
    assert note["delivery_detail"] or note["delivery_status"] == "accepted"
    assert reminder(h, rem["id"])["state"] == "delivered"


def test_changed_deadline_reconciles_the_old_reminder(h):
    task, rem = _scheduled_reminder(h, deadline_local_date="2026-10-12")
    old_occurrence = reminder(h, rem["id"])["occurrence_key"]
    patch(h, f"/v1/tasks/{task['id']}", task, deadline_local_date="2026-10-09")
    current = reminder(h, rem["id"])
    assert current["target_revision"] == task["revision"] + 1, "reminder follows the revised target"
    assert current["state"] in ("pending_schedule", "scheduled"), "never silently lost"
    h.tick()  # re-sync
    h.clock.set(DUE)
    fire_stale(h, rem["id"], old_occurrence)  # the old external schedule fires again
    h.tick()
    delivered = notifications(h)
    assert len(delivered) == 1, "exactly one delivery: the stale occurrence is suppressed"
    assert "2026-10-09" in delivered[0]["body"], "delivery reflects the new deadline"
    assert "2026-10-12" not in delivered[0]["body"]


def test_canceled_task_suppresses_a_stale_due_delivery(h):
    task, rem = _scheduled_reminder(h)
    occurrence = reminder(h, rem["id"])["occurrence_key"]
    patch(h, f"/v1/tasks/{task['id']}", task, state="canceled")
    assert reminder(h, rem["id"])["state"] == "canceled"
    h.clock.set(DUE)
    fire_stale(h, rem["id"], occurrence)
    h.tick()
    assert notifications(h) == []
    assert reminder(h, rem["id"])["state"] == "canceled"


def test_canceled_event_suppresses_its_reminder(h):
    event = h.client.post(
        "/v1/events",
        json={"title": "Bloomberg event", "local_date": "2026-10-08",
              "starts_at": "2026-10-08T22:00:00Z", "timezone": "America/New_York"},
        headers={"Idempotency-Key": "ev-1"},
    )
    assert event.status_code == 201, event.status_code
    event = event.json()
    rem = h.client.post(
        "/v1/reminders",
        json={"target_id": event["id"], "target_kind": "event",
              "expected_target_revision": event["revision"], "local_date": "2026-10-07",
              "local_time": "09:00", "timezone": "America/New_York"},
        headers={"Idempotency-Key": "rem-ev"},
    ).json()
    h.tick()
    occurrence = reminder(h, rem["id"])["occurrence_key"]
    patch(h, f"/v1/events/{event['id']}", event, attendance_status="canceled")
    h.clock.set(DUE)
    fire_stale(h, rem["id"], occurrence)
    h.tick()
    assert notifications(h) == []
    assert reminder(h, rem["id"])["state"] == "canceled"


def test_ordinary_reminder_makes_zero_model_calls(h):
    before = len(h.adapters.inference.calls)
    _scheduled_reminder(h, deadline_local_date="2026-10-12")
    h.clock.set(DUE)
    h.tick()
    assert len(notifications(h)) == 1
    assert len(h.adapters.inference.calls) == before == 0
    usage = h.client.get("/v1/usage").json()
    assert usage["model_calls"] == 0 and usage["measured_model_micro_usd"] == 0
    assert notifications(h)[0]["generated"] is False, "templated text, not model output"


def test_unsupported_push_falls_back_to_the_visible_feed_and_never_claims_acceptance(h):
    from e2e_support import NY, key
    from metis_scheduler.transports import WebPushTransport

    h.adapters.notification_transports["web_push"] = WebPushTransport(h.state, h.clock)  # no key, no subscription
    task = make_task(h, "Register for the event")
    rem = h.client.post(
        "/v1/reminders",
        json={"target_id": task["id"], "target_kind": "task", "expected_target_revision": task["revision"],
              "local_date": "2026-10-07", "local_time": "09:00", "timezone": NY, "channel": "web_push"},
        headers={"Idempotency-Key": key("push")},
    ).json()
    h.tick()
    h.clock.set(DUE)
    h.tick()
    (note,) = notifications(h)
    assert note["reminder_id"] == rem["id"], "the in-app feed still carries the reminder"
    assert note["delivery_status"] == "unsupported" and note["accepted_at"] is None
    assert note["delivery_detail"], "the reason is visible rather than a silent success"


def test_fixture_delivery_evidence_is_labelled_as_not_a_real_delivery(h):
    _scheduled_reminder(h)
    h.clock.set(DUE)
    h.tick()
    (note,) = notifications(h)
    assert "fixture" in (note["delivery_detail"] or "").lower()
