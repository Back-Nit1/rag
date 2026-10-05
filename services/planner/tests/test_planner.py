"""Planning invariants: real capacity, visible overflow, application validation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from metis_contracts import (
    AvailabilityBlock,
    ErrorCode,
    Estimate,
    Event,
    FixedClock,
    MetisError,
    PlanStatus,
    Task,
    TaskState,
    reset_settings_cache,
)
from metis_contracts.entities import TimeWindow
from metis_pipeline.inference import FixtureInferenceClient
from metis_planner import (
    Interval,
    PlannerService,
    assemble,
    pack,
    rank_candidates,
    validate,
)
from metis_retrieval import FixtureEmbedder, RetrievalService, SqliteMemoryStore
from metis_state.sqlite_store import SqliteStateStore

NY = "America/New_York"
USER = "user_a"
DAY = date(2026, 10, 6)
# 2026-10-06 12:00 UTC == 08:00 New York, before the 09:00 work-hours start.
T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture()
def store(tmp_path, clock, monkeypatch) -> SqliteStateStore:
    monkeypatch.setenv("METIS_DAILY_MICRO_USD", "1000000")
    reset_settings_cache()
    yield SqliteStateStore(str(tmp_path / "s.sqlite"), clock=clock)
    reset_settings_cache()


@pytest.fixture()
def retrieval(store, clock) -> RetrievalService:
    return RetrievalService(
        state_store=store,
        memory_store=SqliteMemoryStore(":memory:"),
        embedder=FixtureEmbedder(),
        clock=clock,
    )


@pytest.fixture()
def planner(store, retrieval, clock) -> PlannerService:
    return PlannerService(
        state_store=store, retrieval=retrieval, inference=FixtureInferenceClient(), clock=clock
    )


def add_task(store, clock, *, task_id, title="Task", minutes=None, **kw) -> Task:
    estimate = Estimate(minutes=minutes, provenance="user") if minutes else None
    task = Task(
        id=task_id,
        user_id=USER,
        created_at=clock.now(),
        updated_at=clock.now(),
        title=title,
        state=kw.pop("state", TaskState.READY),
        duration_estimate=estimate,
        timezone=NY,
        **kw,
    )
    with store.transaction(USER) as tx:
        return tx.put("task", task)


def add_event(store, clock, *, event_id, title, start_hour_utc, end_hour_utc=None) -> Event:
    event = Event(
        id=event_id,
        user_id=USER,
        created_at=clock.now(),
        updated_at=clock.now(),
        title=title,
        starts_at=datetime(2026, 10, 6, start_hour_utc, 0, tzinfo=UTC),
        ends_at=datetime(2026, 10, 6, end_hour_utc, 0, tzinfo=UTC) if end_hour_utc else None,
        local_date=DAY,
        timezone=NY,
        attendance_status="committed",
    )
    with store.transaction(USER) as tx:
        return tx.put("event", event)


def add_availability(store, clock, *, start, end, kind="available", block_id=None) -> AvailabilityBlock:
    block = AvailabilityBlock(
        id=block_id or f"avail_{start}_{kind}",
        user_id=USER,
        created_at=clock.now(),
        updated_at=clock.now(),
        local_date=DAY,
        timezone=NY,
        window=TimeWindow(start=start, end=end),
        kind=kind,
    )
    with store.transaction(USER) as tx:
        return tx.put("availability", block)


def snapshot_for(store, retrieval, clock):
    return assemble(
        state_store=store,
        retrieval=retrieval,
        clock=clock,
        user_id=USER,
        local_date=DAY,
        timezone_name=NY,
    )


# --- capacity and availability --------------------------------------------- #


def test_default_work_hours_are_flagged_as_an_assumption(store, retrieval, clock):
    snapshot = snapshot_for(store, retrieval, clock)
    assert snapshot.availability_configured is False
    assert any("work hours" in a for a in snapshot.assumptions)
    assert snapshot.capacity_minutes > 0


def test_entered_availability_replaces_the_default(store, retrieval, clock):
    add_availability(store, clock, start="10:00", end="12:00")
    snapshot = snapshot_for(store, retrieval, clock)
    assert snapshot.availability_configured is True
    assert snapshot.capacity_minutes == 120


def test_a_manual_busy_block_is_removed_from_capacity(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    add_availability(store, clock, start="12:00", end="13:00", kind="busy", block_id="busy_lunch")
    snapshot = snapshot_for(store, retrieval, clock)
    assert snapshot.capacity_minutes == 7 * 60


def test_a_fixed_event_is_carved_out_of_free_time(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    add_event(store, clock, event_id="ev_1", title="Standup", start_hour_utc=14, end_hour_utc=15)
    snapshot = snapshot_for(store, retrieval, clock)
    assert snapshot.capacity_minutes == 7 * 60
    assert all(
        not Interval(
            datetime(2026, 10, 6, 14, 0, tzinfo=UTC),
            datetime(2026, 10, 6, 15, 0, tzinfo=UTC),
        ).overlaps(i)
        for i in snapshot.free_intervals
    )


def test_an_event_without_an_end_uses_a_visible_default_estimate(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    add_event(store, clock, event_id="ev_1", title="Bloomberg event", start_hour_utc=14)
    snapshot = snapshot_for(store, retrieval, clock)
    assert any("60 minutes is a visible default estimate" in a for a in snapshot.assumptions)


def test_past_time_is_never_offered_as_capacity(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    clock.set(datetime(2026, 10, 6, 18, 0, tzinfo=UTC))  # 14:00 NY
    snapshot = snapshot_for(store, retrieval, clock)
    assert all(i.starts_at >= clock.now() for i in snapshot.free_intervals)
    assert snapshot.capacity_minutes == 3 * 60


# --- candidate selection ---------------------------------------------------- #


def test_overdue_and_near_deadlines_rank_first(store, clock):
    tasks = [
        Task(id="task_someday", user_id=USER, created_at=T0, updated_at=T0, title="Someday"),
        Task(
            id="task_overdue",
            user_id=USER,
            created_at=T0,
            updated_at=T0,
            title="Overdue",
            deadline_local_date=date(2026, 10, 1),
            timezone=NY,
        ),
        Task(
            id="task_soon",
            user_id=USER,
            created_at=T0,
            updated_at=T0,
            title="Soon",
            deadline_local_date=date(2026, 10, 7),
            timezone=NY,
        ),
    ]
    candidates, remainder = rank_candidates(tasks, local_date=DAY, timezone_name=NY, now=T0, limit=2)
    assert [t.id for t in candidates] == ["task_overdue", "task_soon"]
    assert [t.id for t in remainder] == ["task_someday"]


def test_a_blocked_task_is_deferred_not_dropped(store, clock):
    tasks = [
        Task(
            id="task_blocked",
            user_id=USER,
            created_at=T0,
            updated_at=T0,
            title="Blocked",
            depends_on=["task_prereq"],
        ),
        Task(id="task_free", user_id=USER, created_at=T0, updated_at=T0, title="Free"),
    ]
    candidates, remainder = rank_candidates(tasks, local_date=DAY, timezone_name=NY, now=T0, limit=5)
    assert "task_blocked" not in [t.id for t in candidates]
    assert "task_blocked" in [t.id for t in remainder]


def test_the_focus_limit_is_respected_and_the_rest_overflows(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    for i in range(6):
        add_task(store, clock, task_id=f"task_{i}", title=f"Task {i}", minutes=30)
    snapshot = snapshot_for(store, retrieval, clock)
    assert len(snapshot.candidate_tasks) == snapshot.preferences.max_focus_tasks
    assert len(snapshot.overflow_tasks) == 6 - snapshot.preferences.max_focus_tasks


# --- packing and overflow --------------------------------------------------- #


def test_work_that_does_not_fit_becomes_visible_overflow(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="10:00", block_id="avail_hour")
    add_task(store, clock, task_id="task_big", title="Big", minutes=55)
    add_task(store, clock, task_id="task_also_big", title="Also big", minutes=50)
    snapshot = snapshot_for(store, retrieval, clock)
    blocks, overflow = pack(snapshot)
    assert len(blocks) == 1
    assert overflow, "the plan must not shrink work to look complete"


def test_a_task_without_an_estimate_gets_a_labelled_default(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    add_task(store, clock, task_id="task_x", title="No estimate")
    snapshot = snapshot_for(store, retrieval, clock)
    blocks, _ = pack(snapshot)
    block = next(b for b in blocks if b.task_id == "task_x")
    assert block.estimate is not None and block.estimate.provenance == "default"


def test_fixed_appointments_appear_at_their_real_time(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    event = add_event(store, clock, event_id="ev_1", title="Standup", start_hour_utc=14, end_hour_utc=15)
    snapshot = snapshot_for(store, retrieval, clock)
    blocks, _ = pack(snapshot)
    fixed = next(b for b in blocks if b.event_id == "ev_1")
    assert fixed.fixed is True and fixed.starts_at == event.starts_at


def test_rationales_are_grounded_in_deadline_or_priority(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    add_task(
        store, clock, task_id="task_due", title="Due soon", deadline_local_date=date(2026, 10, 7), minutes=30
    )
    snapshot = snapshot_for(store, retrieval, clock)
    blocks, _ = pack(snapshot)
    block = next(b for b in blocks if b.task_id == "task_due")
    assert block.rationale and "due in 1 day" in block.rationale


# --- deterministic validation ----------------------------------------------- #


def test_the_validator_rejects_overlapping_blocks(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    add_task(store, clock, task_id="task_a", title="A", minutes=60)
    add_task(store, clock, task_id="task_b", title="B", minutes=60)
    snapshot = snapshot_for(store, retrieval, clock)
    blocks, _ = pack(snapshot)
    clashing = [blocks[0], blocks[1].model_copy(update={"starts_at": blocks[0].starts_at})]
    result = validate(clashing, snapshot, overflow_ids=[])
    assert result.valid is False
    assert any(i.code == "blocks_overlap" for i in result.issues)


def test_the_validator_rejects_work_outside_availability(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="10:00", block_id="avail_hour")
    add_task(store, clock, task_id="task_a", title="A", minutes=30)
    snapshot = snapshot_for(store, retrieval, clock)
    blocks, _ = pack(snapshot)
    moved = [
        blocks[0].model_copy(
            update={
                "starts_at": blocks[0].starts_at + timedelta(hours=8),
                "ends_at": blocks[0].ends_at + timedelta(hours=8),
            }
        )
    ]
    result = validate(moved, snapshot, overflow_ids=[])
    assert any(i.code == "outside_availability" for i in result.issues)


def test_the_validator_rejects_moving_a_fixed_appointment(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    add_event(store, clock, event_id="ev_1", title="Standup", start_hour_utc=14, end_hour_utc=15)
    snapshot = snapshot_for(store, retrieval, clock)
    blocks, _ = pack(snapshot)
    fixed_index = next(i for i, b in enumerate(blocks) if b.event_id == "ev_1")
    blocks[fixed_index] = blocks[fixed_index].model_copy(
        update={
            "starts_at": blocks[fixed_index].starts_at + timedelta(hours=1),
            "ends_at": blocks[fixed_index].ends_at + timedelta(hours=1),
        }
    )
    result = validate(blocks, snapshot, overflow_ids=[])
    assert any(i.code == "fixed_block_moved" for i in result.issues)


def test_the_validator_enforces_dependency_order(store, retrieval, clock):
    from metis_contracts import PlanBlock

    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    prereq = add_task(store, clock, task_id="task_prereq", title="Prereq", minutes=30)
    after = add_task(
        store, clock, task_id="task_after", title="After", minutes=30, depends_on=["task_prereq"]
    )
    snapshot = snapshot_for(store, retrieval, clock)
    # rank_candidates defers a blocked task, so the dependent pair is placed by
    # hand here: this test is about the validator, not the packer.
    snapshot.candidate_tasks = [prereq, after]
    first = snapshot.free_intervals[0].starts_at
    swapped = [
        PlanBlock(
            task_id="task_after", starts_at=first, ends_at=first + timedelta(minutes=30), title="After"
        ),
        PlanBlock(
            task_id="task_prereq",
            starts_at=first + timedelta(minutes=30),
            ends_at=first + timedelta(minutes=60),
            title="Prereq",
        ),
    ]
    result = validate(swapped, snapshot, overflow_ids=[])
    assert any(i.code == "dependency_order" for i in result.issues)


def test_the_validator_flags_a_dependency_that_is_neither_done_nor_scheduled(store, retrieval, clock):
    from metis_contracts import PlanBlock

    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    prereq = add_task(store, clock, task_id="task_prereq", title="Prereq", minutes=30)
    after = add_task(
        store, clock, task_id="task_after", title="After", minutes=30, depends_on=["task_prereq"]
    )
    snapshot = snapshot_for(store, retrieval, clock)
    snapshot.candidate_tasks = [prereq, after]
    first = snapshot.free_intervals[0].starts_at
    lone = [
        PlanBlock(task_id="task_after", starts_at=first, ends_at=first + timedelta(minutes=30), title="After")
    ]
    result = validate(lone, snapshot, overflow_ids=[])
    assert any(i.code == "unmet_dependency" for i in result.issues)


def test_the_validator_rejects_a_done_task_in_a_plan(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    task = add_task(store, clock, task_id="task_a", title="A", minutes=30)
    snapshot = snapshot_for(store, retrieval, clock)
    blocks, _ = pack(snapshot)
    snapshot.candidate_tasks = [
        task.model_copy(update={"state": TaskState.DONE, "completed_at": clock.now()})
    ]
    result = validate(blocks, snapshot, overflow_ids=[])
    assert any(i.code == "task_not_active" for i in result.issues)


def test_the_validator_rejects_scheduling_after_a_deadline(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    add_task(
        store, clock, task_id="task_late", title="Late", minutes=30, deadline_local_date=date(2026, 10, 5)
    )
    snapshot = snapshot_for(store, retrieval, clock)
    blocks, _ = pack(snapshot)
    result = validate(blocks, snapshot, overflow_ids=[])
    assert any(i.code == "after_deadline" for i in result.issues)


def test_validation_is_always_attributed_to_the_application(store, retrieval, clock, planner):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    add_task(store, clock, task_id="task_a", title="A", minutes=30)
    snapshot = snapshot_for(store, retrieval, clock)
    plan = planner.build_plan(snapshot)
    assert plan.validation is not None
    assert plan.validation.checked_by == "application"


def test_overflow_and_assumed_availability_are_reported_as_warnings(store, retrieval, clock):
    add_task(store, clock, task_id="task_a", title="A", minutes=30)
    snapshot = snapshot_for(store, retrieval, clock)
    blocks, _overflow = pack(snapshot)
    result = validate(blocks, snapshot, overflow_ids=["task_z"])
    codes = {i.code for i in result.issues}
    assert "overflow_present" in codes and "availability_assumed" in codes
    assert result.valid is True, "warnings must not invalidate an otherwise sound plan"


# --- epoch and degradation --------------------------------------------------- #


def test_an_input_change_during_assembly_is_detected(store, retrieval, clock, monkeypatch):
    monkeypatch.setenv("METIS_PLAN_EPOCH_RETRIES", "1")
    reset_settings_cache()
    add_task(store, clock, task_id="task_a", title="A", minutes=30)

    original = retrieval.list_eligible_obligations

    def meddling(user_id, **kw):
        result = original(user_id, **kw)
        add_task(store, clock, task_id=f"task_new_{clock.now().timestamp()}", title="Snuck in")
        return result

    retrieval.list_eligible_obligations = meddling  # type: ignore[assignment]
    with pytest.raises(MetisError) as exc:
        snapshot_for(store, retrieval, clock)
    assert exc.value.code is ErrorCode.EPOCH_CONFLICT
    reset_settings_cache()


def test_the_plan_records_the_epoch_and_the_input_revisions(store, retrieval, clock, planner):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    task = add_task(store, clock, task_id="task_a", title="A", minutes=30)
    snapshot = snapshot_for(store, retrieval, clock)
    plan = planner.build_plan(snapshot)
    assert plan.planning_epoch == store.get_planning_epoch(USER)
    assert plan.input_revisions[task.id] == task.revision


def test_a_plan_is_still_produced_with_no_inference_client(store, retrieval, clock):
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    add_task(store, clock, task_id="task_a", title="A", minutes=30)
    offline = PlannerService(state_store=store, retrieval=retrieval, inference=None, clock=clock)
    plan = offline.build_plan(snapshot_for(store, retrieval, clock))
    assert plan.blocks and plan.validation and plan.validation.valid
    assert plan.model_id is None


def test_an_exhausted_budget_falls_back_to_the_deterministic_outline(store, retrieval, clock, monkeypatch):
    monkeypatch.setenv("METIS_DAILY_MICRO_USD", "1")
    reset_settings_cache()
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    add_task(store, clock, task_id="task_a", title="A", minutes=30)
    store.reserve_budget(USER, local_date=DAY, micro_usd=1, operation="other", correlation_id="c0")
    planner = PlannerService(
        state_store=store, retrieval=retrieval, inference=FixtureInferenceClient(), clock=clock
    )
    plan = planner.build_plan(snapshot_for(store, retrieval, clock))
    assert plan.blocks, "the outline must still be usable at the cap"
    assert plan.status is PlanStatus.FALLBACK
    assert any("budget" in a.lower() for a in plan.assumptions)
    reset_settings_cache()


def test_an_incomplete_scan_is_surfaced_on_the_plan(store, retrieval, clock, planner, monkeypatch):
    monkeypatch.setenv("METIS_PLAN_READ_PAGES", "1")
    reset_settings_cache()
    add_availability(store, clock, start="09:00", end="17:00", block_id="avail_day")
    for i in range(250):
        add_task(store, clock, task_id=f"task_{i:03d}", title=f"T{i}", minutes=15)
    plan = planner.build_plan(snapshot_for(store, retrieval, clock))
    assert plan.validation is not None and plan.validation.inputs_complete is False
    assert any("complete coverage" in a for a in plan.assumptions)
    reset_settings_cache()
