"""Provider outage and budget exhaustion keep manual and deterministic work usable."""

from __future__ import annotations

from e2e_support import DUE, add_reminder, capture, detail, make_task, notifications, patch, reminder
from metis_contracts import ErrorCode, MetisError, reset_settings_cache


def _schedule_reminder(h):
    task = make_task(h, "Register for the event")
    rem = add_reminder(h, task)
    h.tick()
    assert reminder(h, rem["id"])["state"] == "scheduled"
    return task, rem


def _assert_manual_and_deterministic_work_survive(h, task, rem):
    # Manual edit still works.
    edited = patch(h, f"/v1/tasks/{task['id']}", task, priority=3)
    assert edited["priority"] == 3
    # A new manual task still works.
    assert make_task(h, "Manual note while offline")["id"]
    # The already-scheduled deterministic reminder still fires, with zero model calls.
    h.clock.set(DUE)
    h.tick()
    assert [n["reminder_id"] for n in notifications(h)] == [rem["id"]]


def test_provider_outage_preserves_capture_manual_edits_and_scheduled_reminders(h, monkeypatch):
    task, rem = _schedule_reminder(h)

    def down(**_):
        raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "provider down")

    monkeypatch.setattr(h.adapters.inference, "complete_json", down)
    created = capture(h)
    assert created.status_code == 201, "raw capture is saved without a model"
    cap = created.json()["capture_id"]
    results = h.drain()
    assert any(r.retried for r in results), "the extraction request is scheduled for retry"
    assert sum(r.dead_lettered for r in results) == 0, "an outage retries; it does not dead-letter work"
    after = detail(h, cap).json()
    assert after["transcript_text"] and after["proposals"] == [], "capture readable, no fake proposal"

    _assert_manual_and_deterministic_work_survive(h, task, rem)


def test_extraction_resumes_after_the_provider_recovers(h, monkeypatch):
    real = h.adapters.inference.complete_json
    state = {"down": True}

    def flaky(**kw):
        if state["down"]:
            raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "provider down")
        return real(**kw)

    monkeypatch.setattr(h.adapters.inference, "complete_json", flaky)
    cap = capture(h).json()["capture_id"]
    h.drain()
    assert detail(h, cap).json()["proposals"] == []
    state["down"] = False
    h.advance(minutes=30)  # past the retry back-off
    h.drain()
    proposals = detail(h, cap).json()["proposals"]
    assert len(proposals) == 1 and proposals[0]["adapter_mode"] == "fixture"


def test_budget_exhaustion_blocks_optional_inference_but_not_essential_work(h, monkeypatch):
    task, rem = _schedule_reminder(h)
    monkeypatch.setenv("METIS_DAILY_MICRO_USD", "0")
    reset_settings_cache()
    cap = capture(h).json()["capture_id"]
    h.drain()
    assert h.adapters.inference.calls == [], "no model call may be made once the cap is reached"
    assert detail(h, cap).json()["proposals"] == []
    assert h.client.get("/v1/usage").json()["model_calls"] == 0
    _assert_manual_and_deterministic_work_survive(h, task, rem)
