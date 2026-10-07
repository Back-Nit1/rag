"""Scheduler handlers: schedule sync, cancel sync and the due dispatch."""

from __future__ import annotations

import logging

from metis_contracts import (
    DeliveryStatus,
    ErrorCode,
    EventEnvelope,
    EventType,
    FailureCategory,
    MetisError,
    ReminderState,
)
from metis_contracts.entities import Notification
from metis_contracts.events import ReminderDuePayload, ReminderSyncPayload
from metis_pipeline.dispatcher import DispatchContext, HandlerRegistry

from .service import build_notification, evaluate_due, occurrence_key

log = logging.getLogger("metis.scheduler.handlers")

registry = HandlerRegistry()


@registry.register(EventType.REMINDER_SCHEDULE_SYNC)
def handle_schedule_sync(envelope: EventEnvelope, context: DispatchContext) -> None:
    """Create or update the provider schedule, then record the honest state."""
    if context.schedule_store is None:
        raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "no schedule store is configured")

    payload = ReminderSyncPayload.model_validate(envelope.payload)
    state = context.state_store
    reminder = state.get(envelope.user_id, "reminder", payload.reminder_id)
    if reminder is None:
        raise MetisError(ErrorCode.TOMBSTONED, "the reminder was deleted")
    # Supersession is judged by the TRIGGER, not by the revision: this handler's
    # own schedule_failed bookkeeping bumps the revision, and that must not
    # block its retry. A re-pointed reminder has a different trigger_at and
    # raises its own sync event.
    if payload.trigger_at is not None and reminder.trigger_at != payload.trigger_at:
        raise MetisError(ErrorCode.REVISION_CONFLICT, "the reminder was re-pointed; this sync is superseded")
    if reminder.state in (ReminderState.CANCELED, ReminderState.SUPERSEDED):
        raise MetisError(ErrorCode.REVISION_CONFLICT, f"the reminder is {reminder.state.value}")

    try:
        scheduler_ref = context.schedule_store.upsert_schedule(
            reminder_id=reminder.id,
            user_id=envelope.user_id,
            trigger_at=reminder.trigger_at,
            payload={
                "reminder_id": reminder.id,
                "user_id": envelope.user_id,
                "occurrence_key": reminder.occurrence_key,
                "scheduled_for": reminder.trigger_at.isoformat(),
            },
        )
    except MetisError:
        # The entity stays saved. The UI shows schedule_failed, never
        # "reminder set", and a retry recovers without duplicating.
        with state.transaction(envelope.user_id) as tx:
            tx.put(
                "reminder",
                reminder.model_copy(
                    update={
                        "state": ReminderState.SCHEDULE_FAILED,
                        "last_error": FailureCategory.PROVIDER_UNAVAILABLE,
                        "attempts": reminder.attempts + 1,
                    }
                ),
                expected_revision=reminder.revision,
            )
        raise

    with state.transaction(envelope.user_id) as tx:
        tx.put(
            "reminder",
            reminder.model_copy(
                update={
                    "state": ReminderState.SCHEDULED,
                    "scheduler_ref": scheduler_ref,
                    "last_error": None,
                }
            ),
            expected_revision=reminder.revision,
        )
    log.info("reminder %s scheduled ref=%s", reminder.id, scheduler_ref)


@registry.register(EventType.DAILY_PLAN_SCHEDULE_SYNC)
def handle_daily_schedule_sync(envelope: EventEnvelope, context: DispatchContext) -> None:
    """Make the provider match current preferences: upsert, or delete if disabled.

    Always syncs the CURRENT preferences (not the event's snapshot), and an
    event older than the stored revision is superseded by the newer one.
    ``payload.enabled`` (default true) false means planning is off. Failures
    raise, so the outbox retries and then dead-letters: never reported as synced.
    """
    store = context.schedule_store
    if store is None:
        raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "no schedule store is configured")
    prefs = context.state_store.get_preferences(envelope.user_id)
    if envelope.target_revision is not None and int(prefs.revision) > int(envelope.target_revision):
        raise MetisError(ErrorCode.REVISION_CONFLICT, "preferences changed; this sync is superseded")
    if envelope.payload.get("enabled", True) is False:
        store.delete_daily(user_id=envelope.user_id, scheduler_ref=envelope.payload.get("scheduler_ref"))
        log.info("daily plan schedule removed for %s", envelope.user_id)
        return
    ref = store.upsert_daily(
        user_id=envelope.user_id,
        local_time=prefs.daily_planning_local_time,
        timezone=prefs.timezone,
        payload={"user_id": envelope.user_id},
    )
    log.info("daily plan scheduled for %s ref=%s", envelope.user_id, ref)


