"""Reminder reliability: honest states, suppression and no duplicate delivery."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from metis_contracts import (
    AttendanceStatus,
    DeliveryStatus,
    ErrorCode,
    Event,
    FixedClock,
    MetisError,
    RegistrationStatus,
    ReminderState,
    Task,
    TaskState,
    reset_settings_cache,
)
from metis_contracts.entities import TimeWindow
from metis_pipeline.dispatcher import DispatchContext, OutboxDispatcher
from metis_scheduler import (
    FixtureTransport,
    InAppTransport,
    LocalScheduleStore,
    cancel_reminder,
    evaluate_due,
    in_quiet_hours,
    next_allowed_time,
    raise_due_event,
    request_reminder,
    retarget_reminder,
    snooze_reminder,
    suspend_reminder,
)
from metis_scheduler.handlers import registry as scheduler_registry
from metis_state.sqlite_store import SqliteStateStore

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # 10:00 in New York
NY = "America/New_York"
USER = "user_a"
DUE = datetime(2026, 10, 7, 13, 0, tzinfo=UTC)  # 09:00 NY


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture()
def store(tmp_path, clock, monkeypatch) -> SqliteStateStore:
    monkeypatch.setenv("METIS_JOB_MAX_ATTEMPTS", "3")
    reset_settings_cache()
    yield SqliteStateStore(str(tmp_path / "s.sqlite"), clock=clock)
    reset_settings_cache()


class _FlakyScheduleStore(LocalScheduleStore):
    def __init__(self, fail_times: int = 1) -> None:
        super().__init__()
        self._remaining = fail_times

    def upsert_schedule(self, **kw):
        if self._remaining > 0:
            self._remaining -= 1
            raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "scheduler is unavailable")
        return super().upsert_schedule(**kw)


@pytest.fixture()
def context(store, clock):
    transport = FixtureTransport()
    return DispatchContext(
        state_store=store,
        source_store=None,
        clock=clock,
        schedule_store=LocalScheduleStore(state_store=store, clock=clock),
        notification_transports={"in_app": transport, "fixture": transport},
        worker_id="w1",
    )


def make_task(store, clock, *, task_id="task_register", state=TaskState.READY, **kw) -> Task:
    task = Task(
        id=task_id,
        user_id=USER,
        created_at=clock.now(),
        updated_at=clock.now(),
        title="Register for the Bloomberg event",
        state=state,
        **kw,
    )
    with store.transaction(USER) as tx:
        return tx.put("task", task)


def make_event(store, clock, **kw) -> Event:
    event = Event(
        id=kw.pop("event_id", "event_bloomberg"),
        user_id=USER,
        created_at=clock.now(),
        updated_at=clock.now(),
        title="Bloomberg event",
        starts_at=datetime(2026, 10, 8, 22, 0, tzinfo=UTC),
        local_date=date(2026, 10, 8),
        timezone=NY,
        **kw,
    )
    with store.transaction(USER) as tx:
        return tx.put("event", event)


def add_reminder(store, clock, target, *, kind="task", trigger_at=DUE, channel="in_app"):
    from metis_contracts import DeliveryChannel

    return request_reminder(
        store,
        user_id=USER,
        target_id=target.id,
        target_kind=kind,
        target_revision=target.revision,
        trigger_at=trigger_at,
        timezone_name=NY,
        channel=DeliveryChannel(channel),
        clock=clock,
        correlation_id="corr_1",
    )


def drain(context, *, cycles=4):
    dispatcher = OutboxDispatcher(scheduler_registry, context)
    results = [dispatcher.run_once() for _ in range(cycles)]
    return results


# --- honest scheduling states ---------------------------------------------- #


def test_a_new_reminder_is_pending_schedule_not_scheduled(store, clock):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    assert reminder.state is ReminderState.PENDING_SCHEDULE
    assert reminder.scheduler_ref is None, "nothing may claim a schedule exists yet"


def test_a_successful_sync_moves_it_to_scheduled(store, clock, context):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    drain(context)
    after = store.get(USER, "reminder", reminder.id)
    assert after.state is ReminderState.SCHEDULED
    assert after.scheduler_ref


def test_a_scheduler_outage_leaves_the_data_saved_and_the_state_honest(store, clock):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    transport = FixtureTransport()
    broken = DispatchContext(
        state_store=store,
        source_store=None,
        clock=clock,
        schedule_store=_FlakyScheduleStore(fail_times=99),
        notification_transports={"in_app": transport},
        worker_id="w1",
    )
    OutboxDispatcher(scheduler_registry, broken).run_once()
    after = store.get(USER, "reminder", reminder.id)
    assert after.state is ReminderState.SCHEDULE_FAILED
    assert store.get(USER, "task", task.id) is not None, "the task itself stays saved"


def test_a_retry_after_an_outage_recovers_without_duplicating(store, clock):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    flaky = _FlakyScheduleStore(fail_times=1)
    transport = FixtureTransport()
    ctx = DispatchContext(
        state_store=store,
        source_store=None,
        clock=clock,
        schedule_store=flaky,
        notification_transports={"in_app": transport},
        worker_id="w1",
    )
    dispatcher = OutboxDispatcher(scheduler_registry, ctx)
    dispatcher.run_once()
    clock.advance(seconds=600)
    dispatcher.run_once()
    after = store.get(USER, "reminder", reminder.id)
    assert after.state is ReminderState.SCHEDULED
    assert len(flaky.created) == 1, "one schedule, not one per attempt"


# --- date changes and cancellation ----------------------------------------- #


def test_moving_the_date_supersedes_the_old_occurrence(store, clock, context):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    drain(context)
    scheduled = store.get(USER, "reminder", reminder.id)
    moved = retarget_reminder(
        store,
        scheduled,
        trigger_at=DUE + timedelta(days=1),
        target_revision=task.revision,
        clock=clock,
        correlation_id="corr_2",
    )
    assert moved.occurrence_key != scheduled.occurrence_key
    assert moved.state is ReminderState.PENDING_SCHEDULE


def test_an_old_schedule_firing_after_a_date_change_is_suppressed(store, clock, context):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    drain(context)
    old_occurrence = store.get(USER, "reminder", reminder.id).occurrence_key
    retarget_reminder(
        store,
        store.get(USER, "reminder", reminder.id),
        trigger_at=DUE + timedelta(days=1),
        target_revision=task.revision,
        clock=clock,
        correlation_id="corr_2",
    )
    clock.set(DUE)
    decision = evaluate_due(
        store, store.get(USER, "reminder", reminder.id), occurrence=old_occurrence, clock=clock
    )
    assert decision.deliver is False
    assert "supersedes" in decision.reason


def test_cancelling_before_dispatch_suppresses_delivery(store, clock, context):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    drain(context)
    canceled = cancel_reminder(
        store, store.get(USER, "reminder", reminder.id), clock=clock, correlation_id="corr_3"
    )
    assert canceled.state is ReminderState.CANCELED
    clock.set(DUE)
    assert raise_due_event(store, user_id=USER, reminder_id=reminder.id, clock=clock) is False
    drain(context)
    assert context.notification_transports["in_app"].sent == []


def test_a_completed_task_suppresses_its_reminder(store, clock, context):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    drain(context)
    with store.transaction(USER) as tx:
        tx.put(
            "task",
            task.model_copy(update={"state": TaskState.DONE, "completed_at": clock.now()}),
            expected_revision=task.revision,
        )
    clock.set(DUE)
    current = store.get(USER, "reminder", reminder.id)
    decision = evaluate_due(store, current, occurrence=current.occurrence_key, clock=clock)
    assert decision.deliver is False and "done" in decision.reason


def test_a_canceled_event_suppresses_its_reminder(store, clock, context):
    event = make_event(store, clock)
    reminder = add_reminder(store, clock, event, kind="event")
    drain(context)
    with store.transaction(USER) as tx:
        tx.put(
            "event",
            event.model_copy(
                update={"attendance_status": AttendanceStatus.CANCELED, "canceled_at": clock.now()}
            ),
            expected_revision=event.revision,
        )
    clock.set(DUE)
    current = store.get(USER, "reminder", reminder.id)
    decision = evaluate_due(store, current, occurrence=current.occurrence_key, clock=clock)
    assert decision.deliver is False and "canceled" in decision.reason


def test_completed_registration_suppresses_the_registration_reminder(store, clock, context):
    event = make_event(store, clock, event_id="event_0a1b2c3d")
    reminder = add_reminder(store, clock, event, kind="event")
    with store.transaction(USER) as tx:
        tx.put(
            "event",
            event.model_copy(update={"registration_status": RegistrationStatus.REGISTERED}),
            expected_revision=event.revision,
        )
    clock.set(DUE)
    current = store.get(USER, "reminder", reminder.id)
    decision = evaluate_due(store, current, occurrence=current.occurrence_key, clock=clock)
    assert decision.deliver is False and "registration" in decision.reason


def test_a_deleted_target_suppresses_the_reminder(store, clock, context):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    store.tombstone(USER, "task", task.id)
    clock.set(DUE)
    current = store.get(USER, "reminder", reminder.id)
    decision = evaluate_due(store, current, occurrence=current.occurrence_key, clock=clock)
    assert decision.deliver is False and "deleted" in decision.reason


def test_a_suspended_reminder_waits_for_clarification(store, clock):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    suspended = suspend_reminder(
        store, reminder, reason="the deadline is disputed and awaiting clarification", clock=clock
    )
    clock.set(DUE)
    decision = evaluate_due(store, suspended, occurrence=suspended.occurrence_key, clock=clock)
    assert decision.deliver is False and "suspended" in decision.reason


def test_a_future_trigger_is_not_due_yet(store, clock):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    decision = evaluate_due(store, reminder, occurrence=reminder.occurrence_key, clock=clock)
    assert decision.deliver is False and "not arrived" in decision.reason


# --- delivery -------------------------------------------------------------- #


def test_a_due_reminder_delivers_once_and_only_once(store, clock, context):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    drain(context)
    clock.set(DUE)
    assert raise_due_event(store, user_id=USER, reminder_id=reminder.id, clock=clock) is True
    drain(context)

    notifications = store.list_notifications(USER).items
    assert len(notifications) == 1
    assert notifications[0].delivery_status is DeliveryStatus.ACCEPTED
    assert notifications[0].dedupe_key == store.get(USER, "reminder", reminder.id).occurrence_key
    assert store.get(USER, "reminder", reminder.id).state is ReminderState.DELIVERED


def test_a_duplicate_due_event_does_not_create_a_second_notification(store, clock, context):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    drain(context)
    clock.set(DUE)
    raise_due_event(store, user_id=USER, reminder_id=reminder.id, clock=clock)
    drain(context)
    # A racing worker replays the same occurrence.
    raise_due_event(store, user_id=USER, reminder_id=reminder.id, clock=clock)
    drain(context)
    assert len(store.list_notifications(USER).items) == 1


def test_an_unsupported_channel_still_leaves_the_in_app_record(store, clock):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task, channel="web_push")

    class _Unsupported:
        channel = "web_push"
        mode = "cloud"

        def supported(self, user_id):
            return False

        def send(self, notification):
            raise AssertionError("send must not be called when unsupported")

    ctx = DispatchContext(
        state_store=store,
        source_store=None,
        clock=clock,
        schedule_store=LocalScheduleStore(state_store=store, clock=clock),
        notification_transports={"web_push": _Unsupported(), "in_app": InAppTransport(store, clock)},
        worker_id="w1",
    )
    drain(ctx)
    clock.set(DUE)
    raise_due_event(store, user_id=USER, reminder_id=reminder.id, clock=clock)
    drain(ctx)
    notifications = store.list_notifications(USER).items
    assert len(notifications) == 1
    assert notifications[0].delivery_status is DeliveryStatus.UNSUPPORTED
    assert notifications[0].delivery_detail


def test_an_ordinary_reminder_makes_no_model_calls(store, clock, context):
    task = make_task(store, clock, deadline_local_date=date(2026, 10, 12), timezone=NY)
    reminder = add_reminder(store, clock, task)
    drain(context)
    clock.set(DUE)
    raise_due_event(store, user_id=USER, reminder_id=reminder.id, clock=clock)
    drain(context)
    usage = store.get_usage(USER, clock.today_local(NY))
    assert usage.model_calls == 0, "a templated reminder must not touch a model"
    body = store.list_notifications(USER).items[0].body
    assert "2026-10-12" in body
    assert store.list_notifications(USER).items[0].generated is False


# --- quiet hours ------------------------------------------------------------ #


def test_quiet_hours_wrap_past_midnight():
    windows = [TimeWindow(start="22:00", end="08:00")]
    assert in_quiet_hours(datetime(2026, 10, 6, 3, 0, tzinfo=UTC), timezone_name="UTC", windows=windows)
    assert in_quiet_hours(datetime(2026, 10, 6, 23, 0, tzinfo=UTC), timezone_name="UTC", windows=windows)
    assert not in_quiet_hours(datetime(2026, 10, 6, 12, 0, tzinfo=UTC), timezone_name="UTC", windows=windows)


def test_a_quiet_hours_reminder_is_deferred_not_dropped(store, clock, context):
    task = make_task(store, clock)
    quiet_trigger = datetime(2026, 10, 7, 5, 0, tzinfo=UTC)  # 01:00 NY
    reminder = add_reminder(store, clock, task, trigger_at=quiet_trigger)
    drain(context)
    clock.set(quiet_trigger)
    current = store.get(USER, "reminder", reminder.id)
    decision = evaluate_due(store, current, occurrence=current.occurrence_key, clock=clock)
    assert decision.deliver is False
    assert decision.defer_until is not None and decision.defer_until > clock.now()


def test_next_allowed_time_lands_at_the_end_of_the_window():
    windows = [TimeWindow(start="22:00", end="08:00")]
    moved = next_allowed_time(datetime(2026, 10, 7, 5, 0, tzinfo=UTC), timezone_name="UTC", windows=windows)
    assert moved.hour == 8


# --- snooze ---------------------------------------------------------------- #


def test_snoozing_creates_a_new_occurrence(store, clock, context):
    task = make_task(store, clock)
    reminder = add_reminder(store, clock, task)
    drain(context)
    clock.set(DUE)
    raise_due_event(store, user_id=USER, reminder_id=reminder.id, clock=clock)
    drain(context)
    delivered = store.get(USER, "reminder", reminder.id)
    snoozed = snooze_reminder(store, delivered, minutes=30, clock=clock, correlation_id="corr_s")
    assert snoozed.state is ReminderState.SNOOZED
    assert snoozed.occurrence_key != delivered.occurrence_key
    assert snoozed.trigger_at == clock.now() + timedelta(minutes=30)


def test_an_invalid_transition_is_rejected(store, clock):
    from metis_scheduler import assert_transition

    with pytest.raises(MetisError) as exc:
        assert_transition(ReminderState.CANCELED, ReminderState.SCHEDULED)
    assert exc.value.code is ErrorCode.INVALID_STATE_TRANSITION
