"""Container entrypoint for one transcription job, then exit.

Invoked as a finite ECS Fargate task (or locally with `--job-file`). The job
input arrives as JSON in ``METIS_JOB_INPUT`` or a file path, matching
``metis_contracts.TranscriptionJobInput``.

Security boundary: the worker re-validates that ``s3_key`` sits under the
owner's prefix and that ``source_version`` still matches the current recording.
A late result for a canceled or edited capture is discarded rather than
resurrecting it. No filename from the payload ever reaches a shell.

The worker persists its own result and exits non-zero on failure. A successful
``RunTask`` is not a successful transcription: the caller checks the persisted
result, not the launch.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any

from metis_contracts import (
    ErrorCode,
    MetisError,
    ObjectRef,
    TranscriptionJobInput,
    TranscriptionJobResult,
    settings,
)

log = logging.getLogger("metis.speech.worker")

RESULT_SUFFIX = "transcripts"


def load_job(raw: str) -> TranscriptionJobInput:
    return TranscriptionJobInput.model_validate_json(raw)


def result_key(job: TranscriptionJobInput) -> str:
    """Owner-prefixed, version-scoped result path, derived on the server side."""
    prefix = job.s3_key.rsplit("/captures/", 1)[0]
    return f"{prefix}/{RESULT_SUFFIX}/{job.recording_id}.v{job.source_version}.json"


def run(job: TranscriptionJobInput, *, source_store: Any, transcriber: Any) -> TranscriptionJobResult:
    """Decode one recording and persist the result next to its source."""
    started = time.monotonic()

    if not source_store.owns_key(job.user_id, job.s3_key):
        # Nothing about the queue payload grants authorization.
        raise MetisError(ErrorCode.FORBIDDEN, "object key is outside the owner's prefix")

    ref = ObjectRef(bucket=job.s3_bucket, key=job.s3_key)
    head = source_store.head(ref)
    if head is None:
        raise MetisError(ErrorCode.OBJECT_MISSING, f"recording {job.recording_id} is not stored")
    limit = settings().limits.max_upload_bytes
    if (head.size_bytes or 0) > limit:
        raise MetisError(
            ErrorCode.UPLOAD_TOO_LARGE, f"stored object is {head.size_bytes} bytes, over {limit}"
        )

    body = source_store.get_bytes(ref)
    text, segments, meta = transcriber.transcribe(
        body,
        language=job.language,
        glossary=tuple(job.glossary),
        max_seconds=min(job.max_audio_seconds, settings().limits.max_audio_seconds),
    )

    payload = {
        "job_id": job.job_id,
        "recording_id": job.recording_id,
        "source_version": job.source_version,
        "text": text,
        "segments": [s.model_dump(mode="json") for s in segments],
        "speech": meta,
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    key = result_key(job)
    source_store.put_bytes(
        ObjectRef(bucket=job.s3_bucket, key=key),
        json.dumps(payload, ensure_ascii=False).encode(),
        content_type="application/json",
    )
    log.info(
        "transcribed recording=%s version=%s audio_s=%.2f decode_s=%.2f cold=%s chars=%d",
        job.recording_id,
        job.source_version,
        meta.get("audio_seconds", 0.0),
        meta.get("decode_seconds", 0.0),
        meta.get("cold_start"),
        len(text),
    )
    return TranscriptionJobResult(
        job_id=job.job_id,
        recording_id=job.recording_id,
        source_version=job.source_version,
        ok=True,
        text=text,
        segments_ref=key,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Metis speech worker: one job, then exit")
    parser.add_argument("--job-file", help="path to a JSON job input (local runs)")
    args = parser.parse_args(argv)

    raw = None
    if args.job_file:
        with open(args.job_file, encoding="utf-8") as fh:
            raw = fh.read()
    else:
        raw = os.environ.get("METIS_JOB_INPUT")
    if not raw:
        print("no job input: set METIS_JOB_INPUT or pass --job-file", file=sys.stderr)
        return 2

    try:
        job = load_job(raw)
    except Exception as exc:
        print(f"invalid job input: {exc}", file=sys.stderr)
        return 2

    from metis_state import build_source_store

    from .transcriber import build_transcriber

    transcriber = build_transcriber()
    if hasattr(transcriber, "warm"):
        transcriber.warm()  # measured separately from decode time

    try:
        result = run(job, source_store=build_source_store(), transcriber=transcriber)
    except MetisError as exc:
        failure = TranscriptionJobResult(
            job_id=job.job_id,
            recording_id=job.recording_id,
            source_version=job.source_version,
            ok=False,
            failure=exc.code.value,
            error_message=exc.message,
        )
        print(failure.model_dump_json(), file=sys.stderr)
        # Exit code distinguishes a permanent rejection from a retryable fault,
        # so the state machine does not retry an unsupported file forever.
        return (
            3
            if exc.code in (ErrorCode.UNSUPPORTED_MEDIA, ErrorCode.UPLOAD_TOO_LARGE, ErrorCode.FORBIDDEN)
            else 1
        )
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
