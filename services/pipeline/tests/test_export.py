"""Export worker: selection, determinism, idempotency, failure, isolation."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime

import pytest
from metis_contracts import (
    ErrorCode,
    EventEnvelope,
    EventType,
    FixedClock,
    JobState,
    MetisError,
    ObjectRef,
    SourceRef,
    new_id,
    reset_settings_cache,
)
from metis_contracts.entities import Job, Memory, Task
from metis_pipeline import export as export_mod
from metis_pipeline.dispatcher import DispatchContext
from metis_pipeline.handlers import registry
from metis_state import LocalSourceStore, SqliteStateStore, owner_prefix

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
USER, OTHER = "user_a", "user_b"


@pytest.fixture()
def ctx(tmp_path):
    reset_settings_cache()
    clock = FixedClock(T0)
    yield DispatchContext(
        state_store=SqliteStateStore(str(tmp_path / "s.sqlite"), clock=clock),
        source_store=LocalSourceStore(str(tmp_path / "o"), clock=clock),
        clock=clock,
        worker_id="w",
    )
    reset_settings_cache()


def make_job(ctx, user=USER, job_type="export", max_attempts=3):
    now = ctx.clock.now()
    job = Job(
        id=new_id("job"),
        user_id=user,
        created_at=now,
        updated_at=now,
        job_type=job_type,
        correlation_id="c",
        idempotency_key=new_id("k"),
        max_attempts=max_attempts,
    )
    with ctx.state_store.transaction(user) as tx:
        return tx.put("job", job)


def add_task(ctx, user, title):
    now = ctx.clock.now()
    with ctx.state_store.transaction(user) as tx:
        tx.put("task", Task(id=new_id("tsk"), user_id=user, created_at=now, updated_at=now, title=title))


def add_memory(ctx, user, text):
    now = ctx.clock.now()
    with ctx.state_store.transaction(user) as tx:
        tx.put(
            "memory",
            Memory(
                id=new_id("mem"),
                user_id=user,
                created_at=now,
                updated_at=now,
                kind="observation",
                content=text,
                effective_at=now,
                source_refs=[SourceRef(source_id="src_x", source_version=1, span_ref="0-1")],
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
            ),
        )


def env(job, user=USER, **sel):
    payload = {
        "job_id": job.id,
        "include_sources": True,
        "include_memories": True,
        "include_commitments": True,
    }
    payload.update(sel)
    return EventEnvelope(
        event_type=EventType.EXPORT_REQUESTED,
        user_id=user,
        correlation_id="c",
        target_id=job.id,
        idempotency_key=f"export:{job.id}",
        payload=payload,
    )


def run(ctx, e):
    registry.get(EventType.EXPORT_REQUESTED)(e, ctx)


def zip_of(ctx, job):
    done = ctx.state_store.get(USER, "job", job.id)
    body = ctx.source_store.get_bytes(ObjectRef(bucket="", key=done.result_refs["object_key"]))
    return done, body, zipfile.ZipFile(io.BytesIO(body))


def test_ready_result_refs_and_manifest(ctx):
    job = make_job(ctx)
    add_task(ctx, USER, "pay rent")
    add_memory(ctx, USER, "likes tea")
    run(ctx, env(job))
    done, body, zf = zip_of(ctx, job)
    assert done.state == JobState.READY and done.finished_at
    refs = done.result_refs
    assert refs["object_key"] == f"{owner_prefix(USER)}/exports/{job.id}.zip"
    assert refs["sha256"] == hashlib.sha256(body).hexdigest() and refs["bytes"] == str(len(body))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["contract_version"] == 1 and "schema_version" in manifest and "build" in manifest
    assert manifest["sections"]["tasks.jsonl"]["records"] == 1
    assert "pay rent" in zf.read("tasks.jsonl").decode()
    assert manifest["sections"]["sources.jsonl"]["status"] == "included"


def test_exclusions_are_honoured(ctx):
    job = make_job(ctx)
    add_task(ctx, USER, "t")
    add_memory(ctx, USER, "m")
    run(ctx, env(job, include_memories=False, include_commitments=False, include_sources=False))
    _, _, zf = zip_of(ctx, job)
    assert zf.namelist() == ["manifest.json"]


def test_pagination_exports_every_record(ctx, monkeypatch):
    monkeypatch.setattr(export_mod, "PAGE_SIZE", 2)
    for i in range(5):
        add_task(ctx, USER, f"task {i}")
    job = make_job(ctx)
    run(ctx, env(job, include_memories=False))
    _, _, zf = zip_of(ctx, job)
    assert len(zf.read("tasks.jsonl").splitlines()) == 5


def test_archive_is_deterministic(ctx):
    add_task(ctx, USER, "x")
    job = make_job(ctx)
    p = export_mod.ExportRequestedPayload(
        job_id=job.id, include_sources=True, include_memories=True, include_commitments=True
    )
    assert export_mod.build_archive(ctx, USER, job.id, p) == export_mod.build_archive(ctx, USER, job.id, p)


def test_duplicate_delivery_is_a_noop(ctx):
    job = make_job(ctx)
    run(ctx, env(job))
    first = ctx.state_store.get(USER, "job", job.id)
    add_task(ctx, USER, "later")
    run(ctx, env(job))
    assert ctx.state_store.get(USER, "job", job.id).revision == first.revision


def test_cross_tenant_records_and_jobs_are_isolated(ctx):
    add_task(ctx, OTHER, "secret other")
    add_memory(ctx, OTHER, "secret mem")
    job = make_job(ctx)
    run(ctx, env(job))
    _, _, zf = zip_of(ctx, job)
    assert b"secret" not in b"".join(zf.read(n) for n in zf.namelist())
    # a job owned by someone else, named by a forged event for USER
    foreign = make_job(ctx, user=OTHER)
    with pytest.raises(MetisError) as err:
        run(ctx, env(foreign, user=USER))
    assert err.value.code == ErrorCode.NOT_FOUND
    assert ctx.state_store.get(OTHER, "job", foreign.id).state == JobState.QUEUED


def test_non_export_job_is_rejected(ctx):
    job = make_job(ctx, job_type="transcription")
    with pytest.raises(MetisError):
        run(ctx, env(job))


def test_limit_exceeded_fails_terminally_with_short_message(ctx, monkeypatch):
    monkeypatch.setattr(export_mod, "MAX_ARCHIVE_BYTES", 10)
    add_task(ctx, USER, "x" * 100)
    job = make_job(ctx)
    run(ctx, env(job))
    done = ctx.state_store.get(USER, "job", job.id)
    assert done.state == JobState.FAILED and len(done.error_message) <= 1000 and not done.result_refs


def test_partial_write_failure_retries_then_fails_on_last_attempt(ctx, monkeypatch):
    job = make_job(ctx, max_attempts=2)

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(ctx.source_store, "put_bytes", boom)
    with pytest.raises(OSError):
        run(ctx, env(job))
    mid = ctx.state_store.get(USER, "job", job.id)
    assert mid.state == JobState.STARTING and not mid.result_refs
    run(ctx, env(job))  # attempt 2 == max_attempts: terminal, no raise
    done = ctx.state_store.get(USER, "job", job.id)
    assert done.state == JobState.FAILED and "OSError" in done.error_message
    assert "disk" not in done.error_message


def test_retry_after_transient_failure_succeeds(ctx, monkeypatch):
    job = make_job(ctx)
    real = ctx.source_store.put_bytes
    calls = {"n": 0}

    def flaky(ref, body, *, content_type):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("x")
        return real(ref, body, content_type=content_type)

    monkeypatch.setattr(ctx.source_store, "put_bytes", flaky)
    with pytest.raises(OSError):
        run(ctx, env(job))
    run(ctx, env(job))
    assert ctx.state_store.get(USER, "job", job.id).state == JobState.READY


def test_malformed_payload_is_a_validation_error(ctx):
    job = make_job(ctx)
    e = env(job)
    e.payload.pop("include_sources")
    with pytest.raises(MetisError) as err:
        run(ctx, e)
    assert err.value.code == ErrorCode.VALIDATION_FAILED


def test_deleted_source_during_export_is_skipped(ctx, monkeypatch):
    from metis_contracts.entities import SourceArtifact
    from metis_contracts.enums import SourceKind, SourceLifecycle

    now = ctx.clock.now()
    with ctx.state_store.transaction(USER) as tx:
        s = tx.put(
            "source",
            SourceArtifact(
                id=new_id("src"),
                user_id=USER,
                created_at=now,
                updated_at=now,
                kind=SourceKind.TEXT,
                lifecycle=SourceLifecycle.READY,
                capture_timezone="UTC",
                captured_at=now,
            ),
        )
    listed = ctx.state_store._page("source", USER, "", [], cursor=None, limit=10)
    monkeypatch.setattr(ctx.state_store, "list_sources", lambda u, cursor=None, limit=100: listed)
    ctx.state_store.purge(USER, "source", s.id)  # vanishes between listing and read
    job = make_job(ctx)
    run(ctx, env(job))
    _, _, zf = zip_of(ctx, job)
    assert zf.read("sources.jsonl") == b""


def test_sources_are_exported_owner_scoped_without_audio(ctx):
    from metis_contracts.entities import SourceArtifact
    from metis_contracts.enums import SourceKind, SourceLifecycle

    now = ctx.clock.now()
    for user in (USER, OTHER):
        with ctx.state_store.transaction(user) as tx:
            tx.put(
                "source",
                SourceArtifact(
                    id=f"src_{user}", user_id=user, created_at=now, updated_at=now,
                    kind=SourceKind.AUDIO, lifecycle=SourceLifecycle.READY,
                    capture_timezone="UTC", captured_at=now,
                    object_ref=ObjectRef(bucket="", key=f"{owner_prefix(user)}/a.wav", content_type="audio/wav"),
                ),
            )  # fmt: skip
    job = make_job(ctx)
    run(ctx, env(job))
    _, _, zf = zip_of(ctx, job)
    manifest = json.loads(zf.read("manifest.json"))
    body = zf.read("sources.jsonl").decode()
    assert manifest["sections"]["sources.jsonl"] == {
        "status": "included",
        "records": 1,
        "sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    assert f"src_{USER}" in body and f"src_{OTHER}" not in body
    assert "object_ref" not in body and "a.wav" not in body
