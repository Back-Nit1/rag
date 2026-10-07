"""Semantic validation, registration/cancel semantics and project resumption."""

from __future__ import annotations

import pytest
from e2e_support import (
    DUE,
    NY,
    USER,
    accept_all,
    add_reminder,
    capture,
    detail,
    key,
    make_task,
    notifications,
    patch,
    reminder,
)

TEXT = "Submit the application for the grant."


def _model_says(h, monkeypatch, item: dict):
    """Make the (fixture) model return a structurally valid payload with `item`."""

    def complete_json(**kw):
        payload = {
            "items": [{"kind": "task", "title": "Submit the application", "missing_fields": [],
                       "evidence": [{"span_ref": "s1", "start_char": 0, "end_char": len(TEXT)}], **item}],
            "requires_clarification": False,
        }
        usage = {"model_id": "fixture-inference", "adapter_mode": "fixture", "micro_usd": 0,
                 "input_tokens": 10, "output_tokens": 10}
        return payload, usage

    monkeypatch.setattr(h.adapters.inference, "complete_json", complete_json)
    cap = capture(h, TEXT).json()["capture_id"]
    h.tick()
    proposals = detail(h, cap).json()["proposals"]
    assert proposals, "a proposal (or an explicit unresolved record) exists"
    return proposals[0]


def test_structurally_valid_but_absurd_deadline_fails_semantic_validation(h, monkeypatch):
    proposal = _model_says(h, monkeypatch, {"deadline_local_date": "2099-01-01"})
    assert all(i["deadline_local_date"] is None for i in proposal["items"])


def test_unsupported_registration_and_commitment_claims_are_downgraded(h, monkeypatch):
    proposal = _model_says(
        h, monkeypatch,
        {"kind": "event_intent", "title": "Grant briefing", "registration_status": "registered",
         "attendance_status": "committed"},
    )
    item = proposal["items"][0]
    assert item["registration_status"] == "unknown"
    assert item["attendance_status"] == "tentative"


@pytest.mark.xfail(
    strict=True,
    reason="DEFECT D6: semantic validation only rejects deadlines beyond a horizon; a plausible "
    "deadline the note never states (2026-10-20) is accepted as fact "
    "(services/pipeline/src/metis_pipeline/extraction.py:365-373, owner: pipeline)",
)
def test_a_plausible_deadline_the_note_never_states_is_rejected(h, monkeypatch):
    proposal = _model_says(h, monkeypatch, {"deadline_local_date": "2026-10-20"})
    assert all(i["deadline_local_date"] is None for i in proposal["items"])


# --- registration ------------------------------------------------------- #


def _event_with_reminder(h):
    event = h.client.post(
        "/v1/events",
        json={"title": "Bloomberg event", "local_date": "2026-10-08", "starts_at": "2026-10-08T22:00:00Z",
              "timezone": NY},
        headers={"Idempotency-Key": key("ev")},
    ).json()
    rem = h.client.post(
        "/v1/reminders",
        json={"target_id": event["id"], "target_kind": "event", "expected_target_revision": event["revision"],
              "local_date": "2026-10-07", "local_time": "09:00", "timezone": NY},
        headers={"Idempotency-Key": key("rem")},
    ).json()
    h.tick()
    return event, rem


@pytest.mark.xfail(
    strict=True,
    reason="DEFECT D10 (medium): evaluate_due suppresses on registration_status=registered only when the "
    "literal text 'regist' is in the reminder's target_id (services/scheduler/src/metis_scheduler/"
    "service.py:357), so real ids (event_<hex>) never suppress and a registered event still nags "
    "(owner: scheduler)",
)
def test_marking_registered_removes_the_now_useless_registration_reminder(h):
    event, rem = _event_with_reminder(h)
    patch(h, f"/v1/events/{event['id']}", event, registration_status="registered")
    h.clock.set(DUE)
    h.tick()
    assert [n for n in notifications(h) if n["reminder_id"] == rem["id"]] == [], "no stale nag"


@pytest.mark.xfail(
    strict=True,
    reason="DEFECT D7: the spoken follow-up 'I already registered' is extracted for a NEW event intent; "
    "nothing links it to the existing event/registration task, so the old follow-up stays open "
    "(no target resolution in services/api/src/metis_api/proposals.py, owner: api/pipeline)",
)
def test_spoken_already_registered_closes_the_existing_registration_follow_up(h):
    cap = capture(h).json()["capture_id"]
    h.tick()
    created = accept_all(h, cap).json()
    reg_tasks = [t for t in created["created_task_ids"]
                 if h.client.get(f"/v1/tasks/{t}").json()["title"].lower().startswith("register")]
    follow = capture(h, "I already registered for the Bloomberg event in New York.", idem=key("f")).json()
    h.tick()
    accept_all(h, follow["capture_id"])
    states = [h.client.get(f"/v1/tasks/{t}").json()["state"] for t in reg_tasks]
    assert states and all(s in ("done", "canceled") for s in states)


