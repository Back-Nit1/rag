"""The end-to-end slice, exercised through real HTTP calls."""

from __future__ import annotations

from datetime import UTC, datetime

from metis_contracts import JobState
from metis_state.object_store import owner_prefix

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
NY = "America/New_York"
USER = "demo_authenticated_subject"

BLOOMBERG = (
    "There is a Bloomberg event in New York I want to go to. Remind me to register and help me prepare."
)


def capture_text(client, text=BLOOMBERG, *, client_capture_id=None, idem="idem-1"):
    body = {
        "kind": "text",
        "text": text,
        "capture_timezone": NY,
        "captured_at": T0.isoformat(),
    }
    if client_capture_id:
        body["client_capture_id"] = client_capture_id
    return client.post("/v1/captures", json=body, headers={"Idempotency-Key": idem})


# --- health and limits ------------------------------------------------------ #


def test_health_reports_fixture_adapters_honestly(client):
    response = client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "local"
    assert body["adapters"]["inference"] == "fixture"
    assert body["adapters"]["transcriber"] == "fixture"
    assert body["contract_version"] == 1


def test_limits_are_published_so_the_browser_enforces_the_same_numbers(client):
    body = client.get("/v1/limits").json()
    assert body["max_audio_seconds"] == 300
    assert body["max_upload_bytes"] == 25 * 1024 * 1024
    assert "audio/webm" in body["allowed_audio_types"]


# --- capture -> proposal ---------------------------------------------------- #


def test_a_text_capture_is_saved_immediately(client):
    response = capture_text(client)
    assert response.status_code == 201
    body = response.json()
    assert body["capture_id"].startswith("src_")
    assert body["revision"] == 1
    assert body["upload"] is None


def test_the_capture_detail_exposes_the_transcript_before_any_extraction(client):
    capture_id = capture_text(client).json()["capture_id"]
    detail = client.get(f"/v1/captures/{capture_id}").json()
    assert detail["transcript_text"] == BLOOMBERG
    assert detail["proposals"] == []


def test_extraction_produces_a_proposal_that_invents_nothing(client, worker):
    capture_id = capture_text(client).json()["capture_id"]
    worker()
    detail = client.get(f"/v1/captures/{capture_id}").json()
    assert detail["proposals"], "the worker should have produced a proposal"
    proposal = detail["proposals"][0]
    assert proposal["requires_clarification"] is True
    assert proposal["adapter_mode"] == "fixture"
    intent = next(i for i in proposal["items"] if i["kind"] == "event_intent")
    assert intent["starts_at"] is None and intent["local_date"] is None
    assert intent["registration_status"] == "unknown"
    assert intent["attendance_status"] == "tentative"


def test_a_repeated_capture_with_the_same_client_id_creates_one_source(client):
    first = capture_text(client, client_capture_id="cli-1", idem="idem-a").json()
    second = capture_text(client, client_capture_id="cli-1", idem="idem-b").json()
    assert first["capture_id"] == second["capture_id"]


def test_a_replayed_idempotency_key_returns_the_first_response(client):
    first = capture_text(client, idem="same-key").json()
    second = capture_text(client, text="a different note", idem="same-key").json()
    assert first["capture_id"] == second["capture_id"]


# --- proposal -> commitments ------------------------------------------------ #


def resolve_all(client, capture_id):
    detail = client.get(f"/v1/captures/{capture_id}").json()
    proposal = detail["proposals"][0]
    decisions = [{"item_index": i, "action": "accept"} for i in range(len(proposal["items"]))]
    return client.post(
        f"/v1/proposals/{proposal['id']}/resolve",
        json={"expected_revision": proposal["revision"], "decisions": decisions},
        headers={"Idempotency-Key": f"resolve-{capture_id}"},
    )


def test_accepting_the_bloomberg_proposal_creates_a_tentative_event_and_tasks(client, worker):
    capture_id = capture_text(client).json()["capture_id"]
    worker()
    response = resolve_all(client, capture_id)
    assert response.status_code == 200
    body = response.json()
    assert body["created_event_ids"], "an event intent should exist"
    assert body["created_task_ids"], "a registration task should exist"

    event = client.get(f"/v1/events/{body['created_event_ids'][0]}").json()
    assert event["attendance_status"] == "tentative", "interest is not a commitment"
    assert event["registration_status"] == "unknown", "registration is never inferred"
    assert event["starts_at"] is None


