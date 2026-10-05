"""Health and export. Notification reads live in the reminders router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status
from metis_contracts import CONTRACT_VERSION, JobState, new_id, settings
from metis_contracts.api import CreateExportRequest, HealthResponse, JobAccepted
from metis_contracts.entities import Job

from ..deps import Context, get_adapters

router = APIRouter(prefix="/v1", tags=["ops"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Adapter modes, so the client can label fixture mode honestly."""
    adapters = get_adapters(request)
    cfg = settings()
    described = adapters.describe()
    degraded = any(mode == "none" for mode in described.values())
    return HealthResponse(
        status="degraded" if degraded else "ok",
        mode=cfg.mode.value,
        adapters=described,
        contract_version=CONTRACT_VERSION,
    )


@router.get("/limits")
def limits(request: Request) -> dict[str, Any]:
    """The server's own limits, so the browser enforces the same numbers."""
    limits = settings().limits
    return {
        "max_audio_seconds": limits.max_audio_seconds,
        "max_upload_bytes": limits.max_upload_bytes,
        "max_text_chars": limits.max_text_chars,
        "allowed_audio_types": list(limits.allowed_audio_types),
        "max_focus_tasks_default": 3,
    }


@router.post("/exports", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_export(body: CreateExportRequest, ctx: Context) -> JobAccepted:
    """Queue an authenticated export of this user's own records and sources."""
    now = ctx.adapters.clock.now()
    job = Job(
        id=new_id("job"),
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        job_type="export",
        state=JobState.QUEUED,
        correlation_id=ctx.correlation_id,
        idempotency_key=f"export:{ctx.user_id}:{now.isoformat()}",
    )
    with ctx.state.transaction(ctx.user_id) as tx:
        stored = tx.put("job", job)
    return JobAccepted(job_id=stored.id, state=stored.state.value)
