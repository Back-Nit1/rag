"""Reminders, notifications and push subscriptions."""

from __future__ import annotations

from datetime import time

from fastapi import APIRouter, Query, status
from metis_contracts import (
    ErrorCode,
    MetisError,
    Notification,
    PushSubscription,
    Reminder,
    ReminderState,
    new_id,
    resolve_timezone,
)
from metis_contracts.api import (
    CreateReminderRequest,
    NotificationPage,
    RegisterPushRequest,
    ReminderPage,
    SnoozeReminderRequest,
)
from metis_pipeline.timeref import to_instant

from ..deps import Context

router = APIRouter(prefix="/v1", tags=["reminders"])


@router.post("/reminders", response_model=Reminder, status_code=status.HTTP_201_CREATED)
def create_reminder(body: CreateReminderRequest, ctx: Context) -> Reminder:
    """Create an explicit reminder request.

    The response distinguishes `pending_schedule` from `scheduled`: a saved
    reminder is not a synchronised schedule.
    """
    from metis_scheduler import request_reminder

    if replayed := ctx.replay():
        return Reminder.model_validate(replayed)

    target = ctx.state.get(ctx.user_id, body.target_kind, body.target_id)
    if target is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"{body.target_kind} {body.target_id} not found")
    if target.revision != body.expected_target_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"{body.target_kind} {body.target_id} is at revision {target.revision}",
            current_revision=target.revision,
        )
    resolve_timezone(body.timezone)

    if body.trigger_at is not None:
        trigger_at = body.trigger_at
    else:
        assert body.local_date and body.local_time  # guaranteed by the contract
        hour, minute = (int(part) for part in body.local_time.split(":"))
        resolution = to_instant(body.local_date, time(hour, minute), body.timezone)
        if resolution.instant is None:
            raise MetisError(
                ErrorCode.SEMANTIC_VALIDATION_FAILED,
                "that local time does not exist on that date because of a daylight-saving change",
            )
        if resolution.dst == "ambiguous":
            raise MetisError(
                ErrorCode.AMBIGUOUS_TARGET,
                "that local time occurs twice on that date; please pick the earlier or later one",
            )
        trigger_at = resolution.instant

    reminder = request_reminder(
        ctx.state,
        user_id=ctx.user_id,
        target_id=body.target_id,
        target_kind=body.target_kind,
        target_revision=target.revision,
        trigger_at=trigger_at,
        timezone_name=body.timezone,
        channel=body.channel,
        requires_generation=body.requires_generation,
        clock=ctx.adapters.clock,
        correlation_id=ctx.correlation_id,
    )
    ctx.remember(reminder.model_dump(mode="json"))
    return reminder


@router.get("/reminders", response_model=ReminderPage)
def list_reminders(
    ctx: Context,
    state: ReminderState | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> ReminderPage:
    return ctx.state.list_reminders(ctx.user_id, state=state, cursor=cursor, limit=limit)


@router.get("/reminders/{reminder_id}", response_model=Reminder)
def get_reminder(reminder_id: str, ctx: Context) -> Reminder:
    return ctx.require("reminder", reminder_id, Reminder)


@router.delete("/reminders/{reminder_id}", response_model=Reminder)
def cancel_reminder_route(reminder_id: str, ctx: Context) -> Reminder:
    """Cancel a reminder.

    Suppression is guaranteed for an occurrence detected before dispatch. A
    message already handed to an external transport may not be recallable.
    """
    from metis_scheduler import cancel_reminder

    reminder = ctx.state.get(ctx.user_id, "reminder", reminder_id)
    if reminder is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"reminder {reminder_id} not found")
    return cancel_reminder(ctx.state, reminder, clock=ctx.adapters.clock, correlation_id=ctx.correlation_id)


@router.post("/reminders/{reminder_id}/snooze", response_model=Reminder)
def snooze(reminder_id: str, body: SnoozeReminderRequest, ctx: Context) -> Reminder:
    from metis_scheduler import snooze_reminder

    reminder = ctx.state.get(ctx.user_id, "reminder", reminder_id)
    if reminder is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"reminder {reminder_id} not found")
    if reminder.revision != body.expected_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"reminder {reminder_id} is at revision {reminder.revision}",
            current_revision=reminder.revision,
        )
    return snooze_reminder(
        ctx.state,
        reminder,
        minutes=body.snooze_minutes,
        clock=ctx.adapters.clock,
        correlation_id=ctx.correlation_id,
    )


@router.get("/notifications", response_model=NotificationPage)
def list_notifications(
    ctx: Context,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> NotificationPage:
    return ctx.state.list_notifications(ctx.user_id, cursor=cursor, limit=limit)


@router.post("/notifications/{notification_id}/read", response_model=Notification)
def mark_read(notification_id: str, ctx: Context) -> Notification:
    """Record a read receipt. This is a separate fact from transport acceptance."""
    notification = ctx.require("notification", notification_id, Notification)
    with ctx.state.transaction(ctx.user_id) as tx:
        return tx.put(
            "notification",
            notification.model_copy(update={"read_at": ctx.adapters.clock.now()}),
            expected_revision=notification.revision,
        )


@router.post("/push-subscriptions", response_model=PushSubscription, status_code=status.HTTP_201_CREATED)
def register_push(body: RegisterPushRequest, ctx: Context) -> PushSubscription:
    """Register this user's browser push subscription.

    Ownership comes from the token, never the body. Web push needs browser and
    platform support; delivery is not a universal guarantee.
    """
    now = ctx.adapters.clock.now()
    subscription = PushSubscription(
        id=new_id("push"),
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        user_agent=body.user_agent,
    )
    with ctx.state.transaction(ctx.user_id) as tx:
        return tx.put("push_subscription", subscription)


@router.delete("/push-subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_push(subscription_id: str, ctx: Context) -> None:
    subscription = ctx.state.get(ctx.user_id, "push_subscription", subscription_id)
    if subscription is None:
        raise MetisError(ErrorCode.NOT_FOUND, "subscription not found")
    ctx.state.tombstone(ctx.user_id, "push_subscription", subscription_id)
