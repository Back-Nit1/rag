"""Plan enqueueing shared by ``POST /v1/plans`` and the scheduled daily hook.

Both callers create the same plan + job + ``plan.requested`` outbox event in
one transaction; generation, the budget gate and freshness checks stay in the
ordinary worker path. The scheduled path never trusts its payload: it re-reads
preferences and derives the local date and planning epoch server-side.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from metis_contracts import (
    ErrorCode,
    EventType,
    JobState,
    MetisError,
    OutboxEvent,
    Plan,
    PlanStatus,
    new_id,
    resolve_timezone,
)
from metis_contracts.entities import Job
from metis_contracts.ports import Clock, StateStore

log = logging.getLogger("metis.api.planning")

#: Provider clocks may fire marginally early; anything further ahead is bogus.
_FUTURE_SKEW = timedelta(minutes=5)


def request_plan(
    state: StateStore,
    clock: Clock,
    *,
    user_id: str,
    local_date: date,
    timezone: str,
    time_budget_minutes: int,
    correlation_id: str,
    idempotency_key: str | None = None,
    plan_id: str | None = None,
) -> tuple[Plan, Job]:
    """Create one pending plan, its job and the outbox event atomically.

    A caller-chosen ``plan_id`` makes creation single-winner: a second attempt
    hits REVISION_CONFLICT inside the transaction, and a failed attempt leaves
    nothing behind, so a retry can still succeed.
    """
    now = clock.now()
    epoch = state.get_planning_epoch(user_id)
    key = idempotency_key or f"plan:{local_date.isoformat()}:{epoch}"
    plan = Plan(
        id=plan_id or new_id("plan"),
        user_id=user_id,
        created_at=now,
        updated_at=now,
        local_date=local_date,
        timezone=timezone,
        planning_epoch=epoch,
        time_budget_minutes=time_budget_minutes,
        status=PlanStatus.PENDING,
    )
    job = Job(
        id=new_id("job"),
        user_id=user_id,
        created_at=now,
        updated_at=now,
        job_type="plan",
        state=JobState.QUEUED,
        target_id=plan.id,
        target_revision=1,
        correlation_id=correlation_id,
        idempotency_key=key,
    )
    with state.transaction(user_id) as tx:
        stored_plan = tx.put("plan", plan)
        stored_job = tx.put("job", job)
        if plan_id:
            tx.remember_idempotent(key, {"plan_id": stored_plan.id, "job_id": stored_job.id})
        tx.append_outbox(
            OutboxEvent(
                id=new_id("obx"),
                user_id=user_id,
                created_at=now,
                updated_at=now,
                event_type=EventType.PLAN_REQUESTED,
                target_id=stored_plan.id,
                target_revision=stored_plan.revision,
                correlation_id=correlation_id,
                idempotency_key=key,
                payload={
                    "plan_id": stored_plan.id,
                    "job_id": stored_job.id,
                    "local_date": local_date.isoformat(),
                    "timezone": timezone,
                    "time_budget_minutes": time_budget_minutes,
                    "planning_epoch": epoch,
                },
                available_at=now,
            )
        )
    return stored_plan, stored_job


def _planned_local(day: date, hhmm: str, zone: Any) -> tuple[datetime, bool]:
    """Wall-clock planning time on ``day`` and whether it exists that day.

    A time skipped by spring-forward does not round-trip through UTC; the
    scheduler fires it shifted by the gap.
    """
    hh, mm = (int(p) for p in hhmm.split(":"))
    wall = datetime.combine(day, time(hh, mm), tzinfo=zone)
    return wall, wall.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == wall.replace(tzinfo=None)


def _matches_schedule(local: datetime, hhmm: str, zone: Any) -> bool:
    wall, exists = _planned_local(local.date(), hhmm, zone)
    minute = local.replace(second=0, microsecond=0)
    if exists:
        return minute.replace(tzinfo=None) == wall.replace(tzinfo=None)
    delta = minute.replace(tzinfo=None) - wall.replace(tzinfo=None)
    return timedelta(0) <= delta <= timedelta(hours=1)


def handle_daily_due(
    state: StateStore,
    clock: Clock,
    *,
    user_id: str,
    scheduled_time: str | None = None,
    local_date: str | None = None,
    scheduler_ref: str | None = None,
) -> dict[str, Any]:
    """Enqueue today's plan for ``user_id`` if this invocation is still valid.

    ``scheduled_time`` (cloud: ISO instant from the provider) or ``local_date``
    (local timer) is only a hint. Returns ``{"enqueued": 0|1, "reason", ...}``.
    """

    def skip(reason: str) -> dict[str, Any]:
        log.info("daily plan suppressed user=%s reason=%s ref=%s", user_id, reason, scheduler_ref)
        return {"enqueued": 0, "reason": reason}

    prefs = state.get_preferences(user_id)
    if state.get(user_id, "preferences", prefs.id) is None:
        return skip("no_preferences")
    if not prefs.daily_planning_enabled:
        return skip("disabled")

    zone = resolve_timezone(prefs.timezone)
    now = clock.now()
    today = clock.today_local(prefs.timezone)
    if scheduled_time is not None:
        try:
            instant = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
        except ValueError:
            return skip("invalid_scheduled_time")
        if instant.tzinfo is None or instant > now + _FUTURE_SKEW:
            return skip("invalid_scheduled_time")
        local = instant.astimezone(zone)
        if not _matches_schedule(local, prefs.daily_planning_local_time, zone):
            return skip("stale_schedule")
        day = local.date()
    else:
        try:
            day = date.fromisoformat(local_date or "")
        except ValueError:
            return skip("invalid_local_date")
        wall, _ = _planned_local(day, prefs.daily_planning_local_time, zone)
        if now < wall.astimezone(UTC):
            return skip("stale_schedule")
    if day != today:
        return skip("stale_date")

    key = f"daily-plan:{day.isoformat()}"
    if (prior := state.recall_idempotent(user_id, key)) is not None:
        return {"enqueued": 0, "reason": "duplicate", **prior}
    try:
        plan, job = request_plan(
            state,
            clock,
            user_id=user_id,
            local_date=day,
            timezone=prefs.timezone,
            time_budget_minutes=0,
            correlation_id=f"daily:{user_id}:{day.isoformat()}",
            idempotency_key=key,
            plan_id=f"plan_daily_{day.isoformat()}",
        )
    except MetisError as exc:
        if exc.code is not ErrorCode.REVISION_CONFLICT:
            raise
        prior = state.recall_idempotent(user_id, key) or {}
        return {"enqueued": 0, "reason": "duplicate", **prior}
    result = {"plan_id": plan.id, "job_id": job.id}
    return {"enqueued": 1, "reason": "enqueued", **result}


def run_local_daily_due(state: StateStore, clock: Clock, schedule_store: Any) -> list[dict[str, Any]]:
    """Local-mode timer tick: act on every occurrence the local store reports."""
    return [
        handle_daily_due(state, clock, user_id=user_id, local_date=day)
        for user_id, day in schedule_store.list_due_daily(now=clock.now())
    ]
