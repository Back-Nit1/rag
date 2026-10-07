"""Cross-user isolation (sources, jobs, retrieval) and owner-scoped, retryable export."""

from __future__ import annotations

import io
import zipfile

from e2e_support import OTHER, USER, accept_all, capture, detail, key, make_task, patch
from metis_contracts import ErrorCode, EventType, MetisError, OutboxEvent, new_id

NOTE = "I tried the quokka cache approach on the Cerno build and it failed."
MARK = "quokka"


def _b(h):
    return h.as_user(OTHER)


def _a_data(h):
    cap = capture(h, NOTE).json()["capture_id"]
    h.tick()
    accept_all(h, cap)
    h.tick()
    return cap


def test_user_b_cannot_read_change_resolve_or_delete_user_a_captures(h):
    cap = _a_data(h)
    proposal = detail(h, cap).json()["proposals"][0]
    hb = _b(h)
    assert detail(h, cap, headers=hb).status_code == 404
    assert h.client.patch(
        f"/v1/captures/{cap}/transcript", json={"expected_revision": 1, "text": "tampered"}, headers=hb
    ).status_code == 404
    assert h.client.post(
        f"/v1/proposals/{proposal['id']}/resolve",
        json={"expected_revision": proposal["revision"], "decisions": []},
        headers={**hb, "Idempotency-Key": key("b")},
    ).status_code == 404
    assert h.client.delete(f"/v1/captures/{cap}", headers=hb).status_code == 404
    assert h.client.get("/v1/captures", headers=hb).json()["items"] == []
    assert detail(h, cap).status_code == 200, "owner data untouched"


def test_user_b_cannot_see_user_a_jobs_tasks_or_reminders(h):
    cap = _a_data(h)
    job_id = h.client.delete(f"/v1/captures/{cap}").json()["purge_job_id"]  # an owner job exists
    assert h.client.get(f"/v1/jobs/{job_id}").status_code == 200
    task = make_task(h, "A private task")
    hb = _b(h)
    assert h.client.get(f"/v1/jobs/{job_id}", headers=hb).status_code == 404
    assert h.client.get(f"/v1/tasks/{task['id']}", headers=hb).status_code == 404
    assert h.client.get("/v1/tasks", headers=hb).json()["items"] == []
    assert h.client.patch(
        f"/v1/tasks/{task['id']}", json={"expected_revision": 1, "priority": 3}, headers=hb
    ).status_code == 404


def test_a_forged_queue_payload_cannot_make_user_b_process_user_a_data(h):
    cap = _a_data(h)
    before = len(h.state.list_proposals(USER, source_id=cap, limit=50).items)
    now = h.clock.now()
    with h.state.transaction(OTHER) as tx:
        tx.append_outbox(
            OutboxEvent(
                id=new_id("obx"), user_id=OTHER, created_at=now, updated_at=now,
                event_type=EventType.EXTRACTION_REQUESTED, target_id=cap, target_revision=1,
                correlation_id="forged", idempotency_key=key("forged"),
                payload={"source_id": cap, "source_version": 1, "range_index": 0, "range_count": 1,
                         "char_start": 0, "char_end": 10},
                available_at=now,
            )
        )
    h.drain()
    assert h.state.list_proposals(OTHER, source_id=cap, limit=50).items == []
    assert len(h.state.list_proposals(USER, source_id=cap, limit=50).items) == before


