"""Capture deletion: immediate tombstone, then a resumable purge of derived data."""

from __future__ import annotations

import pytest
from e2e_support import USER, accept_all, capture, detail, patch
from metis_contracts import ErrorCode, MetisError

NOTE = "I tried the stale cache approach on the Cerno build and it failed."


def _indexed_capture(h, text=NOTE):
    cap = capture(h, text).json()["capture_id"]
    h.tick()
    accept_all(h, cap)
    h.tick()
    (memory,) = [
        m
        for m in h.state.list_memories(USER, limit=50).items
        if any(r.source_id == cap for r in m.source_refs)
    ]
    assert memory.indexed_at is not None, "the fixture embedder indexed the memory"
    return cap, memory


def _vector_hits(h, text=NOTE):
    vector = h.adapters.embedder.embed([text])[0]
    return h.adapters.memory_store.query(
        USER, vector=vector, text=text, project_id=None, include_historical=True, limit=10
    )


def _search(h, query="stale cache approach"):
    body = h.client.post("/v1/search", json={"query": query, "include_historical": True}).json()
    return body["results"]


def test_deleted_capture_is_inaccessible_immediately_even_before_the_purge_runs(h):
    cap, _ = _indexed_capture(h)
    assert _search(h), "baseline: the memory is retrievable"
    resp = h.client.delete(f"/v1/captures/{cap}")
    assert resp.status_code == 200 and resp.json()["tombstoned"] is True
    # No worker tick yet: access is already gone.
    assert detail(h, cap).status_code == 404
    assert cap not in {c["id"] for c in h.client.get("/v1/captures").json()["items"]}
    assert _search(h) == [], "derived evidence from a deleted source is never returned"
    job = h.client.get(f"/v1/jobs/{resp.json()['purge_job_id']}").json()
    assert job["state"] == "queued", "purge pending, not claimed done"


def test_purge_removes_object_vectors_and_marks_the_job_ready_only_when_finished(h):
    cap, _ = _indexed_capture(h)
    source = h.state.get(USER, "source", cap)
    assert _vector_hits(h), "baseline: a vector exists"
    job_id = h.client.delete(f"/v1/captures/{cap}").json()["purge_job_id"]
    h.tick()
    job = h.client.get(f"/v1/jobs/{job_id}").json()
    assert job["state"] == "ready" and job["finished_at"]
    assert _vector_hits(h) == [], "vectors removed"
    assert h.state.get(USER, "source", cap) is None
    if source.object_ref is not None:
        assert h.adapters.source_store.head(source.object_ref) is None, "object bytes removed"


def _fail_first_vector_delete(h, monkeypatch):
    real = h.adapters.memory_store.delete
    calls = {"n": 0}

    def flaky(user_id, ids):
        calls["n"] += 1
        if calls["n"] == 1:
            raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "vector store unavailable")
        return real(user_id, ids)

    monkeypatch.setattr(h.adapters.memory_store, "delete", flaky)


def test_purge_retries_after_a_mid_purge_failure_and_access_stays_removed(h, monkeypatch):
    cap, _ = _indexed_capture(h)
    job_id = h.client.delete(f"/v1/captures/{cap}").json()["purge_job_id"]
    _fail_first_vector_delete(h, monkeypatch)
    h.drain()
    assert h.client.get(f"/v1/jobs/{job_id}").json()["state"] != "ready", "not done while a step failed"
    assert detail(h, cap).status_code == 404, "access stays removed during the failure"
    assert _search(h) == []
    h.advance(minutes=30)
    h.drain()
    assert h.client.get(f"/v1/jobs/{job_id}").json()["state"] == "ready"


def test_resumed_purge_actually_removes_the_vector(h, monkeypatch):
    cap, _ = _indexed_capture(h)
    h.client.delete(f"/v1/captures/{cap}")
    _fail_first_vector_delete(h, monkeypatch)
    h.drain()
    h.advance(minutes=30)
    h.drain()
    assert _vector_hits(h) == []


def test_independently_edited_task_survives_purge_with_a_disclosed_missing_source(h):
    cap = capture(h).json()["capture_id"]
    h.tick()
    task_id = accept_all(h, cap).json()["created_task_ids"][0]
    task = h.client.get(f"/v1/tasks/{task_id}").json()
    patch(h, f"/v1/tasks/{task_id}", task, title="My own wording")
    h.client.delete(f"/v1/captures/{cap}")
    h.tick()
    after = h.client.get(f"/v1/tasks/{task_id}").json()
    assert after["title"] == "My own wording" and after["source_unavailable"] is True


def test_purge_also_removes_derived_proposals(h):
    cap, _ = _indexed_capture(h)
    h.client.delete(f"/v1/captures/{cap}")
    h.tick()
    assert h.state.list_proposals(USER, source_id=cap, limit=50).items == []
