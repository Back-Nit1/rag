"""The end-to-end slice, exercised through real HTTP calls."""

from __future__ import annotations

from datetime import UTC, datetime

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


def test_an_export_is_queued_as_a_job(client):
    response = client.post("/v1/exports", json={})
    assert response.status_code == 202
    assert response.json()["state"] == "queued"


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
