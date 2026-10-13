"""Reminder reschedule, project PATCH, capture->project, explicit status target."""

from __future__ import annotations

import pytest
from metis_api import deps
from metis_api.auth import Principal

NY = "America/New_York"
T0 = "2026-10-05T14:00:00+00:00"


@pytest.fixture()
def other(client, monkeypatch):
    """Requests as a second authenticated subject: pass headers=other."""
    real = deps.principal_from_header

    def principal(authorization):
        if authorization and authorization.startswith("Bearer "):
            return Principal(user_id=authorization.split(" ", 1)[1], source="cognito")
        return real(authorization)

    monkeypatch.setattr(deps, "principal_from_header", principal)
    return {"Authorization": "Bearer someone_else"}


def _capture(client, text, idem):
    r = client.post(
        "/v1/captures",
        json={"kind": "text", "text": text, "capture_timezone": NY, "captured_at": T0},
        headers={"Idempotency-Key": idem},
    )
    assert r.status_code == 201
    return r.json()["capture_id"]


def _reminder(client, worker):
    task = client.post("/v1/tasks", json={"title": "Register"}, headers={"Idempotency-Key": "t"}).json()
    rem = client.post(
        "/v1/reminders",
        json={
            "target_id": task["id"],
            "target_kind": "task",
            "expected_target_revision": task["revision"],
            "local_date": "2026-10-07",
            "local_time": "09:00",
            "timezone": NY,
        },
        headers={"Idempotency-Key": "r"},
    ).json()
    worker()
    return client.get(f"/v1/reminders/{rem['id']}").json()


# --- reschedule ------------------------------------------------------------- #


