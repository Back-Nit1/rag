"""Health and export. Notification reads live in the reminders router."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Request, Response, status
from metis_contracts import CONTRACT_VERSION, ErrorCode, EventType, JobState, MetisError, new_id, settings
from metis_contracts.api import CreateExportRequest, ExportStatusResponse, HealthResponse, JobAccepted
from metis_contracts.entities import Job, ObjectRef, OutboxEvent

from ..deps import Context, RequestContext, get_adapters

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
    """Queue a durable export of this user's own records.

    The job and its ``export.requested`` outbox event commit together. The
    selection flags ride in the event payload because ``Job`` has no field for
    them. A retry with the same Idempotency-Key replays the first response.
    """
    key = ctx.require_idempotency_key()
    if replayed := ctx.replay():
        return JobAccepted.model_validate(replayed)
    now = ctx.adapters.clock.now()
    dedupe = f"export:{key}"
    job = Job(
        id=new_id("job"),
        user_id=ctx.user_id,
        created_at=now,
        updated_at=now,
        job_type="export",
        state=JobState.QUEUED,
        correlation_id=ctx.correlation_id,
        idempotency_key=dedupe,
        adapter_mode=ctx.adapters.inference.mode,
    )
    with ctx.state.transaction(ctx.user_id) as tx:
        stored = tx.put("job", job)
        tx.append_outbox(
            OutboxEvent(
                id=new_id("obx"),
                user_id=ctx.user_id,
                created_at=now,
                updated_at=now,
                event_type=EventType.EXPORT_REQUESTED,
                target_id=stored.id,
                correlation_id=ctx.correlation_id,
                idempotency_key=dedupe,
                payload={"job_id": stored.id, **body.model_dump()},
                available_at=now,
            )
        )
    result = JobAccepted(job_id=stored.id, state=stored.state.value)
    ctx.remember(result.model_dump(mode="json"))
    return result


def _owned_export(ctx: RequestContext, job_id: str) -> Job:
    job = ctx.state.get(ctx.user_id, "job", job_id)
    if not isinstance(job, Job) or job.job_type != "export":
        raise MetisError(ErrorCode.NOT_FOUND, f"export {job_id} not found")
    return job


def _verified_result(ctx: RequestContext, job: Job) -> ObjectRef | None:
    """The stored archive ref, only when the job is durably complete and the key is the owner's."""
    refs = job.result_refs
    if job.state != JobState.READY or not {"object_key", "sha256", "bytes"} <= refs.keys():
        return None
    if not ctx.adapters.source_store.owns_key(ctx.user_id, refs["object_key"]):
        return None
    return ObjectRef(
        bucket="", key=refs["object_key"], size_bytes=int(refs["bytes"]), checksum_sha256=refs["sha256"]
    )


@router.get("/exports/{job_id}", response_model=ExportStatusResponse)
def export_status(job_id: str, ctx: Context) -> ExportStatusResponse:
    job = _owned_export(ctx, job_id)
    ref = _verified_result(ctx, job)
    return ExportStatusResponse(
        job_id=job.id,
        state=job.state,
        created_at=job.created_at,
        finished_at=job.finished_at,
        byte_size=ref.size_bytes if ref else None,
        sha256=ref.checksum_sha256 if ref else None,
        download_url=f"/v1/exports/{job.id}/download" if ref else None,
        error_message=job.error_message if job.state == JobState.FAILED else None,
    )


@router.get("/exports/{job_id}/download")
def export_download(job_id: str, ctx: Context) -> Response:
    """Controlled, authenticated download: ownership and checksum are re-verified per request."""
    ref = _verified_result(ctx, _owned_export(ctx, job_id))
    if ref is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"export {job_id} is not ready")
    body = ctx.adapters.source_store.get_bytes(ref)
    if hashlib.sha256(body).hexdigest() != ref.checksum_sha256:
        raise MetisError(ErrorCode.OBJECT_MISSING, "the stored export failed its checksum")
    return Response(
        body,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="metis-export-{job_id}.zip"',
            "Cache-Control": "private, no-store",
        },
    )