def test_cross_user_vector_hit_is_rejected_before_any_text_reaches_the_model(h, monkeypatch):
    cap = _a_data(h)
    (memory,) = [m for m in h.state.list_memories(USER, limit=50).items
                 if any(r.source_id == cap for r in m.source_refs)]
    store = h.adapters.memory_store
    # A buggy/poisoned index returns user A's memory for user B's query.
    queried: list[str] = []

    def poisoned(user_id, **kw):
        queried.append(user_id)
        return [(memory.id, 0.99, {"project_id": None})]

    monkeypatch.setattr(store, "query", poisoned)
    results = h.client.post("/v1/search", json={"query": "cache approach"}, headers=_b(h)).json()["results"]
    assert queried == [OTHER], "the poisoned index was really consulted"
    assert results == [], "the foreign hit is dropped at hydration"

    seen: list[str] = []
    real_complete = h.adapters.inference.complete_json

    def spy(**kw):
        seen.append(kw["user_content"])
        return real_complete(**kw)

    monkeypatch.setattr(h.adapters.inference, "complete_json", spy)
    task = h.client.post(
        "/v1/tasks", json={"title": "Fix cache", "state": "ready", "deadline_local_date": "2026-10-07",
                           "timezone": "America/New_York"},
        headers={**_b(h), "Idempotency-Key": key("tb")},
    ).json()
    h.client.post("/v1/plans", json={"local_date": "2026-10-05", "timezone": "America/New_York"},
                  headers={**_b(h), "Idempotency-Key": key("pb")})
    h.tick()
    assert task["id"]
    assert not any(MARK in text for text in seen), "no foreign text reached an inference prompt"


def test_search_never_returns_another_users_memories(h):
    _a_data(h)
    assert h.client.post("/v1/search", json={"query": "quokka cache"}).json()["results"], "owner finds it"
    assert h.client.post("/v1/search", json={"query": "quokka cache"}, headers=_b(h)).json()["results"] == []


# --- export ------------------------------------------------------------- #


def _request_export(h, idem="exp-1", headers=None):
    return h.client.post("/v1/exports", json={}, headers={"Idempotency-Key": idem, **(headers or {})})


def _names_and_text(blob: bytes) -> tuple[list[str], str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = z.namelist()
        return names, " ".join(z.read(n).decode("utf-8", "replace") for n in names)


def test_export_is_owner_scoped_retried_and_downloadable_only_when_ready(h, monkeypatch):
    _a_data(h)
    make_task(h, "Owner A only task")
    h.client.post("/v1/tasks", json={"title": "Bee private zebra"},
                  headers={**_b(h), "Idempotency-Key": key("tb")})
    first = _request_export(h).json()
    assert first["state"] == "queued"
    assert h.client.get(f"/v1/exports/{first['job_id']}/download").status_code == 404, "queued is not downloadable"
    assert _request_export(h).json() == first, "a retried request replays the first response"

    # First attempt fails transiently; the retry completes. No partial download appears meanwhile.
    real_put = h.adapters.source_store.put_bytes
    calls = {"n": 0}

    def flaky(ref, body, *, content_type):
        if ref.key.endswith(".zip") and calls["n"] == 0:
            calls["n"] += 1
            raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "object store unavailable")
        return real_put(ref, body, content_type=content_type)

    monkeypatch.setattr(h.adapters.source_store, "put_bytes", flaky)
    h.drain()
    mid = h.client.get(f"/v1/exports/{first['job_id']}").json()
    assert mid["state"] != "ready" and mid["download_url"] is None
    h.advance(minutes=30)
    h.drain()
    done = h.client.get(f"/v1/exports/{first['job_id']}").json()
    assert done["state"] == "ready" and done["download_url"]
    blob = h.client.get(done["download_url"])
    assert blob.status_code == 200
    names, text = _names_and_text(blob.content)
    assert "Owner A only task" in text and "zebra" not in text, "only the owner's records are bundled"

    hb = _b(h)
    assert h.client.get(f"/v1/exports/{first['job_id']}", headers=hb).status_code == 404
    assert h.client.get(f"/v1/exports/{first['job_id']}/download", headers=hb).status_code == 404


def test_one_exports_request_creates_one_job_even_with_many_worker_passes(h):
    job = _request_export(h, "exp-9").json()["job_id"]
    for _ in range(3):
        h.tick()
    jobs = [j for j in h.state.list_jobs(USER, limit=100).items if j.job_type == "export"]
    assert [j.id for j in jobs] == [job] and jobs[0].state.value == "ready"
