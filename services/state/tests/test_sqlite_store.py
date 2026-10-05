"""State invariants. Every other service assumes these hold."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from metis_contracts import (
    ErrorCode,
    Event,
    FixedClock,
    JobState,
    MetisError,
    OutboxEvent,
    Task,
    TaskState,
    reset_settings_cache,
)
from metis_contracts.entities import Job
from metis_state.sqlite_store import SqliteStateStore

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)
USER = "user_a"
OTHER = "user_b"


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture()
def store(tmp_path, clock, monkeypatch) -> SqliteStateStore:
    monkeypatch.setenv("METIS_DAILY_MICRO_USD", "1000")
    reset_settings_cache()
    yield SqliteStateStore(str(tmp_path / "state.sqlite"), clock=clock)
    reset_settings_cache()


def make_task(user: str = USER, task_id: str = "task_1", **kw) -> Task:
    kw.setdefault("title", "Register")
    return Task(id=task_id, user_id=user, created_at=T0, updated_at=T0, **kw)


def outbox_event(user: str = USER, key: str = "k1", **kw) -> OutboxEvent:
    return OutboxEvent(
        id=kw.pop("id", f"obx_{key}"),
        user_id=user,
        created_at=T0,
        updated_at=T0,
        event_type=kw.pop("event_type", "task.changed"),
        correlation_id="corr_1",
        idempotency_key=key,
        available_at=T0,
        **kw,
    )


# --- revisions -------------------------------------------------------------- #


def test_first_write_starts_at_revision_one(store):
    with store.transaction(USER) as tx:
        stored = tx.put("task", make_task())
    assert stored.revision == 1
    assert store.get(USER, "task", "task_1").title == "Register"


def test_second_write_without_expected_revision_is_a_conflict(store):
    with store.transaction(USER) as tx:
        tx.put("task", make_task())
    with pytest.raises(MetisError) as exc, store.transaction(USER) as tx:
        tx.put("task", make_task(title="Changed"))
    assert exc.value.code is ErrorCode.REVISION_CONFLICT
    assert exc.value.current_revision == 1


def test_stale_expected_revision_loses_and_reports_current(store):
    with store.transaction(USER) as tx:
        first = tx.put("task", make_task())
    with store.transaction(USER) as tx:
        tx.put("task", first.model_copy(update={"title": "Winner"}), expected_revision=1)
    with pytest.raises(MetisError) as exc, store.transaction(USER) as tx:
        tx.put("task", first.model_copy(update={"title": "Loser"}), expected_revision=1)
    assert exc.value.code is ErrorCode.REVISION_CONFLICT
    assert exc.value.current_revision == 2
    assert store.get(USER, "task", "task_1").title == "Winner"


def test_created_at_is_preserved_across_updates(store, clock):
    with store.transaction(USER) as tx:
        tx.put("task", make_task())
    clock.advance(hours=3)
    with store.transaction(USER) as tx:
        updated = tx.put("task", make_task(title="Edited"), expected_revision=1)
    assert updated.created_at == T0
    assert updated.updated_at == T0 + timedelta(hours=3)


def test_update_of_missing_record_is_not_found(store):
    with pytest.raises(MetisError) as exc, store.transaction(USER) as tx:
        tx.put("task", make_task(), expected_revision=1)
    assert exc.value.code is ErrorCode.NOT_FOUND


def test_revision_history_records_changed_fields(store):
    with store.transaction(USER) as tx:
        first = tx.put("task", make_task())
    with store.transaction(USER) as tx:
        tx.put("task", first.model_copy(update={"priority": 3}), expected_revision=1)
    history = store.get_revisions(USER, "task", "task_1")
    assert [h["revision"] for h in history] == [2, 1]
    assert "priority" in history[0]["changed_fields"]


# --- ownership -------------------------------------------------------------- #


def test_cross_user_write_is_forbidden(store):
    with pytest.raises(MetisError) as exc, store.transaction(USER) as tx:
        tx.put("task", make_task(user=OTHER))
    assert exc.value.code is ErrorCode.FORBIDDEN


def test_cross_user_read_returns_nothing(store):
    with store.transaction(USER) as tx:
        tx.put("task", make_task())
    assert store.get(OTHER, "task", "task_1") is None
    assert store.list_tasks(OTHER).items == []


# --- outbox atomicity ------------------------------------------------------- #


def test_entity_and_outbox_commit_together(store):
    with store.transaction(USER) as tx:
        tx.put("task", make_task())
        tx.append_outbox(outbox_event())
    assert store.get(USER, "task", "task_1") is not None
    assert len(store.claim_outbox(owner="w1", limit=10, lease_seconds=60)) == 1


def test_a_failed_transaction_leaves_no_outbox_event(store):
    with pytest.raises(RuntimeError):
        with store.transaction(USER) as tx:
            tx.put("task", make_task())
            tx.append_outbox(outbox_event())
            raise RuntimeError("worker crashed after the write")
    assert store.get(USER, "task", "task_1") is None
    assert store.claim_outbox(owner="w1", limit=10, lease_seconds=60) == []


def test_duplicate_outbox_idempotency_key_yields_one_event(store):
    with store.transaction(USER) as tx:
        tx.append_outbox(outbox_event(key="same", id="obx_1"))
        tx.append_outbox(outbox_event(key="same", id="obx_2"))
    assert len(store.claim_outbox(owner="w1", limit=10, lease_seconds=60)) == 1


def test_a_live_lease_blocks_a_second_dispatcher(store):
    with store.transaction(USER) as tx:
        tx.append_outbox(outbox_event())
    assert len(store.claim_outbox(owner="w1", limit=10, lease_seconds=60)) == 1
    assert store.claim_outbox(owner="w2", limit=10, lease_seconds=60) == []


def test_an_expired_lease_is_reclaimed(store, clock):
    with store.transaction(USER) as tx:
        tx.append_outbox(outbox_event())
    store.claim_outbox(owner="w1", limit=10, lease_seconds=60)
    clock.advance(seconds=61)
    reclaimed = store.claim_outbox(owner="w2", limit=10, lease_seconds=60)
    assert len(reclaimed) == 1
    assert reclaimed[0].attempts == 2


def test_dead_lettered_events_are_listable(store):
    with store.transaction(USER) as tx:
        tx.append_outbox(outbox_event())
    event = store.claim_outbox(owner="w1", limit=1, lease_seconds=60)[0]
    store.complete_outbox(event.id, user_id=USER, state="dead_letter", error="unsupported_media")
    assert [e.id for e in store.list_dead_letters()] == [event.id]


# --- planning epoch --------------------------------------------------------- #


def test_task_change_bumps_the_planning_epoch(store):
    assert store.get_planning_epoch(USER) == 0
    with store.transaction(USER) as tx:
        tx.put("task", make_task())
    assert store.get_planning_epoch(USER) == 1


def test_a_non_planning_record_does_not_bump_the_epoch(store):
    with store.transaction(USER) as tx:
        tx.put(
            "notification",
            __import__("metis_contracts").Notification(
                id="not_1", user_id=USER, created_at=T0, updated_at=T0, title="t",
                body="b", channel="in_app", dedupe_key="d1",
            ),
        )
    assert store.get_planning_epoch(USER) == 0


def test_a_new_commitment_bumps_the_epoch_even_when_unrelated_to_a_plan(store):
    with store.transaction(USER) as tx:
        tx.put("task", make_task())
    before = store.get_planning_epoch(USER)
    with store.transaction(USER) as tx:
        tx.put("task", make_task(task_id="task_unrelated"))
    assert store.get_planning_epoch(USER) == before + 1


# --- queries ---------------------------------------------------------------- #


def test_task_filters_by_state_and_project(store):
    with store.transaction(USER) as tx:
        tx.put("task", make_task(task_id="task_open", state=TaskState.READY, project_id="proj_a"))
        tx.put(
            "task",
            make_task(task_id="task_done", state=TaskState.DONE, completed_at=T0, project_id="proj_b"),
        )
    ready = store.list_tasks(USER, states=[TaskState.READY])
    assert [t.id for t in ready.items] == ["task_open"]
    assert [t.id for t in store.list_tasks(USER, project_id="proj_b").items] == ["task_done"]


def test_a_truncated_page_is_marked_incomplete(store):
    with store.transaction(USER) as tx:
        for i in range(5):
            tx.put("task", make_task(task_id=f"task_{i}"))
    page = store.list_tasks(USER, limit=2)
    assert len(page.items) == 2
    assert page.complete is False and page.next_cursor is not None
    rest = store.list_tasks(USER, limit=10, cursor=page.next_cursor)
    assert rest.complete is True and len(rest.items) == 3


def test_events_filter_by_local_date_range(store):
    from datetime import date

    with store.transaction(USER) as tx:
        tx.put(
            "event",
            Event(
                id="ev_1", user_id=USER, created_at=T0, updated_at=T0, title="Bloomberg event",
                local_date=date(2026, 10, 8), timezone="America/New_York",
            ),
        )
    assert store.list_events(USER, local_date=date(2026, 10, 8)).items
    assert not store.list_events(USER, local_date=date(2026, 10, 9)).items


# --- idempotency and claims ------------------------------------------------- #


def test_idempotent_response_is_replayed(store):
    store.remember_idempotent(USER, "idem-1", {"capture_id": "src_1"})
    assert store.recall_idempotent(USER, "idem-1") == {"capture_id": "src_1"}
    store.remember_idempotent(USER, "idem-1", {"capture_id": "src_2"})
    assert store.recall_idempotent(USER, "idem-1") == {"capture_id": "src_1"}
    assert store.recall_idempotent(OTHER, "idem-1") is None


def test_only_one_worker_wins_an_occurrence_claim(store):
    key = "rem_1:2026-10-07T13:00:00Z"
    assert store.claim_once(USER, key, "w1") is True
    assert store.claim_once(USER, key, "w2") is False


# --- budget ----------------------------------------------------------------- #


def test_concurrent_reservations_cannot_both_pass_the_same_remaining_budget(store, clock):
    day = clock.today_local("UTC")
    store.reserve_budget(USER, local_date=day, micro_usd=600, operation="extract", correlation_id="c1")
    with pytest.raises(MetisError) as exc:
        store.reserve_budget(USER, local_date=day, micro_usd=600, operation="extract", correlation_id="c2")
    assert exc.value.code is ErrorCode.BUDGET_EXCEEDED
    assert store.get_usage(USER, day).reserved_micro_usd == 600


def test_settling_below_the_reservation_frees_the_difference(store, clock):
    day = clock.today_local("UTC")
    res = store.reserve_budget(USER, local_date=day, micro_usd=600, operation="extract", correlation_id="c1")
    store.settle_budget(res, user_id=USER, actual_micro_usd=100, input_tokens=900, output_tokens=120)
    usage = store.get_usage(USER, day)
    assert usage.reserved_micro_usd == 0 and usage.settled_micro_usd == 100
    store.reserve_budget(USER, local_date=day, micro_usd=600, operation="extract", correlation_id="c2")


def test_an_uncertain_failure_retains_the_conservative_charge(store, clock):
    day = clock.today_local("UTC")
    res = store.reserve_budget(USER, local_date=day, micro_usd=400, operation="extract", correlation_id="c1")
    store.settle_budget(res, user_id=USER, actual_micro_usd=None, uncertain=True)
    usage = store.get_usage(USER, day)
    assert usage.settled_micro_usd == 400, "a timed-out call may still have been billed"


def test_a_never_started_request_releases_its_reservation(store, clock):
    day = clock.today_local("UTC")
    res = store.reserve_budget(USER, local_date=day, micro_usd=400, operation="extract", correlation_id="c1")
    store.settle_budget(res, user_id=USER, actual_micro_usd=None)
    usage = store.get_usage(USER, day)
    assert usage.reserved_micro_usd == 0 and usage.settled_micro_usd == 0


def test_settling_twice_is_idempotent(store, clock):
    day = clock.today_local("UTC")
    res = store.reserve_budget(USER, local_date=day, micro_usd=300, operation="extract", correlation_id="c1")
    store.settle_budget(res, user_id=USER, actual_micro_usd=50)
    store.settle_budget(res, user_id=USER, actual_micro_usd=50)
    assert store.get_usage(USER, day).settled_micro_usd == 50


# --- jobs ------------------------------------------------------------------- #


def test_job_lease_is_exclusive_until_it_expires(store, clock):
    job = Job(
        id="job_1", user_id=USER, created_at=T0, updated_at=T0, job_type="transcribe",
        correlation_id="c1", idempotency_key="transcribe:src_1:1",
    )
    with store.transaction(USER) as tx:
        tx.put("job", job)
    assert store.claim_job("job_1", user_id=USER, owner="w1", lease_seconds=60) is not None
    assert store.claim_job("job_1", user_id=USER, owner="w2", lease_seconds=60) is None
    clock.advance(seconds=61)
    assert store.claim_job("job_1", user_id=USER, owner="w2", lease_seconds=60) is not None


def test_terminal_job_state_clears_the_lease(store):
    job = Job(
        id="job_1", user_id=USER, created_at=T0, updated_at=T0, job_type="transcribe",
        correlation_id="c1", idempotency_key="transcribe:src_1:1",
    )
    with store.transaction(USER) as tx:
        tx.put("job", job)
    store.claim_job("job_1", user_id=USER, owner="w1", lease_seconds=60)
    done = store.set_job_state("job_1", user_id=USER, state=JobState.READY, result_refs={"text": "ok"})
    assert done.state is JobState.READY and done.lease_owner is None and done.finished_at is not None


# --- deletion --------------------------------------------------------------- #


def test_tombstone_removes_access_immediately(store):
    with store.transaction(USER) as tx:
        tx.put("task", make_task())
    store.tombstone(USER, "task", "task_1")
    assert store.get(USER, "task", "task_1") is None
    assert store.list_tasks(USER).items == []


def test_writing_to_a_tombstoned_record_is_rejected(store):
    with store.transaction(USER) as tx:
        first = tx.put("task", make_task())
    store.tombstone(USER, "task", "task_1")
    with pytest.raises(MetisError) as exc, store.transaction(USER) as tx:
        tx.put("task", first.model_copy(update={"title": "x"}), expected_revision=1)
    assert exc.value.code is ErrorCode.TOMBSTONED


# --- preferences ------------------------------------------------------------ #


def test_default_preferences_are_editable_defaults_not_inferred_availability(store):
    prefs = store.get_preferences(USER)
    assert prefs.revision == 1 and prefs.max_focus_tasks == 3
    assert prefs.work_hours[0].start == "09:00"
