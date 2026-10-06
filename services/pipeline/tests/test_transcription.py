"""Cloud ASR boundary: launch, finalize, and every way a result can be wrong."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from metis_contracts import (
    AsrTaskOutcome,
    ErrorCode,
    EventEnvelope,
    EventType,
    FailureCategory,
    FixedClock,
    JobState,
    MetisError,
    ObjectRef,
    SourceKind,
    SourceLifecycle,
    new_id,
    reset_settings_cache,
)
from metis_contracts.entities import SourceArtifact
from metis_pipeline.dispatcher import DispatchContext
from metis_pipeline.transcription import (
    StepFunctionsLauncher,
    finalize_transcription,
    launch_transcription,
    transcribe_inline,
)
from metis_speech import FixtureTranscriber
from metis_speech.worker import result_key, run
from metis_state import LocalSourceStore, SqliteStateStore, build_key

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
USER, OTHER = "user_a", "user_b"


class FakeLauncher:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.error: MetisError | None = None

    def start(self, job):
        if self.error:
            raise self.error
        self.started.append(job.job_id)
        return f"exec:{job.job_id}"

    def stop(self, ref):
        pass


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("METIS_JOB_MAX_ATTEMPTS", "3")
    reset_settings_cache()
    clock = FixedClock(T0)
    state = SqliteStateStore(str(tmp_path / "s.sqlite"), clock=clock)
    objects = LocalSourceStore(str(tmp_path / "o"), clock=clock)
    launcher = FakeLauncher()
    ctx = DispatchContext(
        state_store=state,
        source_store=objects,
        clock=clock,
        transcriber=FixtureTranscriber(),
        asr_launcher=launcher,
        worker_id="w",
    )
    yield ctx, launcher
    reset_settings_cache()


def make_source(ctx, *, user=USER, body=b"audio-bytes"):
    sid = new_id("src")
    ref = ObjectRef(bucket="local", key=build_key(user, sid, ".webm"), content_type="audio/webm")
    ctx.source_store.put_bytes(ref, body, content_type="audio/webm")
    now = ctx.clock.now()
    source = SourceArtifact(
        id=sid,
        user_id=user,
        created_at=now,
        updated_at=now,
        kind=SourceKind.AUDIO,
        lifecycle=SourceLifecycle.PROCESSING,
        capture_timezone="America/New_York",
        captured_at=now,
        object_ref=ref,
    )
    with ctx.state_store.transaction(user) as tx:
        return tx.put("source", source)


def envelope(source, job_id="job_1", user=USER, revision=None):
    return EventEnvelope(
        event_id=new_id("evt"),
        event_type=EventType.CAPTURE_COMPLETED,
        user_id=user,
        correlation_id="corr",
        target_id=source.id,
        target_revision=revision or source.current_version,
        idempotency_key=f"transcribe:{source.id}:{source.current_version}",
        payload={"job_id": job_id},
    )


def job_of(ctx, job_id="job_1", user=USER):
    return ctx.state_store.get(user, "job", job_id)


def run_worker(ctx, source, job_id="job_1"):
    """What the ECS task does: decode and persist next to the source."""
    from metis_pipeline.transcription import _job_input

    job = job_of(ctx, job_id)
    run(_job_input(source, job), source_store=ctx.source_store, transcriber=FixtureTranscriber())


def outcome(status="succeeded", job_id="job_1", user=USER, **kw):
    return AsrTaskOutcome(user_id=user, job_id=job_id, status=status, **kw)


def finalized_events(ctx):
    events = ctx.state_store.claim_outbox(owner="t", limit=100, lease_seconds=60)
    return [e for e in events if e.event_type == EventType.TRANSCRIPT_FINALIZED]


def test_inline_mode_is_unchanged_and_needs_no_launcher(env):
    ctx, launcher = env
    ctx.asr_launcher = None
    src = make_source(ctx)
    transcribe_inline(envelope(src), ctx)
    assert job_of(ctx).state is JobState.READY
    assert launcher.started == []


def test_launch_is_not_a_transcript_and_duplicate_completion_launches_once(env):
    ctx, launcher = env
    src = make_source(ctx)
    launch_transcription(envelope(src), ctx)
    launch_transcription(envelope(src), ctx)
    job = job_of(ctx)
    assert job.state is JobState.TRANSCRIBING and job.result_refs["execution_ref"] == "exec:job_1"
    assert launcher.started == ["job_1"]
    stored = ctx.state_store.get(USER, "source", src.id)
    assert stored.current_text() is None and stored.lifecycle is SourceLifecycle.PROCESSING


def test_success_finalizes_once_even_if_task_completion_repeats(env):
    ctx, _ = env
    src = make_source(ctx)
    launch_transcription(envelope(src), ctx)
    run_worker(ctx, src)
    finalize_transcription(ctx, outcome())
    finalize_transcription(ctx, outcome())
    assert job_of(ctx).state is JobState.READY
    stored = ctx.state_store.get(USER, "source", src.id)
    assert stored.lifecycle is SourceLifecycle.READY and len(stored.transcripts) == 1
    assert len(finalized_events(ctx)) == 1


def test_stale_revision_at_launch_is_superseded_and_not_launched(env):
    ctx, launcher = env
    src = make_source(ctx)
    with pytest.raises(MetisError) as exc:
        launch_transcription(envelope(src, revision=src.current_version + 1), ctx)
    assert exc.value.code is ErrorCode.REVISION_CONFLICT
    assert launcher.started == []


def test_edit_during_transcription_discards_late_result(env):
    ctx, _ = env
    src = make_source(ctx)
    launch_transcription(envelope(src), ctx)
    run_worker(ctx, src)
    moved = ctx.state_store.get(USER, "source", src.id).model_copy(update={"current_version": 2})
    with ctx.state_store.transaction(USER) as tx:
        tx.put("source", moved, expected_revision=src.revision)
    assert finalize_transcription(ctx, outcome()).state is JobState.CANCELED
    assert ctx.state_store.get(USER, "source", src.id).current_text() is None
    assert finalized_events(ctx) == []


def test_delete_defeats_late_result(env):
    ctx, _ = env
    src = make_source(ctx)
    launch_transcription(envelope(src), ctx)
    run_worker(ctx, src)
    ctx.state_store.tombstone(USER, "source", src.id)
    assert finalize_transcription(ctx, outcome()).state is JobState.CANCELED
    assert finalized_events(ctx) == []


def test_unsupported_media_fails_the_job_without_launching(env):
    ctx, launcher = env
    src = make_source(ctx, body=b"")
    launch_transcription(envelope(src), ctx)  # permanent: no raise, no retry
    job = job_of(ctx)
    assert job.state is JobState.FAILED and job.failure == "unsupported_media"
    assert launcher.started == []
    assert ctx.state_store.get(USER, "source", src.id).lifecycle is SourceLifecycle.FAILED


def test_launch_failure_defers_then_fails_visibly(env):
    ctx, launcher = env
    launcher.error = MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "ecs down")
    src = make_source(ctx)
    for expected in (JobState.QUEUED, JobState.QUEUED):
        with pytest.raises(MetisError):
            launch_transcription(envelope(src), ctx)
        assert job_of(ctx).state is expected
    with pytest.raises(MetisError):  # third attempt hits job_max_attempts
        launch_transcription(envelope(src), ctx)
    job = job_of(ctx)
    assert job.state is JobState.FAILED and job.error_message == "ecs down"


def test_timeout_and_worker_failure_become_failed_jobs(env):
    ctx, _ = env
    src = make_source(ctx)
    launch_transcription(envelope(src), ctx)
    job = finalize_transcription(ctx, outcome("timed_out"))
    assert job.state is JobState.FAILED and "exceeded" in job.error_message
    assert job.failure is FailureCategory.TIMEOUT
    assert ctx.state_store.get(USER, "source", src.id).lifecycle is SourceLifecycle.FAILED


def test_worker_exit_failure_keeps_its_category(env):
    ctx, _ = env
    src = make_source(ctx)
    launch_transcription(envelope(src), ctx)
    job = finalize_transcription(ctx, outcome("failed", failure="unsupported_media", error_message="corrupt"))
    assert job.failure == "unsupported_media" and job.error_message == "corrupt"


def test_abort_cancels_and_a_later_success_cannot_resurrect(env):
    ctx, _ = env
    src = make_source(ctx)
    launch_transcription(envelope(src), ctx)
    finalize_transcription(ctx, outcome("aborted"))
    run_worker(ctx, src)
    assert finalize_transcription(ctx, outcome()).state is JobState.CANCELED
    assert ctx.state_store.get(USER, "source", src.id).current_text() is None


def test_success_without_a_persisted_result_is_a_failure(env):
    ctx, _ = env
    src = make_source(ctx)
    launch_transcription(envelope(src), ctx)
    job = finalize_transcription(ctx, outcome())
    assert job.state is JobState.FAILED and job.failure == "internal"


def test_result_for_another_job_is_rejected(env):
    ctx, _ = env
    src = make_source(ctx)
    launch_transcription(envelope(src), ctx)
    run_worker(ctx, src)
    from metis_pipeline.transcription import _job_input

    ref = ObjectRef(bucket="local", key=result_key(_job_input(src, job_of(ctx))))
    data = json.loads(ctx.source_store.get_bytes(ref))
    data["job_id"] = "job_other"
    ctx.source_store.put_bytes(ref, json.dumps(data).encode(), content_type="application/json")
    job = finalize_transcription(ctx, outcome())
    assert job.state is JobState.FAILED and job.failure == "permission_denied"
    assert ctx.state_store.get(USER, "source", src.id).current_text() is None


def test_another_user_cannot_finalize_a_job(env):
    ctx, _ = env
    src = make_source(ctx)
    launch_transcription(envelope(src), ctx)
    with pytest.raises(MetisError) as exc:
        finalize_transcription(ctx, outcome(user=OTHER))
    assert exc.value.code is ErrorCode.NOT_FOUND


def test_key_outside_owner_prefix_is_refused_at_launch(env):
    ctx, launcher = env
    src = make_source(ctx)
    foreign = build_key(OTHER, src.id, ".webm")
    bad = src.model_copy(update={"object_ref": src.object_ref.model_copy(update={"key": foreign})})
    with ctx.state_store.transaction(USER) as tx:
        tx.put("source", bad, expected_revision=src.revision)
    with pytest.raises(MetisError) as exc:
        launch_transcription(envelope(src), ctx)
    assert exc.value.code is ErrorCode.FORBIDDEN and launcher.started == []


# ---- StepFunctionsLauncher against a fake client ---------------------------


class ExistsError(Exception):
    pass


def fake_sfn(running=(), start_error=None):
    calls = []

    def start_execution(**kw):
        calls.append(kw)
        if start_error:
            raise start_error
        return {"executionArn": "arn:aws:states:r:1:execution:m:" + kw["name"]}

    return SimpleNamespace(
        exceptions=SimpleNamespace(
            ExecutionAlreadyExists=ExistsError, ClientError=type("CE", (Exception,), {})
        ),
        list_executions=lambda **kw: {"executions": [{"name": n} for n in running]},
        start_execution=start_execution,
        calls=calls,
    )


def spec(job_id="job_1"):
    from metis_contracts import TranscriptionJobInput

    return TranscriptionJobInput(
        recording_id="src_1",
        user_id=USER,
        source_version=1,
        s3_bucket="b",
        s3_key="k",
        idempotency_key="i",
        correlation_id="c",
        job_id=job_id,
    )


def test_launcher_names_execution_after_job_and_caps_concurrency(env):
    sfn = fake_sfn()
    assert StepFunctionsLauncher("arn:aws:states:r:1:stateMachine:m", sfn).start(spec()).endswith(":job_1")
    assert sfn.calls[0]["name"] == "job_1"
    busy = StepFunctionsLauncher("arn:aws:states:r:1:stateMachine:m", fake_sfn(running=["job_9"]))
    with pytest.raises(MetisError) as exc:
        busy.start(spec())
    assert exc.value.code is ErrorCode.RATE_LIMITED


def test_launcher_duplicate_start_returns_existing_execution():
    sfn = fake_sfn(start_error=ExistsError())
    ref = StepFunctionsLauncher("arn:aws:states:r:1:stateMachine:m", sfn).start(spec())
    assert ref == "arn:aws:states:r:1:execution:m:job_1"
