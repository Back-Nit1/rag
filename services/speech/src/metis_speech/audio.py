"""Audio validation and conversion for the ASR worker.

Everything here treats the input as hostile: container metadata is untrusted,
the decoded duration is what counts, decode work is bounded, and no filename or
URL from a user ever reaches a shell.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

from metis_contracts import ErrorCode, MetisError

TARGET_SAMPLE_RATE = 16_000  # what Whisper expects
MAX_DECODE_FRAMES = 60 * 60 * TARGET_SAMPLE_RATE  # hard ceiling: one hour of samples


@dataclass(frozen=True)
class DecodedAudio:
    samples: list[float]
    sample_rate: int
    duration_seconds: float
    channels_in: int
    codec: str


def validate_envelope(body: bytes, *, max_bytes: int) -> None:
    """Cheap checks before any decoder touches the bytes."""
    if not body:
        raise MetisError(ErrorCode.UNSUPPORTED_MEDIA, "the recording is empty")
    if len(body) > max_bytes:
        raise MetisError(
            ErrorCode.UPLOAD_TOO_LARGE,
            f"recording is {len(body)} bytes, over the {max_bytes} byte limit",
        )


def decode_to_mono16k(body: bytes, *, max_seconds: int) -> DecodedAudio:
    """Decode to mono 16 kHz float samples using PyAV.

    Raises ``UNSUPPORTED_MEDIA`` for anything corrupt or undecodable and
    ``LIMIT_EXCEEDED`` when the *decoded* duration exceeds ``max_seconds`` —
    a declared duration in the container header is never trusted.
    """
    try:
        import av
    except ImportError as exc:  # pragma: no cover - container-only dependency
        raise MetisError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "the audio decoder is not installed; this process cannot transcribe",
        ) from exc

    frame_budget = min(max_seconds, MAX_DECODE_FRAMES // TARGET_SAMPLE_RATE) * TARGET_SAMPLE_RATE
    samples: list[float] = []
    channels_in = 0
    codec = "unknown"

    try:
        with av.open(io.BytesIO(body)) as container:
            streams = [s for s in container.streams if s.type == "audio"]
            if not streams:
                raise MetisError(ErrorCode.UNSUPPORTED_MEDIA, "the file contains no audio stream")
            stream = streams[0]
            channels_in = stream.codec_context.channels or 1
            codec = stream.codec_context.name or "unknown"
            resampler = av.AudioResampler(format="flt", layout="mono", rate=TARGET_SAMPLE_RATE)
            for frame in container.decode(stream):
                for resampled in resampler.resample(frame):
                    chunk = resampled.to_ndarray().reshape(-1)
                    samples.extend(float(x) for x in chunk)
                    if len(samples) > frame_budget:
                        raise MetisError(
                            ErrorCode.LIMIT_EXCEEDED,
                            f"decoded audio exceeds the {max_seconds}s limit",
                        )
    except MetisError:
        raise
    except Exception as exc:  # av raises a family of errors; all mean "unusable"
        raise MetisError(ErrorCode.UNSUPPORTED_MEDIA, f"could not decode the recording: {exc}") from exc

    duration = len(samples) / TARGET_SAMPLE_RATE
    if duration <= 0:
        raise MetisError(ErrorCode.UNSUPPORTED_MEDIA, "the recording decoded to zero samples")
    return DecodedAudio(
        samples=samples,
        sample_rate=TARGET_SAMPLE_RATE,
        duration_seconds=duration,
        channels_in=channels_in,
        codec=codec,
    )


def looks_like_silence(audio: DecodedAudio, *, rms_threshold: float = 1e-4) -> bool:
    """A no-speech fixture must create no task and no reminder, so the worker
    needs to recognise silence before extraction is ever enqueued."""
    if not audio.samples:
        return True
    mean_square = sum(s * s for s in audio.samples) / len(audio.samples)
    return math.sqrt(mean_square) < rms_threshold
