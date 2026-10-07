"""Capture -> transcript -> extraction -> resolution -> records, with honest states."""

from __future__ import annotations

from e2e_support import BLOOMBERG, NY, accept_all, capture, detail, key


def _intent(h, capture_id):
    proposal = detail(h, capture_id).json()["proposals"][0]
    return proposal, next(i for i in proposal["items"] if i["kind"] == "event_intent")


def test_text_capture_to_records_and_every_fact_has_provenance(h):
    cap = capture(h).json()["capture_id"]
    assert detail(h, cap).json()["proposals"] == [], "saved before any extraction ran"
    h.tick()
    proposal, _ = _intent(h, cap)
    assert proposal["adapter_mode"] == "fixture", "fixture output must be labelled as fixture"
    assert all(i["evidence"] for i in proposal["items"])

    body = accept_all(h, cap).json()
    assert body["created_event_ids"] and body["created_task_ids"]
    for task_id in body["created_task_ids"]:
        task = h.client.get(f"/v1/tasks/{task_id}").json()
        ref = task["source_refs"][0]
        assert ref["source_id"] == cap and ref["source_version"] == 1 and ref["span_ref"]


def test_audio_fixture_capture_reaches_structured_state_only_after_transcription(h):
    """Uploaded is not transcribed: the transcript appears only after the worker ran."""
    resp = h.client.post(
        "/v1/captures",
        json={
            "kind": "audio",
            "capture_timezone": NY,
            "captured_at": "2026-10-05T14:00:00+00:00",
            "content_type": "audio/webm",
            "duration_seconds": 5,
            "expected_bytes": 18,
        },
        headers={"Idempotency-Key": key("aud")},
    )
    assert resp.status_code == 201, (resp.status_code, resp.json().get("code"), resp.json().get("message"))
    body = resp.json()
    cap = body["capture_id"]
    assert body["upload"] is not None
    assert detail(h, cap).json()["transcript_text"] in (None, ""), "uploaded must not look transcribed"
    assert (
        h.client.put(
            f"/v1/local-uploads/{cap}", content=b"RIFF-fixture-bytes", headers={"Content-Type": "audio/webm"}
        ).status_code
        == 204
    )
    done = h.client.post(
        f"/v1/captures/{cap}/complete",
        json={"actual_bytes": 18, "expected_revision": detail(h, cap).json()["source"]["revision"]},
        headers={"Idempotency-Key": key("done")},
    )
    assert done.status_code in (200, 202), (done.status_code, done.json().get("code"))
    h.tick()
    after = detail(h, cap).json()
    assert after["transcript_text"], "the fixture transcriber should have finalized a transcript"
    assert after["source"]["transcripts"][0].get("adapter_mode", "fixture") == "fixture"
    assert after["proposals"], "extraction ran on the finalized transcript"


def test_tentative_event_has_no_invented_date(h):
    cap = capture(h, "Maybe I should go to that Themis workshop at some point.").json()["capture_id"]
    h.tick()
    body = accept_all(h, cap).json()
    assert body["created_reminder_ids"] == []
    for event_id in body["created_event_ids"]:
        event = h.client.get(f"/v1/events/{event_id}").json()
        assert event["attendance_status"] == "tentative"
        assert event["starts_at"] is None and event["local_date"] is None
        assert event["registration_status"] == "unknown"
    assert h.client.get("/v1/reminders").json()["items"] == []


def test_one_clarification_answer_resolves_date_and_reminder_time(h):
    cap = capture(h).json()["capture_id"]
    h.tick()
    _, intent = _intent(h, cap)
    assert set(intent["missing_fields"]) >= {"event_date_time", "registration_reminder_time"}
    first = accept_all(h, cap).json()
    event_threads = [c for c in first["clarifications"] if c["target_id"] == first["created_event_ids"][0]]
    assert len(event_threads) == 1, "event fields are batched into one question"
    assert set(event_threads[0]["missing_fields"]) >= {"event_date_time", "registration_reminder_time"}
    assert first["created_reminder_ids"] == [], "no reminder before the user answers"

    # A single answer (date + time) is applied as one edit of the same proposal item.
    answered = {
        **intent,
        "starts_at": "2026-10-20T14:00:00Z",
        "local_date": "2026-10-20",
        "timezone": NY,
        "deadline_local_date": "2026-10-12",
        "remind_local_time": "09:00",
        "missing_fields": [],
    }
    second_cap = capture(h, BLOOMBERG + " ", idem=key("c2")).json()["capture_id"]
    h.tick()
    p2, i2 = _intent(h, second_cap)
    idx = p2["items"].index(i2)
    body = accept_all(h, second_cap, edits={idx: {**answered, "evidence": i2["evidence"]}}).json()
    assert body["created_reminder_ids"], "date + time answer yields a scheduled-intent reminder"
    event = h.client.get(f"/v1/events/{body['created_event_ids'][0]}").json()
    assert event["local_date"] == "2026-10-20"
    reminder = h.client.get(f"/v1/reminders/{body['created_reminder_ids'][0]}").json()
    assert reminder["state"] == "pending_schedule", "not 'scheduled' until sync is confirmed"
    assert not [c for c in body["clarifications"] if c["blocking"]]
    h.tick()
    assert h.client.get(f"/v1/reminders/{reminder['id']}").json()["state"] == "scheduled"


def test_one_extracted_item_opens_one_clarification_thread(h):
    cap = capture(h).json()["capture_id"]
    h.tick()
    body = accept_all(h, cap).json()
    assert len(body["clarifications"]) == 1