def test_a_missing_date_produces_a_clarification_and_no_reminder(client, worker):
    capture_id = capture_text(client).json()["capture_id"]
    worker()
    body = resolve_all(client, capture_id).json()
    assert body["created_reminder_ids"] == [], "no reminder without a resolvable date and time"
    assert body["clarifications"], "the user must be asked"
    question = body["clarifications"][0]
    assert question["blocking"] is True
    assert "remind" in question["question"].lower() or "date" in question["question"].lower()


def test_only_one_clarification_thread_per_captured_item(client, worker):
    capture_id = capture_text(client).json()["capture_id"]
    worker()
    body = resolve_all(client, capture_id).json()
    targets = [c["target_id"] for c in body["clarifications"]]
    assert len(targets) == len(set(targets))


def test_a_preparation_task_starts_as_a_candidate_not_an_obligation(client, worker):
    capture_id = capture_text(client).json()["capture_id"]
    worker()
    body = resolve_all(client, capture_id).json()
    tasks = [client.get(f"/v1/tasks/{tid}").json() for tid in body["created_task_ids"]]
    prep = next((t for t in tasks if t["title"].startswith("Prepare")), None)
    assert prep is not None and prep["state"] == "inbox"


def test_a_tentative_thought_stays_tentative(client, worker):
    capture_id = capture_text(
        client, text="Maybe I should go to that Themis workshop at some point.", idem="idem-tent"
    ).json()["capture_id"]
    worker()
    detail = client.get(f"/v1/captures/{capture_id}").json()
    proposal = detail["proposals"][0]
    assert proposal["requires_clarification"] is True
    body = resolve_all(client, capture_id).json()
    for event_id in body["created_event_ids"]:
        assert client.get(f"/v1/events/{event_id}").json()["attendance_status"] == "tentative"


# --- transcript correction -------------------------------------------------- #


def test_correcting_a_transcript_supersedes_the_old_proposal(client, worker):
    capture_id = capture_text(client).json()["capture_id"]
    worker()
    before = client.get(f"/v1/captures/{capture_id}").json()
    assert before["proposals"][0]["disposition"] == "pending"

    response = client.patch(
        f"/v1/captures/{capture_id}/transcript",
        json={
            "expected_revision": before["source"]["revision"],
            "text": "There is a Bloomberg event in New York on the twelfth. Remind me to register.",
        },
    )
    assert response.status_code == 200
    after = response.json()
    assert after["source"]["current_version"] == 2
    assert any(p["disposition"] == "superseded" for p in after["proposals"])
    assert after["transcript_text"].endswith("Remind me to register.")

    worker()
    final = client.get(f"/v1/captures/{capture_id}").json()
    assert any(p["source_version"] == 2 for p in final["proposals"]), "a new proposal is regenerated"


def test_the_original_transcript_stays_reviewable_after_a_correction(client, worker):
    capture_id = capture_text(client).json()["capture_id"]
    worker()
    source = client.get(f"/v1/captures/{capture_id}").json()["source"]
    client.patch(
        f"/v1/captures/{capture_id}/transcript",
        json={"expected_revision": source["revision"], "text": "corrected text"},
    )
    detail = client.get(f"/v1/captures/{capture_id}").json()
    versions = {t["source_version"]: t for t in detail["source"]["transcripts"]}
    assert versions[1]["text"] == BLOOMBERG
    assert versions[2]["edited_by_user"] is True


