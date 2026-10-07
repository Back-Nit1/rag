"""Offline retry, duplicate delivery and transcript correction supersession."""

from __future__ import annotations

import pytest
from e2e_support import BLOOMBERG, DUE, NY, USER, accept_all, capture, detail, key, notifications, reminder


def _counts(h):
    return (
        len(h.client.get("/v1/tasks").json()["items"]),
        len(h.client.get("/v1/events").json()["items"]),
        len(h.client.get("/v1/captures").json()["items"]),
    )


def test_offline_retry_with_stable_client_id_yields_one_source_and_one_commitment_set(h):
    ids = {
        capture(h, client_capture_id="offline-1", idem=key("retry")).json()["capture_id"] for _ in range(3)
    }
    assert len(ids) == 1
    cap = ids.pop()
    h.tick()
    h.tick()  # a second worker pass redelivers nothing new
    assert len(detail(h, cap).json()["proposals"]) == 1, "duplicate deliveries create one proposal"

    proposal = detail(h, cap).json()["proposals"][0]
    decision = {
        "expected_revision": proposal["revision"],
        "decisions": [{"item_index": i, "action": "accept"} for i in range(len(proposal["items"]))],
    }
    replay_key = key("resolve")
    # The offline outbox replays the same user decision with the same idempotency key.
    post = lambda: h.client.post(  # noqa: E731
        f"/v1/proposals/{proposal['id']}/resolve", json=decision, headers={"Idempotency-Key": replay_key}
    )
    assert post().status_code == 200
    tasks, events, captures = _counts(h)
    assert (captures, events) == (1, 1) and tasks >= 1
    assert [post().status_code, post().status_code] == [200, 200], "replay returns the first response"
    h.tick()
    assert _counts(h) == (tasks, events, captures), "worker passes add nothing"


@pytest.mark.xfail(
    strict=True,
    reason="DEFECT D2: resolving an already-accepted proposal with a fresh Idempotency-Key and the "
    "current revision creates a second set of commitments (services/api/src/metis_api/proposals.py)",
)
def test_resolving_an_already_resolved_proposal_is_refused(h):
    cap = capture(h).json()["capture_id"]
    h.tick()
    assert accept_all(h, cap).status_code == 200
    before = _counts(h)
    assert accept_all(h, cap).status_code == 409
    assert _counts(h) == before


def test_transcript_correction_supersedes_proposal_index_and_keeps_later_manual_edits(h):
    # A user-edited task from another capture must survive everything below.
    task_cap = capture(h, BLOOMBERG, idem=key("c2")).json()["capture_id"]
    h.tick()
    task_id = accept_all(h, task_cap).json()["created_task_ids"][0]
    task = h.client.get(f"/v1/tasks/{task_id}").json()
    h.client.patch(
        f"/v1/tasks/{task_id}", json={"expected_revision": task["revision"], "title": "My wording"}
    )

    cap = capture(h, "I tried the stale cache approach and it failed.").json()["capture_id"]
    h.tick()
    pending = detail(h, cap).json()["proposals"][0]
    accept_all(h, cap)  # creates a memory + index request; the worker has NOT run yet
    (memory,) = [
        m
        for m in h.state.list_memories(USER, limit=50).items
        if any(r.source_id == cap for r in m.source_refs)
    ]
    assert memory.indexed_at is None

    source = detail(h, cap).json()["source"]
    resp = h.client.patch(
        f"/v1/captures/{cap}/transcript",
        json={
            "expected_revision": source["revision"],
            "text": "I tried the warm cache approach and it failed.",
        },
    )
    assert resp.status_code == 200
    h.tick()
    final = detail(h, cap).json()
    assert final["source"]["current_version"] == 2
    assert any(p["source_version"] == 2 for p in final["proposals"]), "new extraction for the new version"
    assert pending["source_version"] == 1
    after = h.state.get(USER, "memory", memory.id)
    assert after.indexed_at is None, "the superseded index request must not embed outdated text"
    assert h.client.get(f"/v1/tasks/{task_id}").json()["title"] == "My wording", "manual edit preserved"


def test_correcting_a_date_after_scheduling_holds_the_old_reminder(h):
    cap = capture(h, "Register for the Bloomberg event on the twelfth, remind me at nine.").json()[
        "capture_id"
    ]
    h.tick()
    proposal = detail(h, cap).json()["proposals"][0]
    task_idx = next(i for i, it in enumerate(proposal["items"]) if it["kind"] == "event_intent")
    item = {
        **proposal["items"][task_idx],
        "local_date": "2026-10-12",
        "timezone": NY,
        "deadline_local_date": "2026-10-12",
        "remind_local_time": "09:00",
        "missing_fields": [],
    }
    body = accept_all(h, cap, edits={task_idx: item}).json()
    (rid,) = body["created_reminder_ids"][:1]
    h.tick()
    assert reminder(h, rid)["state"] == "scheduled"

    source = detail(h, cap).json()["source"]
    h.client.patch(
        f"/v1/captures/{cap}/transcript",
        json={
            "expected_revision": source["revision"],
            "text": "Register for the Bloomberg event on the twentieth, remind me at nine.",
        },
    )
    held = reminder(h, rid)
    assert held["state"] != "delivered"
    h.clock.set(DUE)
    h.tick()
    assert [n for n in notifications(h) if n["reminder_id"] == rid] == [], (
        "a reminder whose date was disputed must not fire on the old date"
    )


@pytest.mark.xfail(
    strict=True,
    reason="DEFECT D3: one 'remind me to register' utterance yields two registration tasks "
    "(a standalone task item plus the event's own registration task); "
    "pipeline fixture extraction + api proposals.py",
)
def test_one_utterance_creates_one_registration_task(h):
    cap = capture(h).json()["capture_id"]
    h.tick()
    accept_all(h, cap)
    titles = [t["title"].lower() for t in h.client.get("/v1/tasks").json()["items"]]
    assert len([t for t in titles if t.startswith("register")]) == 1