def test_reschedule_is_pending_until_the_worker_confirms(client, worker):
    rem = _reminder(client, worker)
    assert rem["state"] == "scheduled"
    r = client.post(
        f"/v1/reminders/{rem['id']}/reschedule",
        json={"expected_revision": rem["revision"], "local_date": "2026-10-08", "local_time": "10:30"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "pending_schedule"
    assert body["trigger_at"].startswith("2026-10-08T14:30:00")  # 10:30 New York (EDT)
    assert client.get(f"/v1/reminders/{rem['id']}").json()["state"] == "pending_schedule"
    worker()
    after = client.get(f"/v1/reminders/{rem['id']}").json()
    assert after["state"] == "scheduled" and after["trigger_at"] == body["trigger_at"]


def test_reschedule_stale_revision_is_409(client, worker):
    rem = _reminder(client, worker)
    r = client.post(
        f"/v1/reminders/{rem['id']}/reschedule",
        json={"expected_revision": rem["revision"] + 5, "trigger_at": "2026-10-09T12:00:00Z"},
    )
    assert r.status_code == 409


def test_reschedule_into_the_past_is_refused_and_changes_nothing(client, worker):
    rem = _reminder(client, worker)
    r = client.post(
        f"/v1/reminders/{rem['id']}/reschedule",
        json={"expected_revision": rem["revision"], "trigger_at": "2026-10-01T12:00:00Z"},
    )
    assert r.status_code == 422
    assert client.get(f"/v1/reminders/{rem['id']}").json()["revision"] == rem["revision"]


def test_reschedule_of_a_canceled_reminder_is_an_invalid_transition(client, worker):
    rem = _reminder(client, worker)
    client.delete(f"/v1/reminders/{rem['id']}")
    current = client.get(f"/v1/reminders/{rem['id']}").json()
    r = client.post(
        f"/v1/reminders/{rem['id']}/reschedule",
        json={"expected_revision": current["revision"], "trigger_at": "2026-10-09T12:00:00Z"},
    )
    assert r.status_code == 422


def test_reschedule_cross_owner_is_not_found(client, worker, other):
    rem = _reminder(client, worker)
    r = client.post(
        f"/v1/reminders/{rem['id']}/reschedule",
        json={"expected_revision": rem["revision"], "trigger_at": "2026-10-09T12:00:00Z"},
        headers=other,
    )
    assert r.status_code == 404


def test_reschedule_replay_returns_first_response_and_one_sync(client, worker, adapters):
    rem = _reminder(client, worker)
    body = {"expected_revision": rem["revision"], "trigger_at": "2026-10-09T12:00:00Z"}
    h = {"Idempotency-Key": "resched-1"}
    first = client.post(f"/v1/reminders/{rem['id']}/reschedule", json=body, headers=h)
    second = client.post(f"/v1/reminders/{rem['id']}/reschedule", json=body, headers=h)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert client.get(f"/v1/reminders/{rem['id']}").json()["revision"] == first.json()["revision"]


# --- project patch ---------------------------------------------------------- #


def _project(client, pid="proj_cerno"):
    r = client.post(
        "/v1/projects",
        json={
            "id": pid,
            "user_id": "x",
            "created_at": T0,
            "updated_at": T0,
            "title": "Cerno",
            "aliases": ["cerno-app"],
            "objective": "Ship it",
        },
    )
    assert r.status_code == 201
    return r.json()


def test_project_patch_omitted_unchanged_null_clears(client):
    p = _project(client)
    r = client.patch(
        f"/v1/projects/{p['id']}",
        json={"expected_revision": p["revision"], "title": "Cerno 2", "objective": None},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Cerno 2" and body["objective"] is None
    assert body["aliases"] == ["cerno-app"] and body["revision"] == p["revision"] + 1


def test_project_patch_archive_and_aliases_deduped(client):
    p = _project(client)
    r = client.patch(
        f"/v1/projects/{p['id']}",
        json={"expected_revision": p["revision"], "aliases": ["a", "a", "b"], "archived": True},
    )
    assert r.json()["aliases"] == ["a", "b"] and r.json()["archived"] is True


def test_project_patch_cannot_null_title_and_stale_is_409(client):
    p = _project(client)
    assert (
        client.patch(
            f"/v1/projects/{p['id']}", json={"expected_revision": p["revision"], "title": None}
        ).status_code
        == 422
    )
    assert (
        client.patch(f"/v1/projects/{p['id']}", json={"expected_revision": 99, "title": "x"}).status_code
        == 409
    )


def test_project_patch_cross_owner_and_replay(client, other):
    p = _project(client)
    body = {"expected_revision": p["revision"], "title": "Renamed"}
    assert client.patch(f"/v1/projects/{p['id']}", json=body, headers=other).status_code == 404
    h = {"Idempotency-Key": "pp-1"}
    a = client.patch(f"/v1/projects/{p['id']}", json=body, headers=h)
    b = client.patch(f"/v1/projects/{p['id']}", json=body, headers=h)
    assert a.status_code == b.status_code == 200 and a.json() == b.json()


# --- attach capture to project ---------------------------------------------- #


def test_attach_capture_files_unprojected_derivatives_and_keeps_existing(client, worker):
    proj = _project(client)
    other_proj = _project(client, "proj_other")
    cap = _capture(client, "Remind me to register for the Bloomberg event in New York.", "c1")
    worker()
    detail = client.get(f"/v1/captures/{cap}").json()
    proposal = detail["proposals"][0]
    client.post(
        f"/v1/proposals/{proposal['id']}/resolve",
        json={
            "expected_revision": proposal["revision"],
            "decisions": [{"item_index": i, "action": "accept"} for i in range(len(proposal["items"]))],
        },
    )
    detail = client.get(f"/v1/captures/{cap}").json()
    assert detail["created_task_ids"]
    pinned = client.get(f"/v1/tasks/{detail['created_task_ids'][0]}").json()
    client.patch(
        f"/v1/tasks/{pinned['id']}",
        json={"expected_revision": pinned["revision"], "project_id": "proj_other"},
    )

    r = client.post(
        f"/v1/captures/{cap}/project",
        json={"expected_revision": detail["source"]["revision"], "project_id": proj["id"]},
    )
    assert r.status_code == 200 and r.json()["project_id"] == proj["id"]
    for tid in detail["created_task_ids"]:
        t = client.get(f"/v1/tasks/{tid}").json()
        assert t["project_id"] == (other_proj["id"] if tid == pinned["id"] else proj["id"])


def test_attach_capture_conflict_missing_project_and_cross_owner(client, other):
    proj = _project(client)
    cap = _capture(client, "A note.", "c2")
    rev = client.get(f"/v1/captures/{cap}").json()["source"]["revision"]
    path = f"/v1/captures/{cap}/project"
    assert client.post(path, json={"expected_revision": rev + 3, "project_id": proj["id"]}).status_code == 409
    assert client.post(path, json={"expected_revision": rev, "project_id": "proj_nope"}).status_code == 404
    assert (
        client.post(
            path, json={"expected_revision": rev, "project_id": proj["id"]}, headers=other
        ).status_code
        == 404
    )
    h = {"Idempotency-Key": "att-1"}
    body = {"expected_revision": rev, "project_id": proj["id"]}
    a = client.post(path, json=body, headers=h)
    b = client.post(path, json=body, headers=h)
    assert a.status_code == b.status_code == 200 and a.json() == b.json()


# --- explicit status_update target ------------------------------------------ #


def _ambiguous(client, worker):
    ids = []
    for i in range(2):
        e = client.post(
            "/v1/events",
            json={"title": f"Bloomberg briefing {i}", "local_date": "2026-10-08", "timezone": NY},
            headers={"Idempotency-Key": f"ev{i}"},
        ).json()
        ids.append(e["id"])
    cap = _capture(client, "Cancel that Bloomberg briefing.", "cancel")
    worker()
    proposal = client.get(f"/v1/captures/{cap}").json()["proposals"][0]
    r = client.post(
        f"/v1/proposals/{proposal['id']}/resolve",
        json={
            "expected_revision": proposal["revision"],
            "decisions": [{"item_index": i, "action": "accept"} for i in range(len(proposal["items"]))],
        },
    )
    assert r.status_code == 200
    resolved = r.json()["proposal"]
    assert resolved["target_choices"] and resolved["target_choices"][0]["chosen_id"] is None
    return ids, resolved


def _states(client, ids):
    return {i: client.get(f"/v1/events/{i}").json()["attendance_status"] for i in ids}


def test_choosing_a_candidate_applies_exactly_that_one(client, worker):
    ids, proposal = _ambiguous(client, worker)
    idx = proposal["target_choices"][0]["item_index"]
    assert sorted(proposal["target_choices"][0]["candidate_ids"]) == sorted(ids)
    r = client.post(
        f"/v1/proposals/{proposal['id']}/target",
        json={"expected_revision": proposal["revision"], "item_index": idx, "target_id": ids[1]},
    )
    assert r.status_code == 200
    assert r.json()["proposal"]["target_choices"][0]["chosen_id"] == ids[1]
    states = _states(client, ids)
    assert states[ids[1]] == "canceled" and states[ids[0]] != "canceled"


def test_target_must_be_a_recorded_candidate_and_stale_is_409(client, worker):
    ids, proposal = _ambiguous(client, worker)
    idx = proposal["target_choices"][0]["item_index"]
    path = f"/v1/proposals/{proposal['id']}/target"
    stranger = client.post(
        "/v1/events",
        json={"title": "Unrelated", "local_date": "2026-10-09", "timezone": NY},
        headers={"Idempotency-Key": "evx"},
    ).json()
    bad = client.post(
        path, json={"expected_revision": proposal["revision"], "item_index": idx, "target_id": stranger["id"]}
    )
    assert bad.status_code == 422
    stale = client.post(
        path, json={"expected_revision": proposal["revision"] + 4, "item_index": idx, "target_id": ids[0]}
    )
    assert stale.status_code == 409
    assert "canceled" not in _states(client, [*ids, stranger["id"]]).values()


def test_second_answer_conflicts_and_replay_is_idempotent(client, worker):
    ids, proposal = _ambiguous(client, worker)
    idx = proposal["target_choices"][0]["item_index"]
    path = f"/v1/proposals/{proposal['id']}/target"
    body = {"expected_revision": proposal["revision"], "item_index": idx, "target_id": ids[0]}
    h = {"Idempotency-Key": "choose-1"}
    a = client.post(path, json=body, headers=h)
    b = client.post(path, json=body, headers=h)
    assert a.status_code == b.status_code == 200 and a.json() == b.json()
    fresh = client.post(
        path, json={**body, "expected_revision": a.json()["proposal"]["revision"], "target_id": ids[1]}
    )
    assert fresh.status_code == 409
    assert _states(client, ids)[ids[1]] != "canceled"


def test_choose_target_cross_owner_is_not_found(client, worker, other):
    ids, proposal = _ambiguous(client, worker)
    r = client.post(
        f"/v1/proposals/{proposal['id']}/target",
        json={"expected_revision": proposal["revision"], "item_index": 0, "target_id": ids[0]},
        headers=other,
    )
    assert r.status_code == 404