def test_a_stale_expected_revision_on_a_correction_is_a_conflict(client, worker):
    capture_id = capture_text(client).json()["capture_id"]
    worker()
    response = client.patch(
        f"/v1/captures/{capture_id}/transcript",
        json={"expected_revision": 99, "text": "whatever"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "revision_conflict"
    assert response.json()["current_revision"] >= 1


# --- tasks and concurrency -------------------------------------------------- #


def test_two_simultaneous_edits_produce_one_winner_and_one_conflict(client):
    created = client.post(
        "/v1/tasks",
        json={"title": "Submit the application", "timezone": NY},
        headers={"Idempotency-Key": "task-1"},
    ).json()
    first = client.patch(
        f"/v1/tasks/{created['id']}",
        json={"expected_revision": created["revision"], "priority": 3},
    )
    second = client.patch(
        f"/v1/tasks/{created['id']}",
        json={"expected_revision": created["revision"], "priority": 1},
    )
    assert first.status_code == 200
    assert second.status_code == 409
    assert client.get(f"/v1/tasks/{created['id']}").json()["priority"] == 3


def test_an_omitted_field_is_unchanged_and_an_explicit_null_clears(client):
    created = client.post(
        "/v1/tasks",
        json={"title": "Apply", "deadline_local_date": "2026-10-12", "timezone": NY},
        headers={"Idempotency-Key": "task-2"},
    ).json()
    bumped = client.patch(
        f"/v1/tasks/{created['id']}",
        json={"expected_revision": created["revision"], "priority": 2},
    ).json()
    assert bumped["deadline_local_date"] == "2026-10-12", "an omitted field must not be erased"

    cleared = client.patch(
        f"/v1/tasks/{created['id']}",
        json={"expected_revision": bumped["revision"], "deadline_local_date": None},
    ).json()
    assert cleared["deadline_local_date"] is None


def test_a_dependency_cycle_is_rejected(client):
    a = client.post("/v1/tasks", json={"title": "A"}, headers={"Idempotency-Key": "a"}).json()
    b = client.post("/v1/tasks", json={"title": "B"}, headers={"Idempotency-Key": "b"}).json()
    client.patch(f"/v1/tasks/{b['id']}", json={"expected_revision": b["revision"], "depends_on": [a["id"]]})
    response = client.patch(
        f"/v1/tasks/{a['id']}", json={"expected_revision": a["revision"], "depends_on": [b["id"]]}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "dependency_cycle"


def test_an_invalid_state_transition_is_rejected(client):
    created = client.post("/v1/tasks", json={"title": "A"}, headers={"Idempotency-Key": "t"}).json()
    canceled = client.patch(
        f"/v1/tasks/{created['id']}",
        json={"expected_revision": created["revision"], "state": "canceled"},
    ).json()
    response = client.patch(
        f"/v1/tasks/{created['id']}",
        json={"expected_revision": canceled["revision"], "state": "in_progress"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_state_transition"


def test_completing_a_task_records_the_timestamp_and_history(client):
    created = client.post("/v1/tasks", json={"title": "A"}, headers={"Idempotency-Key": "t"}).json()
    done = client.patch(
        f"/v1/tasks/{created['id']}",
        json={"expected_revision": created["revision"], "state": "done"},
    ).json()
    assert done["state"] == "done" and done["completed_at"] is not None
    history = client.get(f"/v1/tasks/{created['id']}/revisions").json()
    assert [h["revision"] for h in history] == [2, 1]


# --- reminders -------------------------------------------------------------- #


def test_a_reminder_is_pending_schedule_until_the_sync_succeeds(client, worker):
    task = client.post("/v1/tasks", json={"title": "Register"}, headers={"Idempotency-Key": "t"}).json()
    reminder = client.post(
        "/v1/reminders",
        json={
            "target_id": task["id"],
            "target_kind": "task",
            "expected_target_revision": task["revision"],
            "local_date": "2026-10-07",
            "local_time": "09:00",
            "timezone": NY,
        },
        headers={"Idempotency-Key": "rem-1"},
    ).json()
    assert reminder["state"] == "pending_schedule"
    assert reminder["scheduler_ref"] is None

    worker()
    assert client.get(f"/v1/reminders/{reminder['id']}").json()["state"] == "scheduled"


def test_a_nonexistent_local_time_is_refused_rather_than_coerced(client):
    task = client.post("/v1/tasks", json={"title": "X"}, headers={"Idempotency-Key": "t"}).json()
    response = client.post(
        "/v1/reminders",
        json={
            "target_id": task["id"],
            "target_kind": "task",
            "expected_target_revision": task["revision"],
            "local_date": "2026-03-08",
            "local_time": "02:30",
            "timezone": NY,
        },
        headers={"Idempotency-Key": "rem-dst"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "semantic_validation_failed"


def test_an_ambiguous_local_time_asks_rather_than_guessing(client):
    task = client.post("/v1/tasks", json={"title": "X"}, headers={"Idempotency-Key": "t"}).json()
    response = client.post(
        "/v1/reminders",
        json={
            "target_id": task["id"],
            "target_kind": "task",
            "expected_target_revision": task["revision"],
            "local_date": "2026-11-01",
            "local_time": "01:30",
            "timezone": NY,
        },
        headers={"Idempotency-Key": "rem-amb"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ambiguous_target"


def test_completing_a_task_cancels_its_pending_reminder(client, worker):
    task = client.post("/v1/tasks", json={"title": "Register"}, headers={"Idempotency-Key": "t"}).json()
    reminder = client.post(
        "/v1/reminders",
        json={
            "target_id": task["id"],
            "target_kind": "task",
            "expected_target_revision": task["revision"],
            "local_date": "2026-10-07",
            "local_time": "09:00",
            "timezone": NY,
        },
        headers={"Idempotency-Key": "rem-1"},
    ).json()
    worker()
    client.patch(f"/v1/tasks/{task['id']}", json={"expected_revision": task["revision"], "state": "done"})
    worker()
    assert client.get(f"/v1/reminders/{reminder['id']}").json()["state"] == "canceled"


# --- today and planning ----------------------------------------------------- #


def test_today_is_usable_with_no_plan_and_says_availability_is_unknown(client):
    body = client.get("/v1/today").json()
    assert body["local_date"] == "2026-10-05"
    assert body["timezone"] == NY
    assert body["plan"] is None
    assert body["availability_configured"] is False, "free time must not look discovered"


def test_overdue_work_is_listed_separately(client):
    client.post(
        "/v1/tasks",
        json={"title": "Late thing", "deadline_local_date": "2026-10-01", "timezone": NY},
        headers={"Idempotency-Key": "t-late"},
    )
    body = client.get("/v1/today").json()
    assert [t["title"] for t in body["overdue_tasks"]] == ["Late thing"]


def test_a_plan_request_returns_a_job_and_generates_an_outline(client, worker):
    client.post(
        "/v1/availability",
        json={
            "id": "avail_1",
            "user_id": "ignored",
            "created_at": T0.isoformat(),
            "updated_at": T0.isoformat(),
            "local_date": "2026-10-05",
            "timezone": NY,
            "window": {"start": "11:00", "end": "17:00"},
            "kind": "available",
        },
    )
    client.post(
        "/v1/tasks",
        json={"title": "Submit the application", "deadline_local_date": "2026-10-12", "timezone": NY},
        headers={"Idempotency-Key": "t-apply"},
    )
    response = client.post(
        "/v1/plans",
        json={"local_date": "2026-10-05", "timezone": NY},
        headers={"Idempotency-Key": "plan-1"},
    )
    assert response.status_code == 202
    plan_id = response.json()["plan_id"]
    worker()
    plan = client.get(f"/v1/plans/{plan_id}").json()
    assert plan["status"] in ("proposed", "fallback")
    assert plan["validation"]["checked_by"] == "application"
    assert plan["blocks"], "an outline should have been produced"


def test_availability_ownership_comes_from_the_token_not_the_body(client):
    created = client.post(
        "/v1/availability",
        json={
            "id": "avail_x",
            "user_id": "someone_else",
            "created_at": T0.isoformat(),
            "updated_at": T0.isoformat(),
            "local_date": "2026-10-05",
            "timezone": NY,
            "window": {"start": "09:00", "end": "10:00"},
            "kind": "available",
        },
    ).json()
    assert created["user_id"] == USER


def test_adding_a_commitment_invalidates_an_existing_plan(client, worker):
    client.post(
        "/v1/plans",
        json={"local_date": "2026-10-05", "timezone": NY},
        headers={"Idempotency-Key": "plan-1"},
    )
    worker()
    before = client.get("/v1/today").json()
    epoch_before = before["planning_epoch"]

    task = client.post("/v1/tasks", json={"title": "Snuck in"}, headers={"Idempotency-Key": "t-new"}).json()
    client.patch(f"/v1/tasks/{task['id']}", json={"expected_revision": task["revision"], "priority": 3})

    after = client.get("/v1/today").json()
    assert after["planning_epoch"] > epoch_before
    assert after["plan_stale"] is True
    assert after["plan_stale_reason"]


def test_accepting_a_stale_plan_is_refused(client, worker):
    plan_id = client.post(
        "/v1/plans",
        json={"local_date": "2026-10-05", "timezone": NY},
        headers={"Idempotency-Key": "plan-1"},
    ).json()["plan_id"]
    worker()
    plan = client.get(f"/v1/plans/{plan_id}").json()
    task = client.post(
        "/v1/tasks", json={"title": "Changed my mind"}, headers={"Idempotency-Key": "t"}
    ).json()
    client.patch(f"/v1/tasks/{task['id']}", json={"expected_revision": task["revision"], "priority": 3})

    response = client.post(
        f"/v1/plans/{plan_id}/accept",
        json={"expected_revision": plan["revision"], "expected_planning_epoch": plan["planning_epoch"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "epoch_conflict"
    assert client.get(f"/v1/plans/{plan_id}").json()["status"] == "invalidated"


# --- deletion and export ---------------------------------------------------- #


def test_deleting_a_capture_removes_access_immediately(client, worker):
    capture_id = capture_text(client).json()["capture_id"]
    worker()
    response = client.delete(f"/v1/captures/{capture_id}")
    assert response.status_code == 200
    assert response.json()["tombstoned"] is True
    assert client.get(f"/v1/captures/{capture_id}").status_code == 404


def test_a_deleted_capture_leaves_an_independently_edited_task_in_place(client, worker):
    capture_id = capture_text(client).json()["capture_id"]
    worker()
    created = resolve_all(client, capture_id).json()
    task_id = created["created_task_ids"][0]
    task = client.get(f"/v1/tasks/{task_id}").json()
    client.patch(
        f"/v1/tasks/{task_id}",
        json={"expected_revision": task["revision"], "title": "My own wording"},
    )
    client.delete(f"/v1/captures/{capture_id}")
    worker()
    after = client.get(f"/v1/tasks/{task_id}").json()
    assert after["title"] == "My own wording"
    assert after["source_unavailable"] is True, "the missing source must be disclosed"


IDEM = {"Idempotency-Key": "exp-1"}


def _export(client, key="exp-1", **body):
    return client.post("/v1/exports", json=body, headers={"Idempotency-Key": key})


def test_an_export_is_queued_with_a_typed_outbox_event(client, adapters):
    response = _export(client, include_memories=False)
    assert response.status_code == 202
    assert response.json()["state"] == "queued"
    job_id = response.json()["job_id"]
    events = [
        e
        for e in adapters.state_store.claim_outbox(owner="t", limit=10, lease_seconds=30)
        if e.event_type == "export.requested"
    ]
    assert events[0].payload == {
        "job_id": job_id,
        "include_sources": True,
        "include_memories": False,
        "include_commitments": True,
    }


def test_an_export_requires_an_idempotency_key(client):
    assert client.post("/v1/exports", json={}).status_code == 422


def test_a_retried_export_replays_the_first_response(client, adapters):
    first = _export(client).json()
    assert _export(client).json() == first
    events = [
        e
        for e in adapters.state_store.claim_outbox(owner="t", limit=10, lease_seconds=30)
        if e.event_type == "export.requested"
    ]
    assert len(events) == 1
    assert _export(client, key="exp-2").json()["job_id"] != first["job_id"]


def _set_job(adapters, job_id, **update):
    job = adapters.state_store.get(USER, "job", job_id)
    with adapters.state_store.transaction(USER) as tx:
        tx.put("job", job.model_copy(update=update), expected_revision=job.revision)


def test_a_queued_export_is_never_downloadable(client):
    job_id = _export(client).json()["job_id"]
    body = client.get(f"/v1/exports/{job_id}").json()
    assert body["state"] == "queued" and body["download_url"] is None
    assert client.get(f"/v1/exports/{job_id}/download").status_code == 404


def test_a_failed_export_reports_failure_without_a_download(client, adapters):
    job_id = _export(client).json()["job_id"]
    _set_job(adapters, job_id, state=JobState.FAILED, error_message="boom", result_refs={})
    body = client.get(f"/v1/exports/{job_id}").json()
    assert body["state"] == "failed" and body["error_message"] == "boom" and body["download_url"] is None


def test_a_succeeded_job_without_a_verified_result_is_not_downloadable(client, adapters):
    job_id = _export(client).json()["job_id"]
    _set_job(adapters, job_id, state=JobState.READY)
    assert client.get(f"/v1/exports/{job_id}").json()["download_url"] is None


def _complete(client, adapters, job_id, key=None, payload=b"zipbytes"):
    import hashlib

    from metis_contracts.entities import ObjectRef

    key = key or f"{owner_prefix(USER)}/exports/{job_id}.zip"
    adapters.source_store.put_bytes(ObjectRef(bucket="", key=key), payload, content_type="application/zip")
    _set_job(
        adapters,
        job_id,
        state=JobState.READY,
        result_refs={
            "object_key": key,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": str(len(payload)),
        },
    )


def test_a_completed_export_downloads_after_ownership_checks(client, adapters):
    job_id = _export(client).json()["job_id"]
    _complete(client, adapters, job_id)
    body = client.get(f"/v1/exports/{job_id}").json()
    assert body["state"] == "ready" and body["byte_size"] == 8 and body["download_url"]
    download = client.get(body["download_url"])
    assert download.status_code == 200 and download.content == b"zipbytes"


def test_a_result_key_outside_the_owner_prefix_is_refused(client, adapters):
    job_id = _export(client).json()["job_id"]
    _complete(client, adapters, job_id, key="someone_else/exports/x.zip")
    assert client.get(f"/v1/exports/{job_id}").json()["download_url"] is None
    assert client.get(f"/v1/exports/{job_id}/download").status_code == 404


def test_a_corrupted_archive_is_not_served(client, adapters):
    job_id = _export(client).json()["job_id"]
    _complete(client, adapters, job_id)
    from metis_contracts.entities import ObjectRef

    adapters.source_store.put_bytes(
        ObjectRef(bucket="", key=f"{owner_prefix(USER)}/exports/{job_id}.zip"),
        b"tampered",
        content_type="application/zip",
    )
    assert client.get(f"/v1/exports/{job_id}/download").status_code == 409


def test_a_deleted_export_job_is_not_found(client, adapters):
    job_id = _export(client).json()["job_id"]
    adapters.state_store.tombstone(USER, "job", job_id)
    assert client.get(f"/v1/exports/{job_id}").status_code == 404


def test_another_users_export_is_not_found(client, adapters):
    job_id = _export(client).json()["job_id"]
    other = adapters.state_store.get(USER, "job", job_id).model_copy(
        update={"id": "job_foreign", "user_id": "other"}
    )
    with adapters.state_store.transaction("other") as tx:
        tx.put("job", other)
    assert client.get("/v1/exports/job_foreign").status_code == 404
    assert client.get("/v1/exports/job_foreign/download").status_code == 404


# --- errors ----------------------------------------------------------------- #


def test_a_missing_entity_is_a_contract_shaped_404(client):
    response = client.get("/v1/tasks/task_nope")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found" and body["correlation_id"]


def test_a_contract_violation_returns_the_same_error_shape(client):
    response = client.post("/v1/captures", json={"kind": "text", "capture_timezone": NY})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


def test_image_capture_is_declined_plainly_rather_than_faked(client):
    response = client.post(
        "/v1/captures",
        json={"kind": "image", "capture_timezone": NY, "captured_at": T0.isoformat()},
    )
    assert response.status_code == 415
    assert "not implemented yet" in response.json()["message"]


def test_every_response_carries_a_correlation_id(client):
    response = client.get("/v1/health")
    assert response.headers["X-Correlation-Id"]


# --- backend gaps: purge job, capture list, availability edit/delete, vapid -- #


def test_purge_job_is_queued_until_the_worker_finishes_then_ready(client, worker):
    capture_id = capture_text(client).json()["capture_id"]
    worker()
    job_id = client.delete(f"/v1/captures/{capture_id}").json()["purge_job_id"]
    assert client.get(f"/v1/jobs/{job_id}").json()["state"] == "queued"
    worker()
    job = client.get(f"/v1/jobs/{job_id}").json()
    assert job["state"] == JobState.READY.value
    assert job["finished_at"] is not None
    assert job["result_refs"]["not_purged"] == "transcripts,proposals"


def test_list_captures_is_owner_scoped_paged_and_hides_deleted(client):
    ids = [capture_text(client, f"note {i}", idem=f"k{i}").json()["capture_id"] for i in range(3)]
    first = client.get("/v1/captures?limit=2").json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    rest = client.get(f"/v1/captures?limit=2&cursor={first['next_cursor']}").json()
    assert {i["id"] for i in first["items"] + rest["items"]} == set(ids)
    client.delete(f"/v1/captures/{ids[0]}")
    assert ids[0] not in {i["id"] for i in client.get("/v1/captures").json()["items"]}


def _avail(**over):
    return {
        "id": "avail_e",
        "user_id": "x",
        "created_at": T0.isoformat(),
        "updated_at": T0.isoformat(),
        "local_date": "2026-10-05",
        "timezone": NY,
        "window": {"start": "09:00", "end": "10:00"},
        "kind": "available",
        **over,
    }


def test_availability_patch_and_delete_are_revision_checked(client):
    client.post("/v1/availability", json=_avail())
    stale = client.patch("/v1/availability/avail_e?expected_revision=9", json=_avail(kind="busy"))
    assert stale.status_code == 409
    ok = client.patch("/v1/availability/avail_e?expected_revision=1", json=_avail(kind="busy"))
    assert ok.status_code == 200 and ok.json()["kind"] == "busy" and ok.json()["revision"] == 2
    assert client.delete("/v1/availability/avail_e?expected_revision=1").status_code == 409
    before = client.app.state.adapters.state_store.get_planning_epoch(USER)
    assert client.delete("/v1/availability/avail_e?expected_revision=2").status_code == 204
    assert client.app.state.adapters.state_store.get_planning_epoch(USER) > before
    assert client.get("/v1/availability?local_date=2026-10-05").json() == []
    assert client.delete("/v1/availability/avail_e?expected_revision=2").status_code == 404


def test_limits_expose_only_the_public_vapid_key(client, monkeypatch):
    body = client.get("/v1/limits").json()
    assert body["vapid_public_key"] is None
    assert not any("private" in k for k in body)


def test_limits_publish_the_vapid_public_key_when_configured(client, monkeypatch):
    from metis_contracts import reset_settings_cache

    monkeypatch.setenv("METIS_VAPID_PUBLIC_KEY", "BPublicKey")
    reset_settings_cache()
    body = client.get("/v1/limits").json()
    assert body["vapid_public_key"] == "BPublicKey"
    assert "vapid_private_key_param" not in body


def test_list_reminders_pages_and_filters_by_state(client, worker):
    task = client.post("/v1/tasks", json={"title": "Register"}, headers={"Idempotency-Key": "t"}).json()
    for i, day in enumerate(("07", "08", "09")):
        client.post(
            "/v1/reminders",
            json={
                "target_id": task["id"],
                "target_kind": "task",
                "expected_target_revision": task["revision"],
                "local_date": f"2026-10-{day}",
                "local_time": "09:00",
                "timezone": NY,
            },
            headers={"Idempotency-Key": f"rem-{i}"},
        )
    assert {r["state"] for r in client.get("/v1/reminders").json()["items"]} == {"pending_schedule"}
    worker()
    everything = client.get("/v1/reminders").json()["items"]
    assert len(everything) == 3 and {r["state"] for r in everything} == {"scheduled"}
    assert client.get("/v1/reminders?state=canceled").json()["items"] == []
    first = client.get("/v1/reminders?limit=2").json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    rest = client.get(f"/v1/reminders?limit=2&cursor={first['next_cursor']}").json()
    assert [r["id"] for r in first["items"] + rest["items"]] == [r["id"] for r in everything]
    assert client.get("/v1/reminders?limit=101").status_code == 422
