"""Planner handlers: asynchronous plan generation and invalidation."""

from __future__ import annotations

import logging

from metis_contracts import ErrorCode, EventEnvelope, EventType, JobState, MetisError, PlanStatus
from metis_contracts.events import PlanRequestedPayload
from metis_pipeline.dispatcher import DispatchContext, HandlerRegistry

from .generator import PlannerService
from .snapshot import assemble

log = logging.getLogger("metis.planner.handlers")

registry = HandlerRegistry()


@registry.register(EventType.PLAN_REQUESTED)
def handle_plan_requested(envelope: EventEnvelope, context: DispatchContext) -> None:
    """Generate one plan for one date. Bounded, and never open-ended."""
    from datetime import date as _date

    from metis_retrieval import RetrievalService

    payload = PlanRequestedPayload.model_validate(envelope.payload)
    state = context.state_store
    existing = state.get(envelope.user_id, "plan", payload.plan_id)
    if existing is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"plan {payload.plan_id} was not created")
    if existing.status in (PlanStatus.ACCEPTED, PlanStatus.INVALIDATED):
        raise MetisError(ErrorCode.REVISION_CONFLICT, f"the plan is already {existing.status.value}")

    retrieval = RetrievalService(
        state_store=state,
        memory_store=context.memory_store,
        embedder=context.embedder,
        clock=context.clock,
    )
    snapshot = assemble(
        state_store=state,
        retrieval=retrieval,
        clock=context.clock,
        user_id=envelope.user_id,
        local_date=_date.fromisoformat(payload.local_date),
        timezone_name=payload.timezone,
    )
    planner = PlannerService(
        state_store=state, retrieval=retrieval, inference=context.inference, clock=context.clock
    )
    plan = planner.build_plan(
        snapshot, time_budget_minutes=payload.time_budget_minutes, plan_id=payload.plan_id
    )

    with state.transaction(envelope.user_id) as tx:
        tx.put(
            "plan",
            plan.model_copy(update={"created_at": existing.created_at}),
            expected_revision=existing.revision,
        )

    job_id = str(envelope.payload.get("job_id") or "")
    if job_id and state.get(envelope.user_id, "job", job_id) is not None:
        state.set_job_state(
            job_id,
            user_id=envelope.user_id,
            state=JobState.READY,
            result_refs={"plan_id": plan.id},
            metrics={
                "blocks": float(len(plan.blocks)),
                "overflow": float(len(plan.overflow_task_ids)),
                "capacity_minutes": float(plan.validation.capacity_minutes if plan.validation else 0),
            },
        )
    log.info(
        "plan %s status=%s blocks=%d overflow=%d valid=%s complete_inputs=%s",
        plan.id, plan.status.value, len(plan.blocks), len(plan.overflow_task_ids),
        plan.validation.valid if plan.validation else None,
        plan.validation.inputs_complete if plan.validation else None,
    )


@registry.register(EventType.PLAN_INVALIDATED)
def handle_plan_invalidated(envelope: EventEnvelope, context: DispatchContext) -> None:
    """Mark a proposed plan stale after its inputs changed.

    A newly added commitment invalidates an old plan even when it was never
    among the retrieved task ids, because the epoch moved.
    """
    state = context.state_store
    plan = state.get(envelope.user_id, "plan", envelope.target_id or "")
    if plan is None or plan.status is not PlanStatus.PROPOSED:
        return
    with state.transaction(envelope.user_id) as tx:
        tx.put(
            "plan",
            plan.model_copy(
                update={
                    "status": PlanStatus.INVALIDATED,
                    "change_summary": "Your commitments changed after this outline was generated.",
                }
            ),
            expected_revision=plan.revision,
        )


__all__ = ["registry"]