@pytest.mark.xfail(
    strict=True,
    reason="DEFECT D8: 'Cancel that' has no extraction kind or target resolution; it is stored as a "
    "plain thought, never resolving its target nor asking when ambiguous "
    "(ExtractedItemKind has no cancel; proposals.py, owner: contracts/api)",
)
def test_cancel_that_resolves_its_target_or_asks_and_never_cancels_similar_events(h):
    for i in range(2):
        h.client.post(
            "/v1/events",
            json={"title": f"Bloomberg briefing {i}", "local_date": "2026-10-08",
                  "starts_at": "2026-10-08T22:00:00Z", "timezone": NY},
            headers={"Idempotency-Key": key("ev")},
        )
    cap = capture(h, "Cancel that Bloomberg briefing.", idem=key("c")).json()["capture_id"]
    h.tick()
    body = accept_all(h, cap).json()
    events = h.client.get("/v1/events").json()["items"]
    canceled = [e for e in events if e["attendance_status"] == "canceled"]
    assert len(canceled) <= 1, "never cancels every similar event"
    assert canceled or body["clarifications"], "either resolved a target or asked which one"


# --- project resumption --------------------------------------------------- #


def _project(h):
    r = h.client.post(
        "/v1/projects",
        json={"id": "proj_cerno", "user_id": "x", "created_at": "2026-10-05T14:00:00Z",
              "updated_at": "2026-10-05T14:00:00Z", "title": "Cerno", "objective": "Ship the Cerno build"},
    )
    assert r.status_code == 201, (r.status_code, r.json().get("details"))
    return r.json()


def test_resumption_keeps_failed_attempts_as_historical_evidence(h):
    _project(h)
    cap = capture(h, "On Cerno I tried the stale cache approach and it failed.").json()["capture_id"]
    h.tick()
    accept_all(h, cap)
    h.tick()
    found = h.client.post("/v1/search", json={"query": "stale cache approach", "include_historical": True}).json()
    (hit,) = found["results"]
    assert hit["evidence"]["validity"] == "historical", "a failed attempt is history, not a suggestion"
    assert hit["evidence"]["source_id"] == cap and hit["evidence"]["span_ref"]


@pytest.mark.xfail(
    strict=True,
    reason="DEFECT D9: nothing attaches an accepted capture's work_log/attempt memory to a project "
    "(Memory.project_id is never set; ExtractedItem.project_hint is dropped), so "
    "POST /v1/projects/{id}/resume cannot surface failed attempts from captured notes "
    "(services/api/src/metis_api/proposals.py:_record_memory, owner: api)",
)
def test_resume_surfaces_failed_attempts_from_captured_notes(h):
    project = _project(h)
    cap = capture(h, "On Cerno I tried the stale cache approach and it failed.").json()["capture_id"]
    h.tick()
    accept_all(h, cap)
    h.tick()
    packet = h.client.post(f"/v1/projects/{project['id']}/resume", json={"minutes": 30}).json()
    assert any("stale cache" in a["summary"] for a in packet["attempts"])


def test_resume_retains_failed_attempts_and_labels_unverified_next_steps(h):
    """Seed the attempt directly against the project (fixture seeding) to pin the resume contract."""
    from metis_contracts import Memory, MemoryKind
    from metis_retrieval import content_hash

    project = _project(h)
    cap = capture(h, "On Cerno I tried the stale cache approach and it failed.").json()["capture_id"]
    now = h.clock.now()
    text = "Tried the stale cache approach; it failed."
    from metis_contracts import SourceRef

    with h.state.transaction(USER) as tx:
        tx.put("memory", Memory(
            id="mem_attempt1", user_id=USER, created_at=now, updated_at=now, kind=MemoryKind.ATTEMPT,
            content=text, effective_at=now, project_id=project["id"], content_hash=content_hash(text),
            source_refs=[SourceRef(source_id=cap, source_version=1, span_ref="span_full_v1")],
        ))
    packet = h.client.post(f"/v1/projects/{project['id']}/resume", json={"minutes": 30}).json()
    assert [a["summary"] for a in packet["attempts"]] == [text]
    assert packet["evidence"][0]["source_id"] == cap
    assert "not verified facts" in packet["provenance"]
    assert packet["blocker"] is None, "no blocker was recorded, so none is invented"
