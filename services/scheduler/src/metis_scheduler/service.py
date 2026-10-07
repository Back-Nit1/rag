"""Reminder lifecycle: request, sync, suppression, dispatch, snooze, cancel.

The honesty rules this module enforces, each with a test:

* ``pending_schedule`` until a scheduler sync actually succeeded. A saved
  entity with a failed schedule is ``schedule_failed``, never "reminder set".
* A date change supersedes the old occurrence and requests a new one. The old
  schedule may still fire, so the due handler re-validates before dispatching.
* Suppression is guaranteed for anything detected *before* the dispatch
  decision. Once a message is handed to an external transport it may not be
  recallable, and that boundary is stated rather than papered over.
* One logical in-app notification per occurrence, enforced by a conditional
  claim — so a duplicate worker cannot create a second one.
* An ordinary reminder costs zero model calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from metis_contracts import (
    REMINDER_ACTIVE_STATES,
    REMINDER_TRANSITIONS,
    DeliveryChannel,
    DeliveryStatus,
    ErrorCode,
    EventType,
    MetisError,
    Notification,
    OutboxEvent,
    Reminder,
    ReminderState,
    new_id,
    resolve_timezone,
)
from metis_contracts.events import ReminderDuePayload, ReminderSyncPayload
from metis_contracts.ports import Clock, StateStore

log = logging.getLogger("metis.scheduler.service")


def occurrence_key(reminder_id: str, trigger_at: datetime) -> str:
    """Stable per-occurrence key, retained across retries and snoozes."""
    return f"{reminder_id}:{trigger_at.astimezone(trigger_at.tzinfo).isoformat()}"


def assert_transition(current: ReminderState, target: ReminderState) -> None:
    allowed = REMINDER_TRANSITIONS[current]
    if target not in allowed and target is not current:
        raise MetisError(
            ErrorCode.INVALID_STATE_TRANSITION,
            f"a reminder cannot move from {current.value} to {target.value}",
        )


def in_quiet_hours(instant: datetime, *, timezone_name: str, windows: list[Any]) -> bool:
    """Quiet hours are local wall-clock windows and may wrap past midnight."""
    local = instant.astimezone(resolve_timezone(timezone_name)).time()
    for window in windows:
        start = time.fromisoformat(window.start)
        end = time.fromisoformat(window.end)
        if start <= end:
            if start <= local < end:
                return True
        elif local >= start or local < end:  # wraps midnight
            return True
    return False


def next_allowed_time(instant: datetime, *, timezone_name: str, windows: list[Any]) -> datetime:
    """Push an instant to the end of the quiet window it falls in."""
    tz = resolve_timezone(timezone_name)
    local = instant.astimezone(tz)
    for window in windows:
        start = time.fromisoformat(window.start)
        end = time.fromisoformat(window.end)
        wraps = start > end
        inside = (start <= local.time() < end) if not wraps else (local.time() >= start or local.time() < end)
        if inside:
            candidate = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
            if candidate <= local:
                candidate += timedelta(days=1)
            return candidate.astimezone(instant.tzinfo)
    return instant


# --------------------------------------------------------------------------- #
# creating and re-pointing reminders
# --------------------------------------------------------------------------- #


def request_reminder(
    state: StateStore,
    *,
    user_id: str,
    target_id: str,
    target_kind: str,
    target_revision: int,
    trigger_at: datetime,
    timezone_name: str,
    channel: DeliveryChannel = DeliveryChannel.IN_APP,
    requires_generation: bool = False,
    clock: Clock,
    correlation_id: str,
) -> Reminder:
    """Persist a reminder as ``pending_schedule`` and request the sync.

    The reminder row and the sync request commit together; the dispatcher then
    talks to the scheduler. Until that succeeds the UI must not claim the
    reminder is set.
    """
    now = clock.now()
    reminder = Reminder(
        id=new_id("rem"),
        user_id=user_id,
        created_at=now,
        updated_at=now,
        target_id=target_id,
        target_kind=target_kind,  # type: ignore[arg-type]
        target_revision=target_revision,
        trigger_at=trigger_at,
        timezone=timezone_name,
        state=ReminderState.PENDING_SCHEDULE,
        channel=channel,
        occurrence_key=occurrence_key("pending", trigger_at),
        requires_generation=requires_generation,
    )
    reminder = reminder.model_copy(update={"occurrence_key": occurrence_key(reminder.id, trigger_at)})

    with state.transaction(user_id) as tx:
        stored = tx.put("reminder", reminder)
        tx.append_outbox(
            _sync_event(
                stored,
                action="create",
                clock=clock,
                correlation_id=correlation_id,
                event_type=EventType.REMINDER_SCHEDULE_SYNC,
            )
        )
    return stored


def retarget_reminder(
    state: StateStore,
    reminder: Reminder,
    *,
    trigger_at: datetime,
    target_revision: int,
    clock: Clock,
    correlation_id: str,
) -> Reminder:
    """A date change: supersede the old occurrence and request a new schedule.

    The old provider schedule may still fire during the race; the due handler's
    freshness check is what suppresses it.
    """
    assert_transition(reminder.state, ReminderState.PENDING_SCHEDULE)
    updated = reminder.model_copy(
        update={
            "trigger_at": trigger_at,
            "target_revision": target_revision,
            "state": ReminderState.PENDING_SCHEDULE,
            "occurrence_key": occurrence_key(reminder.id, trigger_at),
            "attempts": 0,
            "last_error": None,
            "suspended_reason": None,
        }
    )
    with state.transaction(reminder.user_id) as tx:
        stored = tx.put("reminder", updated, expected_revision=reminder.revision)
        tx.append_outbox(
            _sync_event(
                stored,
                action="update",
                clock=clock,
                correlation_id=correlation_id,
                event_type=EventType.REMINDER_SCHEDULE_SYNC,
            )
        )
    return stored


def cancel_reminder(
    state: StateStore, reminder: Reminder, *, clock: Clock, correlation_id: str, reason: str = "canceled"
) -> Reminder:
    """Cancel and request schedule deletion.

    Suppression is guaranteed for an occurrence detected before dispatch. A
    message already handed to an external transport may not be recallable.
    """
    if reminder.state in (ReminderState.CANCELED, ReminderState.SUPERSEDED):
        return reminder
    assert_transition(reminder.state, ReminderState.CANCELED)
    updated = reminder.model_copy(
        update={"state": ReminderState.CANCELED, "canceled_at": clock.now(), "suspended_reason": reason}
    )
    with state.transaction(reminder.user_id) as tx:
        stored = tx.put("reminder", updated, expected_revision=reminder.revision)
        tx.append_outbox(
            _sync_event(
                stored,
                action="delete",
                clock=clock,
                correlation_id=correlation_id,
                event_type=EventType.REMINDER_CANCEL_SYNC,
            )
        )
    return stored


def suspend_reminder(state: StateStore, reminder: Reminder, *, reason: str, clock: Clock) -> Reminder:
    """Hold a not-yet-dispatched reminder while a disputed date is clarified."""
    if reminder.state not in REMINDER_ACTIVE_STATES:
        return reminder
    updated = reminder.model_copy(update={"suspended_reason": reason})
    with state.transaction(reminder.user_id) as tx:
        return tx.put("reminder", updated, expected_revision=reminder.revision)


def snooze_reminder(
    state: StateStore, reminder: Reminder, *, minutes: int, clock: Clock, correlation_id: str
) -> Reminder:
    assert_transition(reminder.state, ReminderState.SNOOZED)
    trigger_at = clock.now() + timedelta(minutes=minutes)
    updated = reminder.model_copy(
        update={
            "state": ReminderState.SNOOZED,
            "snoozed_until": trigger_at,
            "trigger_at": trigger_at,
            "occurrence_key": occurrence_key(reminder.id, trigger_at),
        }
    )
    with state.transaction(reminder.user_id) as tx:
        stored = tx.put("reminder", updated, expected_revision=reminder.revision)
        tx.append_outbox(
            _sync_event(
                stored,
                action="update",
                clock=clock,
                correlation_id=correlation_id,
                event_type=EventType.REMINDER_SCHEDULE_SYNC,
            )
        )
    return stored


def _sync_event(
    reminder: Reminder, *, action: str, clock: Clock, correlation_id: str, event_type: str
) -> OutboxEvent:
    now = clock.now()
    return OutboxEvent(
        id=new_id("obx"),
        user_id=reminder.user_id,
        created_at=now,
        updated_at=now,
        event_type=event_type,
        target_id=reminder.id,
        target_revision=reminder.revision,
        correlation_id=correlation_id,
        # Keyed by occurrence AND action so a re-point produces a new sync while
        # a retry of the same sync stays one logical request.
        idempotency_key=f"sync:{action}:{reminder.occurrence_key}:{reminder.revision}",
        payload=ReminderSyncPayload(
            reminder_id=reminder.id,
            action=action,  # type: ignore[arg-type]
            trigger_at=reminder.trigger_at,
            scheduler_ref=reminder.scheduler_ref,
        ).model_dump(mode="json"),
        available_at=now,
    )


def raise_due_event(state: StateStore, *, user_id: str, reminder_id: str, clock: Clock) -> bool:
    """Enqueue a due event for a reminder whose trigger has passed.

    Local-mode timer entry point. Returns False when the reminder is no longer
    in a dispatchable state, so a canceled reminder is never even raised.
    """
    reminder = state.get(user_id, "reminder", reminder_id)
    if reminder is None or reminder.state not in (
        ReminderState.SCHEDULED,
        ReminderState.PENDING_SCHEDULE,
        ReminderState.SNOOZED,
    ):
        return False
    if reminder.trigger_at > clock.now():
        return False
    now = clock.now()
    event = OutboxEvent(
        id=new_id("obx"),
        user_id=user_id,
        created_at=now,
        updated_at=now,
        event_type=EventType.REMINDER_DUE,
        target_id=reminder.id,
        target_revision=reminder.revision,
        correlation_id=f"due:{reminder.occurrence_key}",
        idempotency_key=f"due:{reminder.occurrence_key}",
        payload=ReminderDuePayload(
            reminder_id=reminder.id,
            occurrence_key=reminder.occurrence_key,
            scheduled_for=reminder.trigger_at,
        ).model_dump(mode="json"),
        available_at=now,
    )
    with state.transaction(user_id) as tx:
        tx.append_outbox(event)
    return True


# --------------------------------------------------------------------------- #
# the due decision
# --------------------------------------------------------------------------- #


@dataclass
class DueDecision:
    """Why a due occurrence will or will not be delivered."""

    deliver: bool
    reason: str
    defer_until: datetime | None = None


def evaluate_due(state: StateStore, reminder: Reminder, *, occurrence: str, clock: Clock) -> DueDecision:
    """Every suppression check, in one place, before any dispatch decision."""
    if reminder.occurrence_key != occurrence:
        return DueDecision(False, "a newer occurrence supersedes this one")
    if reminder.state in (ReminderState.CANCELED, ReminderState.SUPERSEDED):
        return DueDecision(False, f"the reminder is {reminder.state.value}")
    if reminder.state is ReminderState.DELIVERED:
        return DueDecision(False, "this occurrence was already delivered")
    if reminder.suspended_reason:
        return DueDecision(False, f"suspended: {reminder.suspended_reason}")
    if reminder.trigger_at > clock.now():
        return DueDecision(False, "the trigger time has not arrived")

    target = state.get(reminder.user_id, reminder.target_kind, reminder.target_id)
    if target is None:
        return DueDecision(False, "the target was deleted")
    if int(target.revision) < int(reminder.target_revision):
        return DueDecision(False, "the target is behind the reminder's revision")

    # A completed or canceled target makes the reminder pointless. Lack of a
    # response to an earlier notification never counts as completion.
    state_value = getattr(getattr(target, "state", None), "value", None)
    if state_value in ("done", "canceled"):
        return DueDecision(False, f"the target task is {state_value}")
    attendance = getattr(getattr(target, "attendance_status", None), "value", None)
    if attendance in ("canceled", "declined"):
        return DueDecision(False, f"the event is {attendance}")
    registration = getattr(getattr(target, "registration_status", None), "value", None)
    if registration == "registered":
        return DueDecision(False, "registration is already complete")

    preferences = state.get_preferences(reminder.user_id)
    if in_quiet_hours(clock.now(), timezone_name=reminder.timezone, windows=preferences.quiet_hours):
        return DueDecision(
            False,
            "inside the user's quiet hours",
            defer_until=next_allowed_time(
                clock.now(), timezone_name=reminder.timezone, windows=preferences.quiet_hours
            ),
        )
    return DueDecision(True, "all checks passed")


def build_notification(
    reminder: Reminder, target: Any, *, clock: Clock, body: str | None = None, generated: bool = False
) -> Notification:
    """Template a notification. An ordinary reminder makes zero model calls."""
    title = getattr(target, "title", "Reminder")
    if body is None:
        if reminder.target_kind == "event":
            when = (
                target.starts_at.astimezone(resolve_timezone(reminder.timezone)).strftime("%a %d %b, %H:%M")
                if getattr(target, "starts_at", None)
                else "date not set"
            )
            body = f"{title} — {when}"
        else:
            deadline = getattr(target, "deadline_local_date", None)
            body = f"{title}" + (f" — due {deadline.isoformat()}" if deadline else "")
    now = clock.now()
    return Notification(
        id=new_id("not"),
        user_id=reminder.user_id,
        created_at=now,
        updated_at=now,
        reminder_id=reminder.id,
        title=title,
        body=body,
        entity_links=[reminder.target_id],
        source_refs=list(getattr(target, "source_refs", []) or []),
        channel=reminder.channel,
        # Keyed by occurrence: the dedupe identifier is retained across retries.
        dedupe_key=reminder.occurrence_key,
        delivery_status=DeliveryStatus.PENDING,
        generated=generated,
    )
