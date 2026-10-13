"""Projects, resumption work packets and memory search."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Query, status
from metis_contracts import ErrorCode, MetisError, Project, new_id
from metis_contracts.api import (
    PatchProjectRequest,
    ProjectDetail,
    ProjectPage,
    ResumeProjectRequest,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from metis_contracts.common import Evidence

from ..deps import Context

router = APIRouter(prefix="/v1", tags=["projects"])


@router.get("/projects", response_model=ProjectPage)
def list_projects(
    ctx: Context, cursor: str | None = None, limit: int = Query(default=50, ge=1, le=200)
) -> ProjectPage:
    return ctx.state.list_projects(ctx.user_id, cursor=cursor, limit=limit)


@router.post("/projects", response_model=Project, status_code=status.HTTP_201_CREATED)
def create_project(body: Project, ctx: Context) -> Project:
    if body.id and not re.fullmatch(r"[A-Za-z0-9_-]{3,128}", body.id):
        raise MetisError(ErrorCode.VALIDATION_FAILED, "id may contain only letters, digits, '_' and '-'")
    now = ctx.adapters.clock.now()
    project = body.model_copy(
        update={
            "id": body.id or new_id("proj"),
            "user_id": ctx.user_id,  # ownership is never taken from the body
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
    )
    with ctx.state.transaction(ctx.user_id) as tx:
        return tx.put("project", project)


@router.patch("/projects/{project_id}", response_model=Project)
def patch_project(project_id: str, body: PatchProjectRequest, ctx: Context) -> Project:
    """Omitted key = unchanged; null clears objective/pinned_context only."""
    if replayed := ctx.replay():
        return Project.model_validate(replayed)
    project = ctx.require("project", project_id, Project)
    if project.revision != body.expected_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"project {project_id} is at revision {project.revision}",
            current_revision=project.revision,
        )
    patch = body.model_dump(exclude_unset=True, exclude={"expected_revision"})
    if "aliases" in patch:
        patch["aliases"] = list(dict.fromkeys(patch["aliases"]))
    with ctx.state.transaction(ctx.user_id) as tx:
        stored = tx.put("project", project.model_copy(update=patch), expected_revision=project.revision)
    ctx.remember(stored.model_dump(mode="json"))
    return stored


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str, ctx: Context) -> ProjectDetail:
    """Current project state plus source-backed resumption context."""
    project = ctx.state.get(ctx.user_id, "project", project_id)
    if project is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"project {project_id} not found")

    retrieval = ctx.adapters.retrieval
    open_tasks = [
        t
        for t in ctx.state.list_tasks(ctx.user_id, project_id=project_id, limit=100).items
        if t.state.value not in ("done", "canceled")
    ]
    history = retrieval.get_project_history(ctx.user_id, project_id, limit=10)
    results = [
        SearchResult(
            memory_id=memory.id,
            kind=memory.kind,
            content=memory.content,
            score=0.0,  # direct read, not a ranked search
            project_id=memory.project_id,
            evidence=Evidence(
                source_id=memory.source_refs[0].source_id,
                source_version=memory.source_refs[0].source_version,
                span_ref=memory.source_refs[0].span_ref,
                memory_id=memory.id,
                excerpt=memory.content[:2000],
                recorded_at=memory.effective_at,
                validity="historical"
                if memory.kind.value in ("attempt", "work_log")
                else memory.validity.value,
            ),
        )
        for memory in history
        if memory.source_refs
    ]
    packet = ctx.state.get(ctx.user_id, "work_packet", f"wp_{project_id}")
    stale = bool(
        packet
        and any(
            (
                ctx.state.get(ctx.user_id, "task", entity_id)
                or ctx.state.get(ctx.user_id, "project", entity_id)
            )
            is None
            or getattr(
                ctx.state.get(ctx.user_id, "task", entity_id)
                or ctx.state.get(ctx.user_id, "project", entity_id),
                "revision",
                revision,
            )
            != revision
            for entity_id, revision in (packet.input_revisions or {}).items()
        )
    )
    return ProjectDetail(
        project=project,
        open_tasks=open_tasks,
        recent_memories=results,
        work_packet=packet,
        work_packet_stale=stale,
    )


@router.post("/search", response_model=SearchResponse)
def search(body: SearchRequest, ctx: Context) -> SearchResponse:
    """Scoped memory search with source-backed results.

    An unavailable index degrades visibly rather than failing: the response
    says which sources were available instead of inventing an answer.
    """
    retrieval = ctx.adapters.retrieval
    memories, degraded = retrieval.retrieve_supporting_memories(
        ctx.user_id,
        query=body.query,
        project_id=body.project_id,
        limit=body.limit,
        include_historical=body.include_historical,
    )
    kinds = {k.value for k in body.kinds}
    results = [
        SearchResult(
            memory_id=memory.id,
            kind=memory.kind,
            content=memory.content,
            score=score,
            project_id=memory.project_id,
            evidence=Evidence(
                source_id=memory.source_refs[0].source_id,
                source_version=memory.source_refs[0].source_version,
                span_ref=memory.source_refs[0].span_ref,
                memory_id=memory.id,
                excerpt=memory.content[:2000],
                recorded_at=memory.effective_at,
                validity="historical"
                if memory.kind.value in ("attempt", "work_log")
                else memory.validity.value,
            ),
        )
        for memory, score in memories
        if not kinds or memory.kind.value in kinds
    ]
    mode_used = body.mode
    if body.mode == "auto":
        store = ctx.adapters.memory_store
        mode_used = "hybrid" if getattr(store, "supports_lexical", False) else "semantic"
    return SearchResponse(
        results=results,
        mode_used=mode_used,
        index_available=degraded is None,
        index_stale=False,
        degraded_reason=degraded,
    )


@router.post("/projects/{project_id}/resume")
def resume_project(project_id: str, body: ResumeProjectRequest, ctx: Context) -> dict[str, Any]:
    """Assemble a work packet: objective, attempts, blocker, next action.

    Any inferred file path or command is a suggestion, which is why the
    response labels its own provenance rather than asserting verified facts.
    """
    project = ctx.state.get(ctx.user_id, "project", project_id)
    if project is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"project {project_id} not found")
    retrieval = ctx.adapters.retrieval
    history = retrieval.get_project_history(ctx.user_id, project_id, limit=20)
    attempts = [m for m in history if m.kind.value in ("attempt", "work_log")]
    blockers = [m for m in history if m.kind.value == "blocker"]
    return {
        "project_id": project_id,
        "minutes": body.minutes,
        "objective": project.objective,
        "attempts": [
            {"summary": m.content, "recorded_at": m.effective_at.isoformat(), "outcome": "failed"}
            for m in attempts
        ],
        "blocker": blockers[0].content if blockers else None,
        "provenance": "assembled from recorded sources; suggested next actions are not verified facts",
        "evidence": [
            {
                "source_id": m.source_refs[0].source_id,
                "source_version": m.source_refs[0].source_version,
                "span_ref": m.source_refs[0].span_ref,
            }
            for m in history
            if m.source_refs
        ],
    }
