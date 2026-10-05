"""Today, plan request, acceptance and edits."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, status
from metis_contracts import (
    ErrorCode,
    EventType,
    JobState,
    MetisError,
    OutboxEvent,
    Plan,
    PlanStatus,
    new_id,
)
from metis_contracts.api import (
    AcceptPlanRequest,
    CreatePlanRequest,
    CreatePlanResponse,
    PatchPlanRequest,
    TodayResponse,
)
from metis_contracts.entities import Job

from .. import services
from ..deps import Context

router = APIRouter(prefix="/v1", tags=["plans"])


@router.get("/today", response_model=TodayResponse)
def today(ctx: Context, date_: date | None = None) -> TodayResponse:
    return services.get_today(ctx, date_)


@router.post("/plans", response_model=CreatePlanResponse, status_code=status.HTTP_202_ACCEPTED)
def create_plan(body: CreatePlanRequest, ctx: Context) -> CreatePlanResponse:
    """Request one bounded plan for one date. Generation is asynchronous."""
    ctx.require_idempotency_key()
    if replayed := ctx.replay():
        return CreatePlanResponse.model_validate(replayed)

    now = ctx.adapters.clock.now()
    epoch = ctx.state.get_planning_epoch(ctx.user_id)
    plan = Plan(
        id=new_id("plan"),
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        local_date=body.local_date,
        timezone=body.timezone,
        planning_epoch=epoch,
        time_budget_minutes=body.time_budget_minutes or 0,
        status=PlanStatus.PENDING,
    )
    job = Job(
        id=new_id("job"),
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        job_type="plan",
        state=JobState.QUEUED,
        target_id=plan.id,
        target_revision=1,
        correlation_id=ctx.correlation_id,
        idempotency_key=f"plan:{body.local_date.isoformat()}:{epoch}",
    )
    with ctx.state.transaction(ctx.user_id) as tx:
        stored_plan = tx.put("plan", plan)
        stored_job = tx.put("job", job)
        tx.append_outbox(
            OutboxEvent(
                id=new_id("obx"),
                user_id=ctx.user_id,
                created_at=now,
                updated_at=now,
                event_type=EventType.PLAN_REQUESTED,
                target_id=stored_plan.id,
                target_revision=stored_plan.revision,
                correlation_id=ctx.correlation_id,
                idempotency_key=f"plan:{body.local_date.isoformat()}:{epoch}",
                payload={
                    "plan_id": stored_plan.id,
                    "job_id": stored_job.id,
                    "local_date": body.local_date.isoformat(),
                    "timezone": body.timezone,
                    "time_budget_minutes": body.time_budget_minutes or 0,
                    "planning_epoch": epoch,
                },
                available_at=now,
            )
        )
    result = CreatePlanResponse(
        plan_id=stored_plan.id, job_id=stored_job.id, status=stored_plan.status.value
    )
    ctx.remember(result.model_dump(mode="json"))
    return result


@router.get("/plans/{plan_id}", response_model=Plan)
def get_plan(plan_id: str, ctx: Context) -> Plan:
    plan = ctx.state.get(ctx.user_id, "plan", plan_id)
    if plan is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"plan {plan_id} not found")
    return plan


@router.post("/plans/{plan_id}/accept", response_model=Plan)
def accept_plan(plan_id: str, body: AcceptPlanRequest, ctx: Context) -> Plan:
    """Accept a proposed plan, atomically rejecting stale inputs.

    The epoch check is the important part: a commitment added after generation
    invalidates the plan even when it was never among the retrieved task ids.
    """
    plan = ctx.state.get(ctx.user_id, "plan", plan_id)
    if plan is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"plan {plan_id} not found")
    if plan.revision != body.expected_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"plan {plan_id} is at revision {plan.revision}",
            current_revision=plan.revision,
        )
    current_epoch = ctx.state.get_planning_epoch(ctx.user_id)
    if current_epoch != body.expected_planning_epoch or current_epoch != plan.planning_epoch:
        with ctx.state.transaction(ctx.user_id) as tx:
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
        raise MetisError(
            ErrorCode.EPOCH_CONFLICT,
            "your commitments changed after this outline was generated; regenerate it",
            details={"current_epoch": str(current_epoch), "plan_epoch": str(plan.planning_epoch)},
        )
    if plan.status not in (PlanStatus.PROPOSED, PlanStatus.FALLBACK):
        raise MetisError(
            ErrorCode.INVALID_STATE_TRANSITION, f"a {plan.status.value} plan cannot be accepted"
        )
    with ctx.state.transaction(ctx.user_id) as tx:
        return tx.put(
            "plan",
            plan.model_copy(
                update={"status": PlanStatus.ACCEPTED, "accepted_at": ctx.adapters.clock.now()}
            ),
            expected_revision=plan.revision,
        )


@router.patch("/plans/{plan_id}", response_model=Plan)
def patch_plan(plan_id: str, body: PatchPlanRequest, ctx: Context) -> Plan:
    """Edit or pin blocks, then re-run deterministic validation.

    The user's edit is validated by the same application code that validated
    the generated plan — editing cannot bypass the capacity or ordering rules.
    """
    from metis_planner import assemble, validate

    plan = ctx.state.get(ctx.user_id, "plan", plan_id)
    if plan is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"plan {plan_id} not found")
    if plan.revision != body.expected_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"plan {plan_id} is at revision {plan.revision}",
            current_revision=plan.revision,
        )

    blocks = body.blocks if body.blocks is not None else list(plan.blocks)
    if body.pinned_block_indexes is not None:
        pinned = set(body.pinned_block_indexes)
        blocks = [
            block.model_copy(update={"pinned": index in pinned})
            for index, block in enumerate(blocks)
        ]

    snapshot = assemble(
        state_store=ctx.state,
        retrieval=ctx.adapters.retrieval,
        clock=ctx.adapters.clock,
        user_id=ctx.user_id,
        local_date=plan.local_date,
        timezone_name=plan.timezone,
    )
    validation = validate(blocks, snapshot, overflow_ids=plan.overflow_task_ids)
    with ctx.state.transaction(ctx.user_id) as tx:
        return tx.put(
            "plan",
            plan.model_copy(update={"blocks": blocks, "validation": validation}),
            expected_revision=plan.revision,
        )
