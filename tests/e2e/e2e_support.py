"""Small request helpers shared by the E2E scenarios (no assertions on payload dumps)."""

from __future__ import annotations

from itertools import count

from datetime import UTC, datetime

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # 10:00 in New York
NY = "America/New_York"
USER = "demo_authenticated_subject"
OTHER = "other_authenticated_subject"

_keys = count(1)

BLOOMBERG = (
    "There is a Bloomberg event in New York I want to go to. Remind me to register and help me prepare."
)


def key(prefix: str = "k") -> str:
    return f"{prefix}-{next(_keys)}"


def capture(h, text=BLOOMBERG, *, client_capture_id=None, idem=None, headers=None):
    body = {"kind": "text", "text": text, "capture_timezone": NY, "captured_at": T0.isoformat()}
    if client_capture_id:
        body["client_capture_id"] = client_capture_id
    return h.client.post(
        "/v1/captures", json=body, headers={"Idempotency-Key": idem or key("cap"), **(headers or {})}
    )


def detail(h, capture_id, headers=None):
    return h.client.get(f"/v1/captures/{capture_id}", headers=headers)


def accept_all(h, capture_id, *, edits=None):
    """Resolve the first proposal; `edits` maps item_index -> ExtractedItem dict."""
    proposal = detail(h, capture_id).json()["proposals"][0]
    decisions = []
    for index in range(len(proposal["items"])):
        if edits and index in edits:
            decisions.append({"item_index": index, "action": "edit", "edited": edits[index]})
        else:
            decisions.append({"item_index": index, "action": "accept"})
    return h.client.post(
        f"/v1/proposals/{proposal['id']}/resolve",
        json={"expected_revision": proposal["revision"], "decisions": decisions},
        headers={"Idempotency-Key": key("resolve")},
    )


def make_task(h, title="Submit the application", **fields):
    response = h.client.post(
        "/v1/tasks", json={"title": title, "timezone": NY, **fields}, headers={"Idempotency-Key": key("task")}
    )
    assert response.status_code == 201, response.status_code
    return response.json()


def patch(h, path, current, **fields):
    response = h.client.patch(path, json={"expected_revision": current["revision"], **fields})
    assert response.status_code == 200, (path, response.status_code, response.json().get("code"))
    return response.json()


def add_reminder(h, task, local_date="2026-10-07", local_time="09:00"):
    response = h.client.post(
        "/v1/reminders",
        json={
            "target_id": task["id"],
            "target_kind": "task",
            "expected_target_revision": task["revision"],
            "local_date": local_date,
            "local_time": local_time,
            "timezone": NY,
        },
        headers={"Idempotency-Key": key("rem")},
    )
    assert response.status_code == 201, (response.status_code, response.json().get("code"))
    return response.json()


def reminder(h, reminder_id):
    return h.client.get(f"/v1/reminders/{reminder_id}").json()


def fire_stale(h, reminder_id, occurrence_key, user_id=USER):
    """Simulate an external scheduler firing a (possibly outdated) occurrence."""
    from metis_contracts import EventType, OutboxEvent, new_id
    from metis_contracts.events import ReminderDuePayload

    now = h.clock.now()
    with h.state.transaction(user_id) as tx:
        tx.append_outbox(
            OutboxEvent(
                id=new_id("obx"),
                user_id=user_id,
                created_at=now,
                updated_at=now,
                event_type=EventType.REMINDER_DUE,
                target_id=reminder_id,
                target_revision=1,
                correlation_id=f"stale:{occurrence_key}",
                idempotency_key=f"stale:{occurrence_key}:{new_id('x')}",
                payload=ReminderDuePayload(
                    reminder_id=reminder_id, occurrence_key=occurrence_key, scheduled_for=now
                ).model_dump(mode="json"),
                available_at=now,
            )
        )


def notifications(h):
    return h.client.get("/v1/notifications").json()["items"]


DUE = datetime(2026, 10, 7, 13, 0, tzinfo=UTC)  # 09:00 New York on 2026-10-07