@registry.register(EventType.REMINDER_CANCEL_SYNC)
def handle_cancel_sync(envelope: EventEnvelope, context: DispatchContext) -> None:
    """Request deletion of a provider schedule. Deletion is idempotent."""
    if context.schedule_store is None:
        raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "no schedule store is configured")
    payload = ReminderSyncPayload.model_validate(envelope.payload)
    reminder = context.state_store.get(envelope.user_id, "reminder", payload.reminder_id)
    context.schedule_store.delete_schedule(
        reminder_id=payload.reminder_id,
        scheduler_ref=payload.scheduler_ref or (reminder.scheduler_ref if reminder else None),
    )
    log.info("schedule deletion requested for reminder %s", payload.reminder_id)


@registry.register(EventType.REMINDER_DUE)
def handle_reminder_due(envelope: EventEnvelope, context: DispatchContext) -> None:
    """Validate freshness, claim the occurrence once, then dispatch.

    The order matters: every suppression check runs before the dispatch
    decision, and the conditional claim runs before any transport call, so a
    duplicate worker cannot produce a second in-app notification.
    """
    payload = ReminderDuePayload.model_validate(envelope.payload)
    state = context.state_store
    reminder = state.get(envelope.user_id, "reminder", payload.reminder_id)
    if reminder is None:
        raise MetisError(ErrorCode.TOMBSTONED, "the reminder was deleted")

    decision = evaluate_due(state, reminder, occurrence=payload.occurrence_key, clock=context.clock)
    if not decision.deliver:
        log.info("suppressed reminder %s: %s", reminder.id, decision.reason)
        if decision.defer_until is not None:
            # Quiet hours: re-point rather than drop.
            from .service import retarget_reminder

            retarget_reminder(
                state,
                reminder,
                trigger_at=decision.defer_until,
                target_revision=reminder.target_revision,
                clock=context.clock,
                correlation_id=envelope.correlation_id,
            )
            return
        raise MetisError(ErrorCode.REVISION_CONFLICT, decision.reason)

    # One winner per occurrence. A second worker simply stops here.
    if not state.claim_once(envelope.user_id, f"notify:{reminder.occurrence_key}", context.worker_id):
        log.info("another worker already claimed occurrence %s", reminder.occurrence_key)
        return

    target = state.get(envelope.user_id, reminder.target_kind, reminder.target_id)
    notification = build_notification(reminder, target, clock=context.clock)

    with state.transaction(envelope.user_id) as tx:
        stored_notification = tx.put("notification", notification)
        tx.put(
            "reminder",
            reminder.model_copy(update={"state": ReminderState.DISPATCHING}),
            expected_revision=reminder.revision,
        )

    transport = context.notification_transports.get(
        reminder.channel.value
    ) or context.notification_transports.get("in_app")
    detail: str | None
    if transport is None:
        status, detail = DeliveryStatus.UNSUPPORTED, "no transport is configured"
    elif not transport.supported(envelope.user_id):
        # Not a failure: the in-app feed already holds the record.
        status, detail = DeliveryStatus.UNSUPPORTED, f"{reminder.channel.value} is unavailable for this user"
    else:
        # Final freshness re-check immediately before handing anything to an
        # external transport. After this point the message may not be recallable.
        fresh = state.get(envelope.user_id, "reminder", reminder.id)
        if fresh is None or fresh.state in (ReminderState.CANCELED, ReminderState.SUPERSEDED):
            log.info("reminder %s canceled during dispatch; nothing was sent", reminder.id)
            return
        status, detail = transport.send(stored_notification)

    reminder_after = state.get(envelope.user_id, "reminder", reminder.id)
    with state.transaction(envelope.user_id) as tx:
        current = state.get(envelope.user_id, "notification", stored_notification.id)
        if not isinstance(current, Notification):
            # The feed record was deleted between dispatch and the receipt
            # write. Nothing left to annotate; the delivery itself already
            # happened and must not be retried.
            log.warning("notification %s vanished before its receipt", stored_notification.id)
            return
        tx.put(
            "notification",
            current.model_copy(
                update={
                    "delivery_status": status,
                    "delivery_detail": detail,
                    "accepted_at": context.clock.now() if status is DeliveryStatus.ACCEPTED else None,
                }
            ),
            expected_revision=current.revision,
        )
        if reminder_after is not None and reminder_after.state is ReminderState.DISPATCHING:
            terminal = (
                ReminderState.DELIVERED
                if status in (DeliveryStatus.ACCEPTED, DeliveryStatus.UNSUPPORTED)
                else ReminderState.DELIVERY_FAILED
            )
            tx.put(
                "reminder",
                reminder_after.model_copy(
                    update={
                        "state": terminal,
                        "attempts": reminder_after.attempts + 1,
                        "last_error": None
                        if terminal is ReminderState.DELIVERED
                        else FailureCategory.TRANSIENT,
                    }
                ),
                expected_revision=reminder_after.revision,
            )
    log.info(
        "reminder %s occurrence=%s delivery=%s detail=%s",
        reminder.id,
        reminder.occurrence_key,
        status.value,
        detail,
    )


__all__ = ["occurrence_key", "registry"]
