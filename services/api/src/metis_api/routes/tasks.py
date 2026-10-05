"""Tasks, events, availability and preferences."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, status
from metis_contracts import (
    AvailabilityBlock,
    ErrorCode,
    Event,
    MetisError,
    Preferences,
    Task,
    TaskState,
    new_id,
)
from metis_contracts.api import (
    CreateEventRequest,
    CreateTaskRequest,
    EventPage,
    PatchEventRequest,
    PatchPreferencesRequest,
    PatchTaskRequest,
    TaskPage,
    UsageResponse,
)

from .. import services
from ..deps import Context

router = APIRouter(prefix="/v1", tags=["tasks"])


@router.get("/tasks", response_model=TaskPage)
def list_tasks(
    ctx: Context,
    state: list[TaskState] | None = Query(default=None),
    project_id: str | None = None,
    due_before: date | None = None,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> TaskPage:
    return ctx.state.list_tasks(
        ctx.user_id,
        states=state,
        project_id=project_id,
        due_before=due_before,
        cursor=cursor,
        limit=limit,
    )


@router.post("/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
def create_task(body: CreateTaskRequest, ctx: Context) -> Task:
    if replayed := ctx.replay():
        return Task.model_validate(replayed)
    now = ctx.adapters.clock.now()
    task = Task(
        id=new_id("task"),
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        **body.model_dump(),
    )
    with ctx.state.transaction(ctx.user_id) as tx:
        stored = tx.put("task", task)
    ctx.remember(stored.model_dump(mode="json"))
    return stored


@router.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: str, ctx: Context) -> Task:
    task = ctx.state.get(ctx.user_id, "task", task_id)
    if task is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"task {task_id} not found")
    return task


@router.patch("/tasks/{task_id}", response_model=Task)
def patch_task(task_id: str, body: PatchTaskRequest, ctx: Context) -> Task:
    return services.patch_task(ctx, task_id, body)


@router.get("/tasks/{task_id}/revisions")
def task_revisions(task_id: str, ctx: Context) -> list[dict]:
    """Source-linked change history, newest first."""
    reader = getattr(ctx.state, "get_revisions", None)
    return reader(ctx.user_id, "task", task_id) if reader else []


@router.get("/events", response_model=EventPage)
def list_events(
    ctx: Context,
    local_date: date | None = None,
    until: date | None = None,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> EventPage:
    return ctx.state.list_events(ctx.user_id, local_date=local_date, until=until, cursor=cursor, limit=limit)


@router.post("/events", response_model=Event, status_code=status.HTTP_201_CREATED)
def create_event(body: CreateEventRequest, ctx: Context) -> Event:
    if replayed := ctx.replay():
        return Event.model_validate(replayed)
    now = ctx.adapters.clock.now()
    event = Event(
        id=new_id("event"),
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        **body.model_dump(),
    )
    with ctx.state.transaction(ctx.user_id) as tx:
        stored = tx.put("event", event)
    ctx.remember(stored.model_dump(mode="json"))
    return stored


@router.get("/events/{event_id}", response_model=Event)
def get_event(event_id: str, ctx: Context) -> Event:
    event = ctx.state.get(ctx.user_id, "event", event_id)
    if event is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"event {event_id} not found")
    return event


@router.patch("/events/{event_id}", response_model=Event)
def patch_event(event_id: str, body: PatchEventRequest, ctx: Context) -> Event:
    return services.patch_event(ctx, event_id, body)


@router.get("/availability", response_model=list[AvailabilityBlock])
def list_availability(local_date: date, ctx: Context) -> list[AvailabilityBlock]:
    return ctx.state.list_availability(ctx.user_id, local_date)


@router.post("/availability", response_model=AvailabilityBlock, status_code=status.HTTP_201_CREATED)
def create_availability(body: AvailabilityBlock, ctx: Context) -> AvailabilityBlock:
    """Manual availability. No external calendar provider is required.

    The client never supplies ownership: whatever user_id arrives is replaced
    with the authenticated subject.
    """
    now = ctx.adapters.clock.now()
    block = body.model_copy(
        update={
            "id": body.id or new_id("avail"),
            "user_id": ctx.user_id,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
    )
    existing = ctx.state.get(ctx.user_id, "availability", block.id)
    with ctx.state.transaction(ctx.user_id) as tx:
        return tx.put("availability", block, expected_revision=existing.revision if existing else None)


@router.get("/preferences", response_model=Preferences)
def get_preferences(ctx: Context) -> Preferences:
    return ctx.state.get_preferences(ctx.user_id)


@router.patch("/preferences", response_model=Preferences)
def patch_preferences(body: PatchPreferencesRequest, ctx: Context) -> Preferences:
    return services.patch_preferences(ctx, body)


@router.get("/usage", response_model=UsageResponse)
def get_usage(ctx: Context, local_date: date | None = None) -> UsageResponse:
    """Measured model usage, kept separate from estimated infrastructure cost."""
    from metis_contracts import settings

    preferences = ctx.state.get_preferences(ctx.user_id)
    day = local_date or ctx.adapters.clock.today_local(preferences.timezone)
    usage = ctx.state.get_usage(ctx.user_id, day)
    limit = settings().limits.daily_micro_usd
    return UsageResponse(
        local_date=day,
        measured_model_micro_usd=usage.settled_micro_usd,
        reserved_micro_usd=usage.reserved_micro_usd,
        estimated_infra_micro_usd=None,
        unmeasured_components=[
            "Fargate ASR task time",
            "object storage and requests",
            "DynamoDB requests",
            "queues, scheduler and logs",
            "network egress",
        ],
        model_calls=usage.model_calls,
        audio_seconds=usage.audio_seconds,
        speech_jobs=usage.speech_jobs,
        daily_limit_micro_usd=limit,
        budget_exhausted=usage.committed_micro_usd >= limit,
    )
