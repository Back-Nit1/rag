"""Planning snapshot assembly with epoch checks.

A plan is built from several reads. If the user edits a commitment mid-assembly
the snapshot would be internally inconsistent, so the planning epoch is read
before and after and the assembly retries within a bounded limit.

The snapshot also records whether the obligation scan was complete. An
incomplete scan forbids claiming the plan covers everything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from metis_contracts import (
    AvailabilityBlock,
    ErrorCode,
    Event,
    MetisError,
    Preferences,
    Task,
    resolve_timezone,
    settings,
)
from metis_pipeline.timeref import end_of_local_day, is_overdue

log = logging.getLogger("metis.planner.snapshot")


@dataclass
class Interval:
    """A concrete free interval in UTC instants."""

    starts_at: datetime
    ends_at: datetime

    @property
    def minutes(self) -> int:
        return int((self.ends_at - self.starts_at).total_seconds() // 60)

    def overlaps(self, other: Interval) -> bool:
        return self.starts_at < other.ends_at and other.starts_at < self.ends_at


@dataclass
class PlanningSnapshot:
    """Everything the planner is allowed to see, and nothing else."""

    user_id: str
    local_date: date
    timezone: str
    planning_epoch: int
    preferences: Preferences
    fixed_events: list[Event] = field(default_factory=list)
    candidate_tasks: list[Task] = field(default_factory=list)
    overflow_tasks: list[Task] = field(default_factory=list)
    availability: list[AvailabilityBlock] = field(default_factory=list)
    free_intervals: list[Interval] = field(default_factory=list)
    inputs_complete: bool = True
    availability_configured: bool = False
    assumptions: list[str] = field(default_factory=list)
    input_revisions: dict[str, int] = field(default_factory=dict)

    @property
    def capacity_minutes(self) -> int:
        return sum(i.minutes for i in self.free_intervals)


def _local_instant(local_date: date, wall: str, timezone_name: str) -> datetime:
    tz = resolve_timezone(timezone_name)
    hour, minute = (int(part) for part in wall.split(":"))
    return datetime.combine(local_date, time(hour, minute), tzinfo=tz)


def _subtract(intervals: list[Interval], busy: Interval) -> list[Interval]:
    """Remove a busy window from a list of free intervals."""
    out: list[Interval] = []
    for free in intervals:
        if not free.overlaps(busy):
            out.append(free)
            continue
        if free.starts_at < busy.starts_at:
            out.append(Interval(free.starts_at, min(free.ends_at, busy.starts_at)))
        if busy.ends_at < free.ends_at:
            out.append(Interval(max(free.starts_at, busy.ends_at), free.ends_at))
    return [i for i in out if i.minutes > 0]


def compute_free_intervals(
    *,
    local_date: date,
    timezone_name: str,
    preferences: Preferences,
    availability: list[AvailabilityBlock],
    fixed_events: list[Event],
    now: datetime,
) -> tuple[list[Interval], bool, list[str]]:
    """Derive free intervals from work hours, manual blocks and fixed events.

    Returns ``(intervals, availability_configured, assumptions)``.
    ``availability_configured`` is False when nothing but the default work
    hours was available — the UI must then say free time is unknown rather than
    implying it was discovered from a calendar.
    """
    assumptions: list[str] = []
    explicit_available = [b for b in availability if b.kind == "available"]
    configured = bool(explicit_available)

    if explicit_available:
        intervals = [
            Interval(
                _local_instant(local_date, b.window.start, timezone_name),
                _local_instant(local_date, b.window.end, timezone_name),
            )
            for b in explicit_available
        ]
    else:
        intervals = [
            Interval(
                _local_instant(local_date, w.start, timezone_name),
                _local_instant(local_date, w.end, timezone_name),
            )
            for w in preferences.work_hours
        ]
        assumptions.append(
            "No availability was entered for this date; the configured work hours were used. "
            "No external calendar is connected."
        )

    # Manual busy blocks.
    for block in (b for b in availability if b.kind == "busy"):
        intervals = _subtract(
            intervals,
            Interval(
                _local_instant(local_date, block.window.start, timezone_name),
                _local_instant(local_date, block.window.end, timezone_name),
            ),
        )

    # Fixed appointments: never silently moved, always carved out.
    for event in fixed_events:
        if event.starts_at is None:
            continue
        ends_at = event.ends_at
        if ends_at is None:
            ends_at = event.starts_at + timedelta(minutes=60)
            assumptions.append(f"'{event.title}' has no end time; 60 minutes is a visible default estimate.")
        intervals = _subtract(intervals, Interval(event.starts_at, ends_at))

    # Nothing is scheduled in the past.
    intervals = [Interval(max(i.starts_at, now), i.ends_at) for i in intervals if i.ends_at > now]
    intervals = [i for i in intervals if i.minutes > 0]
    intervals.sort(key=lambda i: i.starts_at)
    return intervals, configured, assumptions


def rank_candidates(
    tasks: list[Task], *, local_date: date, timezone_name: str, now: datetime, limit: int
) -> tuple[list[Task], list[Task]]:
    """Select focus candidates by deadline, priority and dependency readiness.

    Returns ``(candidates, remainder)``. The remainder is NOT discarded — it
    stays visible as overflow, which is the honest answer when work does not
    fit. Similarity plays no part here.
    """
    ready_ids = {t.id for t in tasks if t.state.value == "done"}
    blocked = {t.id for t in tasks if any(dep not in ready_ids for dep in t.depends_on)}

    def sort_key(task: Task) -> tuple[int, int, int, str]:
        overdue = (
            0
            if task.deadline_local_date
            and is_overdue(task.deadline_local_date, task.timezone or timezone_name, now=now)
            else 1
        )
        days_left = (task.deadline_local_date - local_date).days if task.deadline_local_date else 10_000
        return (overdue, max(0, days_left), -task.priority, task.id)

    eligible = [t for t in tasks if t.id not in blocked]
    eligible.sort(key=sort_key)
    deferred = [t for t in tasks if t.id in blocked]
    return eligible[:limit], eligible[limit:] + deferred


def assemble(
    *,
    state_store: Any,
    retrieval: Any,
    clock: Any,
    user_id: str,
    local_date: date,
    timezone_name: str,
) -> PlanningSnapshot:
    """Read the whole snapshot, retrying if the planning epoch moves.

    The epoch is read before and after: that is how an edit made during a
    multi-page read is detected rather than silently baked in.
    """
    limits = settings().limits
    attempts = 0
    while True:
        attempts += 1
        epoch_before = state_store.get_planning_epoch(user_id)
        snapshot = _assemble_once(
            state_store=state_store,
            retrieval=retrieval,
            clock=clock,
            user_id=user_id,
            local_date=local_date,
            timezone_name=timezone_name,
            epoch=epoch_before,
        )
        epoch_after = state_store.get_planning_epoch(user_id)
        if epoch_after == epoch_before:
            return snapshot
        if attempts >= max(1, limits.planning_epoch_retries):
            raise MetisError(
                ErrorCode.EPOCH_CONFLICT,
                "the plan inputs kept changing while the snapshot was being assembled",
                details={"attempts": str(attempts)},
            )
        log.info("planning epoch moved %s -> %s; retrying", epoch_before, epoch_after)


def _assemble_once(
    *,
    state_store: Any,
    retrieval: Any,
    clock: Any,
    user_id: str,
    local_date: date,
    timezone_name: str,
    epoch: int,
) -> PlanningSnapshot:
    limits = settings().limits
    preferences = state_store.get_preferences(user_id)
    scan = retrieval.list_eligible_obligations(user_id, local_date=local_date)
    availability = retrieval.get_availability(user_id, local_date)
    now = clock.now()

    fixed_events = [e for e in scan.events if e.starts_at is not None and e.local_date == local_date]
    day_end = end_of_local_day(local_date, timezone_name)
    relevant_tasks = [
        t
        for t in scan.tasks
        if t.deadline_local_date is None
        or t.deadline_local_date <= local_date
        or t.deadline_local_date <= (local_date + timedelta(days=limits.planning_horizon_days))
    ]

    candidates, overflow = rank_candidates(
        relevant_tasks,
        local_date=local_date,
        timezone_name=timezone_name,
        now=now,
        limit=min(preferences.max_focus_tasks, limits.planning_max_candidate_tasks),
    )

    intervals, configured, assumptions = compute_free_intervals(
        local_date=local_date,
        timezone_name=timezone_name,
        preferences=preferences,
        availability=availability,
        fixed_events=fixed_events,
        now=now,
    )
    intervals = [i for i in intervals if i.starts_at < day_end]

    if not scan.complete:
        assumptions.append(
            "The obligation scan hit its configured read limit, so this plan does not claim "
            "complete coverage of your commitments."
        )

    revisions = {t.id: t.revision for t in relevant_tasks}
    revisions.update({e.id: e.revision for e in fixed_events})
    revisions[f"pref_{user_id}"] = preferences.revision

    return PlanningSnapshot(
        user_id=user_id,
        local_date=local_date,
        timezone=timezone_name,
        planning_epoch=epoch,
        preferences=preferences,
        fixed_events=sorted(fixed_events, key=lambda e: e.starts_at or now),
        candidate_tasks=candidates,
        overflow_tasks=overflow,
        availability=availability,
        free_intervals=intervals,
        inputs_complete=scan.complete,
        availability_configured=configured,
        assumptions=assumptions,
        input_revisions=revisions,
    )
