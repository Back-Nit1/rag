"""Capture, transcript correction, proposal resolution and job status."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response, status
from metis_contracts import ErrorCode, MetisError, ObjectRef, SourceArtifact, SourceLifecycle, settings
from metis_contracts.api import (
    AttachCaptureProjectRequest,
    CaptureDetail,
    CapturePage,
    ChooseTargetRequest,
    CompleteCaptureRequest,
    CompleteCaptureResponse,
    CreateCaptureRequest,
    CreateCaptureResponse,
    DeleteCaptureResponse,
    PatchTranscriptRequest,
    ResolveProposalRequest,
    ResolveProposalResponse,
)
from metis_contracts.entities import Job

from .. import services
from ..deps import Context
from ..proposals import choose_status_target, resolve_proposal

router = APIRouter(prefix="/v1", tags=["captures"])


@router.post("/captures", response_model=CreateCaptureResponse, status_code=status.HTTP_201_CREATED)
def create_capture(body: CreateCaptureRequest, ctx: Context) -> CreateCaptureResponse:
    if replayed := ctx.replay():
        return CreateCaptureResponse.model_validate(replayed)
    result = services.create_capture(ctx, body)
    ctx.remember(result.model_dump(mode="json"))
    return result


@router.post("/captures/{capture_id}/complete", response_model=CompleteCaptureResponse)
def complete_capture(capture_id: str, body: CompleteCaptureRequest, ctx: Context) -> CompleteCaptureResponse:
    # A duplicate completion must not start a second logical inference job.
    ctx.require_idempotency_key()
    if replayed := ctx.replay():
        return CompleteCaptureResponse.model_validate(replayed)
    result = services.complete_capture(ctx, capture_id, body)
    ctx.remember(result.model_dump(mode="json"))
    return result


@router.get("/captures", response_model=CapturePage)
def list_captures(
    ctx: Context,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> CapturePage:
    """The owner's live (non-deleted) captures."""
    return ctx.state.list_sources(ctx.user_id, cursor=cursor, limit=limit)


@router.get("/captures/{capture_id}", response_model=CaptureDetail)
def get_capture(capture_id: str, ctx: Context) -> CaptureDetail:
    return services.get_capture(ctx, capture_id)


@router.patch("/captures/{capture_id}/transcript", response_model=CaptureDetail)
def patch_transcript(capture_id: str, body: PatchTranscriptRequest, ctx: Context) -> CaptureDetail:
    return services.patch_transcript(ctx, capture_id, body)


@router.post("/captures/{capture_id}/project", response_model=SourceArtifact)
def attach_capture_project(
    capture_id: str, body: AttachCaptureProjectRequest, ctx: Context
) -> SourceArtifact:
    if replayed := ctx.replay():
        return SourceArtifact.model_validate(replayed)
    result = services.attach_capture_to_project(ctx, capture_id, body)
    ctx.remember(result.model_dump(mode="json"))
    return result


@router.delete("/captures/{capture_id}", response_model=DeleteCaptureResponse)
def delete_capture(capture_id: str, ctx: Context) -> DeleteCaptureResponse:
    return DeleteCaptureResponse.model_validate(services.delete_capture(ctx, capture_id))


@router.post("/proposals/{proposal_id}/resolve", response_model=ResolveProposalResponse)
def resolve(proposal_id: str, body: ResolveProposalRequest, ctx: Context) -> ResolveProposalResponse:
    if replayed := ctx.replay():
        return ResolveProposalResponse.model_validate(replayed)
    result = resolve_proposal(ctx, proposal_id, body)
    ctx.remember(result.model_dump(mode="json"))
    return result


@router.post("/proposals/{proposal_id}/target", response_model=ResolveProposalResponse)
def choose_target(proposal_id: str, body: ChooseTargetRequest, ctx: Context) -> ResolveProposalResponse:
    if replayed := ctx.replay():
        return ResolveProposalResponse.model_validate(replayed)
    result = choose_status_target(ctx, proposal_id, body)
    ctx.remember(result.model_dump(mode="json"))
    return result


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, ctx: Context) -> Job:
    return ctx.require("job", job_id, Job)


@router.put("/local-uploads/{capture_id}", status_code=status.HTTP_204_NO_CONTENT)
async def local_upload(capture_id: str, request: Request, ctx: Context) -> Response:
    """Local-mode stand-in for a presigned upload.

    It exists so the browser's upload path is identical in local and cloud
    mode. It still enforces ownership and the byte limit, so the validation
    code is the same code.
    """
    if settings().is_cloud:
        raise MetisError(ErrorCode.NOT_FOUND, "uploads go directly to object storage in cloud mode")
    source = ctx.state.get(ctx.user_id, "source", capture_id)
    if source is None or source.object_ref is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"capture {capture_id} has no upload target")
    if not ctx.adapters.source_store.owns_key(ctx.user_id, source.object_ref.key):
        raise MetisError(ErrorCode.FORBIDDEN, "the object key is outside your prefix")

    if source.lifecycle is not SourceLifecycle.PENDING_UPLOAD:
        # An accepted recording is immutable: replacing it after completion would
        # make the transcript describe bytes that are no longer stored.
        raise MetisError(ErrorCode.INVALID_STATE_TRANSITION, "the recording was already completed")
    declared = (source.object_ref.content_type or "").split(";")[0].strip().lower()
    sent = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if declared and sent != declared:
        raise MetisError(ErrorCode.UNSUPPORTED_MEDIA, f"expected {declared}, got {sent or 'no content type'}")

    body = await request.body()
    limit = settings().limits.max_upload_bytes
    if len(body) > limit:
        raise MetisError(ErrorCode.UPLOAD_TOO_LARGE, f"the upload is {len(body)} bytes, over {limit}")
    if not body:
        raise MetisError(ErrorCode.UNSUPPORTED_MEDIA, "the upload is empty")
    ctx.adapters.source_store.put_bytes(
        ObjectRef(bucket=source.object_ref.bucket, key=source.object_ref.key),
        body,
        content_type=declared or "application/octet-stream",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
