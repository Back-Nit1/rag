"""Resumable owner-scoped export: ``export.requested`` -> deterministic zip.

Ownership comes from the event envelope; the job is re-read from state and the
payload only supplies the (bounded, boolean) inclusion choices. The archive is
written under the owner's prefix, verified by re-reading its stored size and
checksum, and only then is the job marked READY in a single state update.
Exported content is never logged and never placed in events or job records.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import time
import zipfile
from typing import Any

from metis_contracts import CONTRACT_VERSION, SCHEMA_VERSION, ErrorCode, EventEnvelope, JobState, MetisError
from metis_contracts.entities import Job, ObjectRef, SourceArtifact
from pydantic import BaseModel, ConfigDict

from .dispatcher import DispatchContext

log = logging.getLogger("metis.pipeline.export")

PAGE_SIZE = 200
MAX_PAGES_PER_KIND = 100
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_SECONDS = 120.0
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_TERMINAL_CODES = {ErrorCode.LIMIT_EXCEEDED, ErrorCode.FORBIDDEN, ErrorCode.VALIDATION_FAILED}


class ExportRequestedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    job_id: str
    include_sources: bool
    include_memories: bool
    include_commitments: bool


class _Budget:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.bytes = 0

    def add(self, data: bytes) -> None:
        self.bytes += len(data)
        if self.bytes > MAX_ARCHIVE_BYTES:
            raise MetisError(ErrorCode.LIMIT_EXCEEDED, "the export exceeds the maximum archive size")
        if time.monotonic() - self.started > MAX_SECONDS:
            raise MetisError(ErrorCode.LIMIT_EXCEEDED, "the export exceeded its time limit")


def _pages(fetch: Any, user_id: str, budget: _Budget) -> list[Any]:
    """Every owner record across bounded pages; deterministic order by id."""
    items: list[Any] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES_PER_KIND):
        page = fetch(user_id, cursor=cursor, limit=PAGE_SIZE)
        items.extend(r for r in page.items if r.user_id == user_id)
        budget.add(b"")
        if not page.next_cursor:
            return sorted(items, key=lambda r: r.id)
        cursor = page.next_cursor
    raise MetisError(ErrorCode.LIMIT_EXCEEDED, "the export has too many records to bundle")


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(json.dumps(r, sort_keys=True, separators=(",", ":")).encode() + b"\n" for r in rows)


def _collect(
    context: DispatchContext, user_id: str, payload: ExportRequestedPayload, budget: _Budget
) -> dict[str, tuple[bytes, str]]:
    """section file name -> (bytes, status). Pure reads; owner-scoped."""
    state: Any = context.state_store
    files: dict[str, tuple[bytes, str]] = {}

    def dump(records: list[Any], **kw: Any) -> bytes:
        body = _jsonl([r.model_dump(mode="json", **kw) for r in records])
        budget.add(body)
        return body

    if payload.include_sources:
        lister = getattr(state, "list_sources", None)
        if lister is None:
            # The StateStore port has no source listing yet; say so, never imply it was exported.
            files["sources.jsonl"] = (b"", "unavailable")
        else:
            live: list[SourceArtifact] = []
            for s in _pages(lister, user_id, budget):
                fresh = state.get(user_id, "source", s.id)  # deletion race: skip what vanished
                if fresh is not None and fresh.tombstoned_at is None:
                    live.append(fresh)
            files["sources.jsonl"] = (dump(live, exclude={"object_ref"}), "included")
    if payload.include_memories:
        files["memories.jsonl"] = (dump(_pages(state.list_memories, user_id, budget)), "included")
    if payload.include_commitments:
        files["tasks.jsonl"] = (dump(_pages(state.list_tasks, user_id, budget)), "included")
        files["events.jsonl"] = (dump(_pages(state.list_events, user_id, budget)), "included")
    return files


def build_archive(
    context: DispatchContext, user_id: str, job_id: str, payload: ExportRequestedPayload
) -> bytes:
    """Deterministic zip: sorted entries, fixed timestamps, canonical JSON."""
    budget = _Budget()
    files = _collect(context, user_id, payload, budget)
    manifest = {
        "kind": "metis-export",
        "job_id": job_id,
        "contract_version": CONTRACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "build": os.environ.get("METIS_BUILD", "unknown"),
        "selection": {
            "sources": payload.include_sources,
            "memories": payload.include_memories,
            "commitments": payload.include_commitments,
        },
        "note": "Audio and image objects are not bundled; sources carry metadata and transcripts only.",
        "sections": {
            name: {
                "status": status,
                "records": body.count(b"\n"),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            for name, (body, status) in sorted(files.items())
        },
    }
    entries = {"manifest.json": json.dumps(manifest, sort_keys=True, indent=2).encode()}
    entries.update({n: b for n, (b, _s) in files.items() if _s == "included"})
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            zf.writestr(info, entries[name])
    data = out.getvalue()
    if len(data) > MAX_ARCHIVE_BYTES:
        raise MetisError(ErrorCode.LIMIT_EXCEEDED, "the export exceeds the maximum archive size")
    return data


def export_key(context: DispatchContext, user_id: str, job_id: str) -> str:
    from metis_state.object_store import owner_prefix

    key = f"{owner_prefix(user_id)}/exports/{job_id}.zip"
    if not context.source_store.owns_key(user_id, key):
        raise MetisError(ErrorCode.FORBIDDEN, "export key is outside the owner prefix")
    return key


def _fail(context: DispatchContext, user_id: str, job_id: str, message: str) -> None:
    context.state_store.set_job_state(
        job_id, user_id=user_id, state=JobState.FAILED, error_message=message[:1000]
    )


def handle_export_requested(envelope: EventEnvelope, context: DispatchContext) -> None:
    user_id = envelope.user_id
    state = context.state_store
    try:
        payload = ExportRequestedPayload.model_validate(envelope.payload)
    except Exception as exc:
        raise MetisError(ErrorCode.VALIDATION_FAILED, "malformed export request") from exc
    job_id = envelope.target_id or payload.job_id
    job = state.get(user_id, "job", job_id)
    if not isinstance(job, Job) or job.job_type != "export" or payload.job_id != job_id:
        raise MetisError(ErrorCode.NOT_FOUND, "export job not found")
    if job.state in (JobState.READY, JobState.FAILED, JobState.CANCELED):
        return  # duplicate delivery

    with state.transaction(user_id) as tx:
        job = tx.put(
            "job",
            job.model_copy(
                update={
                    "state": JobState.STARTING,
                    "attempts": job.attempts + 1,
                    "started_at": job.started_at or context.clock.now(),
                }
            ),
            expected_revision=job.revision,
        )

    try:
        key = export_key(context, user_id, job_id)
        body = build_archive(context, user_id, job_id, payload)
        ref = ObjectRef(bucket="", key=key, content_type="application/zip")
        context.source_store.put_bytes(ref, body, content_type="application/zip")
        digest = hashlib.sha256(body).hexdigest()
        stored = context.source_store.head(ref)
        if stored is None or stored.size_bytes != len(body) or stored.checksum_sha256 != digest:
            raise MetisError(ErrorCode.OBJECT_MISSING, "the stored export could not be verified")
    except MetisError as exc:
        if exc.code in _TERMINAL_CODES or job.attempts >= job.max_attempts:
            _fail(context, user_id, job_id, exc.message)
            log.warning("export %s failed code=%s", job_id, exc.code)
            return
        raise
    except Exception as exc:
        if job.attempts >= job.max_attempts:
            _fail(context, user_id, job_id, f"export failed: {type(exc).__name__}")
            return
        raise

    state.set_job_state(
        job_id,
        user_id=user_id,
        state=JobState.READY,
        result_refs={"object_key": key, "sha256": digest, "bytes": str(len(body))},
    )
    log.info("export %s ready bytes=%d", job_id, len(body))
