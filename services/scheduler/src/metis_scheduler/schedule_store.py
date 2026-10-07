"""ScheduleStore adapters: EventBridge Scheduler, and a local timer table.

A database write and a scheduler change cannot be one transaction, so this is
always driven by the outbox dispatcher. Two consequences are designed in rather
than wished away:

* A successful entity save with a failed schedule create is
  ``pending_schedule`` or ``schedule_failed`` — never "reminder set".
* A superseded schedule may still fire, because deletion is best effort against
  a remote service. The due handler therefore re-validates freshness itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from metis_contracts import AdapterMode, ErrorCode, MetisError, settings
from metis_contracts.ports import Clock, StateStore

log = logging.getLogger("metis.scheduler.store")


def schedule_name(reminder_id: str) -> str:
    """Stable provider-side name, so create is idempotent on the reminder id."""
    return f"metis-rem-{reminder_id}"


def daily_schedule_name(user_id: str) -> str:
    """One stable name per user; hashed because subjects may hold characters
    the provider forbids."""
    return f"metis-daily-{hashlib.sha256(user_id.encode()).hexdigest()[:40]}"


def parse_local_time(local_time: str, timezone: str) -> tuple[time, ZoneInfo]:
    try:
        return time.fromisoformat(local_time), ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError, KeyError) as exc:
        raise MetisError(ErrorCode.VALIDATION_FAILED, f"invalid daily time/timezone: {exc}") from exc


class LocalScheduleStore:
    """Local mode: the reminder row's own ``trigger_at`` is the schedule.

    ``due_reminder_ids`` on the state store is the timer. Nothing external is
    created, so there is nothing to leak between dev runs.
    """

    mode = AdapterMode.LOCAL

    def __init__(self, state_store: StateStore | None = None, clock: Clock | None = None) -> None:
        self._state = state_store
        self._clock = clock
        self.created: dict[str, datetime] = {}
        self.deleted: list[str] = []
        #: user_id -> (local_time, timezone, created_at, payload)
        self.daily: dict[str, tuple[str, str, datetime, dict[str, Any]]] = {}

    def _now(self) -> datetime:
        return self._clock.now() if self._clock else datetime.now(UTC)

    def upsert_daily(self, *, user_id: str, local_time: str, timezone: str, payload: dict[str, Any]) -> str:
        parse_local_time(local_time, timezone)
        old = self.daily.get(user_id)
        # Retrying the same settings keeps the original creation time, so it
        # cannot re-arm an occurrence that already passed.
        created = old[2] if old and old[:2] == (local_time, timezone) else self._now()
        self.daily[user_id] = (local_time, timezone, created, payload)
        return f"local:{daily_schedule_name(user_id)}"

    def delete_daily(self, *, user_id: str, scheduler_ref: str | None) -> None:
        self.daily.pop(user_id, None)

    def list_due_daily(self, *, now: datetime, limit: int = 50) -> list[tuple[str, str]]:
        """Today's occurrence (user local date) once its instant has passed and
        the schedule existed before it. Callers dedupe on (user, date).

        The instant is local wall-clock time in the IANA zone: a time skipped by
        spring-forward fires once, shifted by the gap; an ambiguous fall-back
        time fires at its first occurrence.
        """
        due: list[tuple[str, str]] = []
        for user_id, (local_time, tz, created, _payload) in self.daily.items():
            at, zone = parse_local_time(local_time, tz)
            today: date = now.astimezone(zone).date()
            fire = datetime.combine(today, at, tzinfo=zone).astimezone(UTC)
            if created <= fire <= now:
                due.append((user_id, today.isoformat()))
        return due[:limit]

    def upsert_schedule(
        self, *, reminder_id: str, user_id: str, trigger_at: datetime, payload: dict[str, Any]
    ) -> str:
        self.created[reminder_id] = trigger_at
        self.deleted = [d for d in self.deleted if d != reminder_id]
        return f"local:{schedule_name(reminder_id)}"

    def delete_schedule(self, *, reminder_id: str, scheduler_ref: str | None) -> None:
        self.created.pop(reminder_id, None)
        self.deleted.append(reminder_id)

    def list_due(self, *, now: datetime, limit: int = 50) -> list[str]:
        if self._state is None:
            return [rid for rid, when in self.created.items() if when <= now][:limit]
        return self._state.due_reminder_ids(now=now, limit=limit)


class EventBridgeScheduleStore:
    """One-time EventBridge schedules, one per reminder occurrence.

    The schedule name is derived from the reminder id, so a retried create is
    an update rather than a duplicate. ``FLEXIBLE`` time windows are not used:
    a reminder should fire at the requested minute.
    """

    mode = AdapterMode.CLOUD

    def __init__(self, clock: Clock | None = None) -> None:
        import boto3

        cfg = settings().aws
        self._cfg = cfg
        self._clock = clock
        missing = [
            name
            for name, value in (
                ("METIS_SCHEDULER_GROUP", cfg.scheduler_group),
                ("METIS_SCHEDULER_ROLE_ARN", cfg.scheduler_role_arn),
                ("METIS_REMINDER_TARGET_ARN", cfg.reminder_target_arn),
            )
            if not value
        ]
        if missing or cfg.scheduler_group is None:
            raise ValueError(f"cloud scheduling requires: {', '.join(missing)}")
        # Validated above, so the group name is a str from here on; the target
        # and role arns are read straight off cfg where boto accepts None.
        self._group: str = cfg.scheduler_group
        self._client = boto3.client("scheduler", region_name=cfg.region)

    def upsert_schedule(
        self, *, reminder_id: str, user_id: str, trigger_at: datetime, payload: dict[str, Any]
    ) -> str:
        name = schedule_name(reminder_id)
        # Scheduler expects a naive at() expression plus an explicit timezone.
        at_expression = f"at({trigger_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%S')})"
        request: dict[str, Any] = {
            "Name": name,
            "GroupName": self._group,
            "ScheduleExpression": at_expression,
            "ScheduleExpressionTimezone": "UTC",
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "ActionAfterCompletion": "DELETE",
            "Target": {
                "Arn": self._cfg.reminder_target_arn,
                "RoleArn": self._cfg.scheduler_role_arn,
                "Input": json.dumps(payload),
                "RetryPolicy": {"MaximumRetryAttempts": 2, "MaximumEventAgeInSeconds": 3600},
            },
        }
        try:
            self._client.create_schedule(**request)
        except self._client.exceptions.ConflictException:
            # A retry of the same create: update in place, never duplicate.
            self._client.update_schedule(**request)
        except Exception as exc:
            raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, f"could not create the schedule: {exc}") from exc
        return f"arn:schedule:{self._group}/{name}"

    def delete_schedule(self, *, reminder_id: str, scheduler_ref: str | None) -> None:
        try:
            self._client.delete_schedule(Name=schedule_name(reminder_id), GroupName=self._group)
        except self._client.exceptions.ResourceNotFoundException:
            return  # already gone; deletion is idempotent
        except Exception as exc:
            # Best effort: the schedule may still fire, which the due handler
            # is built to survive.
            log.warning("schedule delete for %s failed: %s", reminder_id, exc)
            raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, f"could not delete the schedule: {exc}") from exc

    def upsert_daily(self, *, user_id: str, local_time: str, timezone: str, payload: dict[str, Any]) -> str:
        """Recurring cron in the user's IANA zone, so DST is the provider's job.

        The target receives ``payload`` plus ``scheduled_time`` (the provider's
        context attribute); the API derives the local date from it.
        """
        at, _ = parse_local_time(local_time, timezone)
        name = daily_schedule_name(user_id)
        request: dict[str, Any] = {
            "Name": name,
            "GroupName": self._group,
            "ScheduleExpression": f"cron({at.minute} {at.hour} * * ? *)",
            "ScheduleExpressionTimezone": timezone,
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "State": "ENABLED",
            "Target": {
                "Arn": self._cfg.reminder_target_arn,
                "RoleArn": self._cfg.scheduler_role_arn,
                "Input": json.dumps({**payload, "scheduled_time": "<aws.scheduler.scheduled-time>"}),
                "RetryPolicy": {"MaximumRetryAttempts": 2, "MaximumEventAgeInSeconds": 3600},
            },
        }
        try:
            try:
                self._client.create_schedule(**request)
            except self._client.exceptions.ConflictException:
                self._client.update_schedule(**request)
        except Exception as exc:
            raise MetisError(
                ErrorCode.PROVIDER_UNAVAILABLE, f"could not sync the daily schedule: {exc}"
            ) from exc
        return f"arn:schedule:{self._group}/{name}"

    def delete_daily(self, *, user_id: str, scheduler_ref: str | None) -> None:
        try:
            self._client.delete_schedule(Name=daily_schedule_name(user_id), GroupName=self._group)
        except self._client.exceptions.ResourceNotFoundException:
            return
        except Exception as exc:
            raise MetisError(
                ErrorCode.PROVIDER_UNAVAILABLE, f"could not delete the daily schedule: {exc}"
            ) from exc

    def list_due_daily(self, *, now: datetime, limit: int = 50) -> list[tuple[str, str]]:
        return []

    def list_due(self, *, now: datetime, limit: int = 50) -> list[str]:
        """Not applicable: EventBridge invokes the due handler directly."""
        return []


def build_schedule_store(state_store: Any | None = None, clock: Any | None = None) -> Any:
    from metis_contracts import Mode

    if settings().mode is Mode.CLOUD:
        return EventBridgeScheduleStore(clock=clock)
    return LocalScheduleStore(state_store=state_store, clock=clock)
