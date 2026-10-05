"""Deterministic plan validation. The model cannot mark its own plan valid.

Every check here is application code: overlap, interval containment, ordering,
capacity, prerequisite order, deadline respect, task state and fixed-block
integrity. A plan that fails is returned with its issues rather than quietly
repaired, so the user sees what was wrong.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from metis_contracts import PlanBlock, PlanValidation, ValidationIssue
from metis_pipeline.timeref import end_of_local_day

from .snapshot import Interval, PlanningSnapshot


def validate(
    blocks: list[PlanBlock], snapshot: PlanningSnapshot, *, overflow_ids: list[str]
) -> PlanValidation:
    issues: list[ValidationIssue] = []
    ordered = sorted(enumerate(blocks), key=lambda pair: pair[1].starts_at)
    task_by_id = {t.id: t for t in snapshot.candidate_tasks + snapshot.overflow_tasks}
    event_by_id = {e.id: e for e in snapshot.fixed_events}

    # 1. No two blocks may overlap.
    for (left_index, left), (right_index, right) in zip(ordered, ordered[1:], strict=False):
        if right.starts_at < left.ends_at:
            issues.append(
                ValidationIssue(
                    code="blocks_overlap",
                    message=f"'{left.title}' and '{right.title}' overlap",
                    block_index=right_index,
                )
            )

    # 2. Every flexible block must sit inside an actual free interval, and
    #    every fixed block must match its real appointment time.
    for index, block in blocks_with_index(blocks):
        if block.event_id and block.fixed:
            event = event_by_id.get(block.event_id)
            if event is None:
                issues.append(
                    ValidationIssue(code="unknown_event", message="fixed block references an unknown event", block_index=index)
                )
            elif event.starts_at and block.starts_at != event.starts_at:
                issues.append(
                    ValidationIssue(
                        code="fixed_block_moved",
                        message=f"'{block.title}' is a fixed appointment and cannot be moved",
                        block_index=index,
                    )
                )
            continue
        if not _within_any(block, snapshot.free_intervals):
            issues.append(
                ValidationIssue(
                    code="outside_availability",
                    message=f"'{block.title}' falls outside the available time",
                    block_index=index,
                )
            )

    # 3. Nothing may be scheduled past the end of the planned local day.
    day_end = end_of_local_day(snapshot.local_date, snapshot.timezone)
    for index, block in blocks_with_index(blocks):
        if block.ends_at > day_end:
            issues.append(
                ValidationIssue(
                    code="past_day_end",
                    message=f"'{block.title}' runs past the end of {snapshot.local_date.isoformat()}",
                    block_index=index,
                )
            )

    # 4. Prerequisites must come first, and may not be scheduled at all if
    #    they are unmet and absent from the plan.
    scheduled_positions = {
        block.task_id: block.starts_at for _, block in blocks_with_index(blocks) if block.task_id
    }
    for index, block in blocks_with_index(blocks):
        task = task_by_id.get(block.task_id or "")
        if task is None:
            continue
        for dependency in task.depends_on:
            prerequisite = task_by_id.get(dependency)
            if prerequisite is not None and prerequisite.state.value == "done":
                continue
            when = scheduled_positions.get(dependency)
            if when is None:
                issues.append(
                    ValidationIssue(
                        code="unmet_dependency",
                        message=f"'{task.title}' depends on work that is neither done nor scheduled",
                        block_index=index,
                    )
                )
            elif when >= block.starts_at:
                issues.append(
                    ValidationIssue(
                        code="dependency_order",
                        message=f"'{task.title}' is scheduled before the work it depends on",
                        block_index=index,
                    )
                )

    # 5. A done or canceled task must never appear in a plan.
    for index, block in blocks_with_index(blocks):
        task = task_by_id.get(block.task_id or "")
        if task is not None and task.state.value in ("done", "canceled"):
            issues.append(
                ValidationIssue(
                    code="task_not_active",
                    message=f"'{task.title}' is {task.state.value} and cannot be planned",
                    block_index=index,
                )
            )

    # 6. A block must not be scheduled after its own deadline.
    for index, block in blocks_with_index(blocks):
        task = task_by_id.get(block.task_id or "")
        if task is None or task.deadline_local_date is None:
            continue
        deadline = end_of_local_day(task.deadline_local_date, task.timezone or snapshot.timezone)
        if block.starts_at >= deadline:
            issues.append(
                ValidationIssue(
                    code="after_deadline",
                    message=f"'{task.title}' is scheduled after its deadline",
                    block_index=index,
                )
            )

    # 7. Capacity. Over-capacity is reported, never hidden by shrinking blocks.
    scheduled_minutes = sum(
        int((b.ends_at - b.starts_at).total_seconds() // 60)
        for _, b in blocks_with_index(blocks)
        if not b.fixed
    )
    capacity = snapshot.capacity_minutes
    if scheduled_minutes > capacity:
        issues.append(
            ValidationIssue(
                code="capacity_exceeded",
                message=f"{scheduled_minutes} minutes of work exceeds {capacity} minutes of capacity",
            )
        )
    if overflow_ids:
        issues.append(
            ValidationIssue(
                code="overflow_present",
                message=f"{len(overflow_ids)} task(s) do not fit and remain in the overflow list",
                severity="warning",
            )
        )
    if not snapshot.availability_configured:
        issues.append(
            ValidationIssue(
                code="availability_assumed",
                message="Free time came from configured work hours, not from entered availability.",
                severity="warning",
            )
        )

    errors = [i for i in issues if i.severity == "error"]
    return PlanValidation(
        valid=not errors,
        issues=issues,
        capacity_minutes=capacity,
        scheduled_minutes=scheduled_minutes,
        inputs_complete=snapshot.inputs_complete,
    )


def blocks_with_index(blocks: list[PlanBlock]):
    return list(enumerate(blocks))


def _within_any(block: PlanBlock, intervals: list[Interval]) -> bool:
    return any(
        interval.starts_at <= block.starts_at and block.ends_at <= interval.ends_at
        for interval in intervals
    )
