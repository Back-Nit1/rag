"""Transcription execution: inline fixture decode, or bounded external ASR.

Cloud flow, one pattern only (spec: Step Functions Standard + ecs:runTask.sync):

1. ``capture.completed`` -> ``launch_transcription`` validates the source,
   records the job and starts a state-machine execution named after the job id
   (so a duplicate launch collapses). A launch is *not* a transcript.
2. The state machine runs the finite ASR task, which persists its result next
   to the source (``metis_speech.worker``), and then invokes
   ``finalize_transcription`` with the task outcome — success, failure, timeout
   or abort.
3. ``finalize_transcription`` re-derives the result key from the stored source,
   verifies the persisted result belongs to this job/recording/version, and
   only then commits one transcript revision and one ``transcript.finalized``.

Late results for a deleted or edited capture are discarded, never applied.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Protocol

from metis_contracts import (
    RETRYABLE_FAILURES,
    TERMINAL_JOB_STATES,
    ErrorCode,
    EventEnvelope,
    EventType,
    FailureCategory,
    JobState,
    MetisError,
    SourceLifecycle,
    TranscriptionJobInput,
    new_id,
    settings,
)
from metis_contracts.common import StrictModel
from metis_contracts.entities import (
    Job,
    SourceArtifact,
    SpeechMetadata,
    TranscriptRevision,
    TranscriptSegment,
)
from metis_contracts.events import TranscriptFinalizedPayload

from .dispatcher import DispatchContext, classify, outbox_event

log = logging.getLogger("metis.pipeline.transcription")


class AsrTaskOutcome(StrictModel):
    """What the state machine reports when the ASR task ends, however it ends."""

    user_id: str
    job_id: str
    status: Literal["succeeded", "failed", "timed_out", "aborted"]
    failure: str | None = None  # ErrorCode value from the worker's stderr result
    error_message: str | None = None


class AsrLauncher(Protocol):
    def start(self, job: TranscriptionJobInput) -> str:
        """Start bounded work; return an execution reference. Idempotent per job id."""
        ...

    def stop(self, ref: str) -> None: ...


class StepFunctionsLauncher:
    """Starts the speech state machine. The infrastructure agent owns the machine."""

    def __init__(self, state_machine_arn: str, client: Any | None = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("stepfunctions")
        self._arn = state_machine_arn
        self._sfn = client

    def start(self, job: TranscriptionJobInput) -> str:
        # ponytail: count-then-start is racy; the state machine's own
        # concurrency guard is the hard cap, this one defers the common case.
        running = self._sfn.list_executions(
            stateMachineArn=self._arn, statusFilter="RUNNING", maxResults=100
        )["executions"]
        if not any(e["name"] == job.job_id for e in running) and (
            len(running) >= settings().limits.speech_concurrency
        ):
            raise MetisError(ErrorCode.RATE_LIMITED, "speech concurrency cap reached; deferred")
        try:
            return str(
                self._sfn.start_execution(
                    stateMachineArn=self._arn, name=job.job_id, input=job.model_dump_json()
                )["executionArn"]
            )
        except self._sfn.exceptions.ExecutionAlreadyExists:
            return self._arn.replace(":stateMachine:", ":execution:") + ":" + job.job_id
        except self._sfn.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            kind = ErrorCode.RATE_LIMITED if "Throttl" in code else ErrorCode.PROVIDER_UNAVAILABLE
            raise MetisError(kind, f"speech launch failed: {code}") from exc

    def stop(self, ref: str) -> None:
        try:
            self._sfn.stop_execution(executionArn=ref, cause="capture canceled or superseded")
        except Exception as exc:  # best effort; a late result is discarded anyway
            log.warning("could not stop %s: %s", ref, exc)


def build_asr_launcher() -> StepFunctionsLauncher | None:
    cfg = settings()
    if not cfg.is_cloud:
        return None
    arn = cfg.aws.speech_state_machine_arn
    if not arn:
        raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "METIS_SPEECH_STATE_MACHINE_ARN is not set")
    return StepFunctionsLauncher(arn)


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #


def _source_for(envelope: EventEnvelope, context: DispatchContext) -> SourceArtifact:
    state = context.state_store
    source: SourceArtifact | None = state.get(envelope.user_id, "source", envelope.target_id or "")
    if source is None:
        raise MetisError(ErrorCode.TOMBSTONED, "the capture was deleted before transcription")
    if source.current_version != envelope.target_revision:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            f"capture moved to version {source.current_version}; this job is superseded",
        )
    if source.object_ref is None:
        raise MetisError(ErrorCode.VALIDATION_FAILED, "audio capture has no object reference")
    if not context.source_store.owns_key(envelope.user_id, source.object_ref.key):
        raise MetisError(ErrorCode.FORBIDDEN, "object key is outside the owner's prefix")
    return source


def _job_for(
    envelope: EventEnvelope, context: DispatchContext, source: SourceArtifact, state: JobState
) -> Job:
    """Existing job (duplicate completion / retry) or a new one."""
    job_id = str(envelope.payload.get("job_id") or new_id("job"))
    existing: Job | None = context.state_store.get(envelope.user_id, "job", job_id)
    if existing is not None:
        return existing
    now = context.clock.now()
    job = Job(
        id=job_id,
        user_id=envelope.user_id,
        created_at=now,
        updated_at=now,
        job_type="transcribe",
        state=state,
        target_id=source.id,
        target_revision=source.current_version,
        correlation_id=envelope.correlation_id,
        idempotency_key=envelope.idempotency_key,
    )
    with context.state_store.transaction(envelope.user_id) as tx:
        stored: Job = tx.put("job", job)
    return stored


def _job_input(source: SourceArtifact, job: Job) -> TranscriptionJobInput:
    cfg = settings()
    if source.object_ref is None:
        raise MetisError(ErrorCode.VALIDATION_FAILED, "audio capture has no object reference")
    return TranscriptionJobInput(
        recording_id=source.id,
        user_id=job.user_id,
        source_version=source.current_version,
        s3_bucket=source.object_ref.bucket,
        s3_key=source.object_ref.key,
        model_id=cfg.speech.model_id,
        max_audio_seconds=cfg.limits.max_audio_seconds,
        idempotency_key=job.idempotency_key,
        correlation_id=job.correlation_id,
        job_id=job.id,
    )


def _end(
    context: DispatchContext, job: Job, state: JobState, category: FailureCategory | None, msg: str
) -> Job:
    """Move a job to a terminal state once; never overwrite an earlier terminal one."""
    fresh: Job | None = context.state_store.get(job.user_id, "job", job.id)
    if fresh is not None and fresh.state in TERMINAL_JOB_STATES:
        return fresh
    ended: Job = context.state_store.set_job_state(
        job.id,
        user_id=job.user_id,
        state=state,
        failure=category if category else None,
        error_message=msg,
    )
    return ended


def _mark_source_failed(context: DispatchContext, user_id: str, source_id: str, version: int) -> None:
    source: SourceArtifact | None = context.state_store.get(user_id, "source", source_id)
    if source is None or source.current_version != version or source.lifecycle is SourceLifecycle.FAILED:
        return
    with context.state_store.transaction(user_id) as tx:
        tx.put(
            "source",
            source.model_copy(update={"lifecycle": SourceLifecycle.FAILED}),
            expected_revision=source.revision,
        )


def commit_transcript(
    context: DispatchContext,
    envelope: EventEnvelope,
    source: SourceArtifact,
    job: Job,
    text: str,
    segments: list[TranscriptSegment],
    meta: dict[str, Any],
) -> None:
    """Persist one transcript revision, one finalized event, and the READY job."""
    state = context.state_store
    state.record_speech_usage(
        envelope.user_id,
        local_date=context.clock.today_local(source.capture_timezone),
        audio_seconds=float(meta.get("audio_seconds", 0.0)),
    )

    # Save the transcript BEFORE extraction: speech errors affect dates, names
    # and intent, so the user must be able to correct it first.
    known = {k: v for k, v in meta.items() if k in SpeechMetadata.model_fields}
    revision = TranscriptRevision(
        source_version=source.current_version,
        text=text,
        asr_text=text,
        edited_by_user=False,
        segments=segments,
        speech=SpeechMetadata.model_validate(known),
        created_at=context.clock.now(),
    )
    updated = source.model_copy(
        update={
            "lifecycle": SourceLifecycle.READY,
            "transcripts": [*source.transcripts, revision],
            "duration_seconds": float(meta.get("audio_seconds", 0.0)) or source.duration_seconds,
        }
    )
    with state.transaction(envelope.user_id) as tx:
        stored = tx.put("source", updated, expected_revision=source.revision)
        if text.strip():
            tx.append_outbox(
                outbox_event(
                    envelope,
                    event_type=EventType.TRANSCRIPT_FINALIZED,
                    target_id=stored.id,
                    target_revision=stored.current_version,
                    payload=TranscriptFinalizedPayload(
                        recording_id=stored.id,
                        source_version=stored.current_version,
                        text_length=len(text),
                    ).model_dump(mode="json"),
                    # Exactly one logical transcript.finalized per accepted
                    # text revision, no matter how many retries happened.
                    idempotency_key=f"transcript:{stored.id}:{stored.current_version}",
                    clock=context.clock,
                )
            )

    state.set_job_state(
        job.id,
        user_id=envelope.user_id,
        state=JobState.READY,
        result_refs={"source_id": source.id, "source_version": str(source.current_version)},
        metrics={
            "audio_seconds": float(meta.get("audio_seconds", 0.0)),
            "decode_seconds": float(meta.get("decode_seconds", 0.0)),
            "model_load_seconds": float(meta.get("model_load_seconds", 0.0) or 0.0),
        },
    )
    if not text.strip():
        log.info("source %s decoded to no speech; nothing downstream was enqueued", source.id)


# --------------------------------------------------------------------------- #
# local / fixture: inline
# --------------------------------------------------------------------------- #


def transcribe_inline(envelope: EventEnvelope, context: DispatchContext) -> None:
    source = _source_for(envelope, context)
    job = _job_for(envelope, context, source, JobState.TRANSCRIBING)
    assert source.object_ref is not None  # _source_for guarantees it
    if job.state in TERMINAL_JOB_STATES:
        return  # duplicate completion; the first run already decided
    if job.state is not JobState.TRANSCRIBING:
        job = context.state_store.set_job_state(job.id, user_id=envelope.user_id, state=JobState.TRANSCRIBING)
    if context.transcriber is None:
        raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "no transcriber is configured")

    spec = _job_input(source, job)
    body = context.source_store.get_bytes(source.object_ref)
    text, segments, meta = context.transcriber.transcribe(
        body,
        language=spec.language or settings().speech.language,
        glossary=tuple(spec.glossary),
        max_seconds=spec.max_audio_seconds,
    )
    commit_transcript(context, envelope, source, job, text, segments, meta)


# --------------------------------------------------------------------------- #
# cloud: launch
# --------------------------------------------------------------------------- #


def launch_transcription(envelope: EventEnvelope, context: DispatchContext) -> None:
    state = context.state_store
    launcher = context.asr_launcher
    if launcher is None:
        raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "no ASR launcher is configured")
    source = _source_for(envelope, context)
    assert source.object_ref is not None  # _source_for guarantees it
    job = _job_for(envelope, context, source, JobState.QUEUED)
    if job.state in TERMINAL_JOB_STATES or "execution_ref" in job.result_refs:
        return  # duplicate completion: never launch a second task for one job

    limits = settings().limits
    try:
        head = context.source_store.head(source.object_ref)
        if head is None:
            raise MetisError(ErrorCode.OBJECT_MISSING, "the recording is not stored")
        if (head.size_bytes or 0) > limits.max_upload_bytes:
            raise MetisError(ErrorCode.UPLOAD_TOO_LARGE, f"stored object is {head.size_bytes} bytes")
        if (head.size_bytes or 0) == 0 or (
            head.content_type and not limits.audio_type_allowed(head.content_type)
        ):
            raise MetisError(ErrorCode.UNSUPPORTED_MEDIA, "unsupported or empty recording")

        state.set_job_state(job.id, user_id=job.user_id, state=JobState.STARTING)
        ref = launcher.start(_job_input(source, job))
    except MetisError as exc:
        category = classify(exc)
        attempts = job.attempts + 1
        if category in RETRYABLE_FAILURES and attempts < limits.job_max_attempts:
            # Let the outbox retry with backoff; the user sees a queued job.
            current: Job | None = state.get(job.user_id, "job", job.id)
            if current is None:
                raise
            with state.transaction(job.user_id) as tx:
                tx.put(
                    "job",
                    current.model_copy(
                        update={
                            "state": JobState.QUEUED,
                            "attempts": attempts,
                            "error_message": f"launch deferred: {exc.message}"[:1000],
                        }
                    ),
                    expected_revision=current.revision,
                )
            raise
        _end(context, job, JobState.FAILED, category, exc.message)
        _mark_source_failed(context, job.user_id, source.id, source.current_version)
        if category in RETRYABLE_FAILURES:
            raise  # dead-letter with the real cause
        return  # permanent: surfaced on the job, nothing to retry

    state.set_job_state(
        job.id, user_id=job.user_id, state=JobState.TRANSCRIBING, result_refs={"execution_ref": ref}
    )


# --------------------------------------------------------------------------- #
# cloud: finalize (invoked by the state machine after the task ends)
# --------------------------------------------------------------------------- #


def finalize_transcription(context: DispatchContext, outcome: AsrTaskOutcome) -> Job:
    """Verify and apply the ASR task result. Safe to call more than once."""
    state = context.state_store
    job: Job | None = state.get(outcome.user_id, "job", outcome.job_id)
    if job is None or job.job_type != "transcribe":
        raise MetisError(ErrorCode.NOT_FOUND, f"job {outcome.job_id} not found")
    if job.state in TERMINAL_JOB_STATES:
        return job  # duplicate task completion, or already canceled

    source: SourceArtifact | None = state.get(job.user_id, "source", job.target_id or "")
    if source is None or source.current_version != job.target_revision:
        return _end(
            context,
            job,
            JobState.CANCELED,
            FailureCategory.SUPERSEDED,
            "the capture was deleted or edited; this result was discarded",
        )

    if outcome.status != "succeeded":
        return _fail_outcome(context, job, source, outcome)

    envelope = EventEnvelope(
        event_id=new_id("evt"),
        event_type=EventType.CAPTURE_COMPLETED,
        user_id=job.user_id,
        correlation_id=job.correlation_id,
        target_id=source.id,
        target_revision=source.current_version,
        idempotency_key=job.idempotency_key,
        payload={"job_id": job.id},
    )
    try:
        text, segments, meta = _read_result(context, job, source)
        commit_transcript(context, envelope, source, job, text, segments, meta)
    except MetisError as exc:
        if exc.code is ErrorCode.REVISION_CONFLICT:  # source moved during commit
            return _end(context, job, JobState.CANCELED, FailureCategory.SUPERSEDED, exc.message)
        _mark_source_failed(context, job.user_id, source.id, source.current_version)
        return _end(context, job, JobState.FAILED, classify(exc), exc.message)
    final: Job | None = state.get(job.user_id, "job", job.id)
    if final is None:
        raise MetisError(ErrorCode.NOT_FOUND, f"job {job.id} vanished")
    return final


def _fail_outcome(context: DispatchContext, job: Job, source: SourceArtifact, outcome: AsrTaskOutcome) -> Job:
    if outcome.status == "aborted":
        return _end(context, job, JobState.CANCELED, None, "transcription was canceled")
    if outcome.status == "timed_out":
        category, msg = (
            FailureCategory.TRANSIENT,
            (f"transcription exceeded {settings().limits.speech_wall_clock_seconds}s; try again"),
        )
    else:
        try:
            category = classify(MetisError(ErrorCode(outcome.failure or ""), ""))
        except ValueError:
            category = FailureCategory.INTERNAL
        msg = outcome.error_message or "transcription failed"
    _mark_source_failed(context, job.user_id, source.id, source.current_version)
    return _end(context, job, JobState.FAILED, category, msg)


def _read_result(
    context: DispatchContext, job: Job, source: SourceArtifact
) -> tuple[str, list[TranscriptSegment], dict[str, Any]]:
    from metis_contracts import ObjectRef
    from metis_speech.worker import result_key

    spec = _job_input(source, job)
    # The key is derived from the stored source, never taken from the task output.
    key = result_key(spec)
    if not context.source_store.owns_key(job.user_id, key):
        raise MetisError(ErrorCode.FORBIDDEN, "result key is outside the owner's prefix")
    ref = ObjectRef(bucket=spec.s3_bucket, key=key)
    if context.source_store.head(ref) is None:
        raise MetisError(ErrorCode.OBJECT_MISSING, "the worker exited without persisting a result")
    try:
        data = json.loads(context.source_store.get_bytes(ref))
        text = data["text"]
        segments = [TranscriptSegment.model_validate(s) for s in data["segments"]]
        meta = dict(data["speech"])
        claimed = (data["job_id"], data["recording_id"], data["source_version"])
    except (ValueError, KeyError, TypeError) as exc:
        raise MetisError(ErrorCode.VALIDATION_FAILED, f"worker result is malformed: {exc}") from exc
    if claimed != (job.id, source.id, source.current_version) or not isinstance(text, str):
        raise MetisError(ErrorCode.FORBIDDEN, "worker result does not belong to this job")
    if float(meta.get("audio_seconds", 0.0)) > spec.max_audio_seconds:
        raise MetisError(ErrorCode.LIMIT_EXCEEDED, "decoded audio exceeds the maximum duration")
    return text, segments, meta
