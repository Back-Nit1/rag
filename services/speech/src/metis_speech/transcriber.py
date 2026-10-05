"""SpeechTranscriber implementations.

`FasterWhisperTranscriber` is the production decoder: pretrained open-source
Whisper weights running through CTranslate2 on CPU in INT8. It needs no speech
provider API key. `FixtureTranscriber` is the deterministic local stand-in and
always reports ``adapter_mode="fixture"`` so a mock can never be mistaken for a
completed production transcription.

Both are deterministic adapters. Neither reasons about the audio, calls tools,
or rewrites what was said: an LLM must never silently "fix" a date, a name, a
negation or a commitment in a transcript.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from metis_contracts import AdapterMode, ErrorCode, MetisError, ObjectRef, TranscriptSegment, settings

from .audio import decode_to_mono16k, looks_like_silence, validate_envelope

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures"


def _metadata(
    *,
    model_id: str,
    adapter_mode: AdapterMode,
    decode_seconds: float,
    audio_seconds: float,
    language: str,
    language_detected: str | None = None,
    cold_start: bool | None = None,
    library_version: str = "n/a",
    compute_type: str = "n/a",
) -> dict[str, Any]:
    cfg = settings().speech
    return {
        "model_id": model_id,
        "artifact_revision": cfg.artifact_revision,
        "library_version": library_version,
        "compute_type": compute_type,
        "beam_size": cfg.beam_size,
        "temperature": cfg.temperature,
        "vad_enabled": cfg.vad_enabled,
        "language": language,
        "language_detected": language_detected,
        "adapter_mode": adapter_mode.value,
        "decode_seconds": round(decode_seconds, 3),
        "audio_seconds": round(audio_seconds, 3),
        "cold_start": cold_start,
    }


class FixtureTranscriber:
    """Deterministic transcriber for local mode and tests.

    Resolution order:

    1. an entry in ``fixtures/transcripts.json`` keyed by the object key's stem
    2. an entry keyed by the sha256 of the audio bytes
    3. a stable synthetic line derived from the checksum

    It never pretends to be a production result, and it still enforces the
    size limit so the local path exercises the same validation.
    """

    mode = AdapterMode.FIXTURE

    def __init__(self, fixture_file: Path | None = None) -> None:
        self._path = fixture_file or (FIXTURE_DIR / "transcripts.json")
        self._table: dict[str, str] = {}
        if self._path.is_file():
            self._table = json.loads(self._path.read_text()).get("by_key", {})

    def transcribe(
        self,
        audio: bytes | ObjectRef,
        *,
        language: str | None = None,
        glossary: tuple[str, ...] = (),
        max_seconds: int = 300,
    ) -> tuple[str, list[TranscriptSegment], dict[str, Any]]:
        started = time.monotonic()
        if isinstance(audio, ObjectRef):
            key_stem = Path(audio.key).stem
            body = b""
        else:
            body = audio
            validate_envelope(body, max_bytes=settings().limits.max_upload_bytes)
            key_stem = hashlib.sha256(body).hexdigest()[:16]

        text = self._table.get(key_stem)
        if text is None:
            digest = hashlib.sha256(body or key_stem.encode()).hexdigest()[:8]
            text = f"Fixture transcript {digest}."

        # One segment per sentence keeps downstream span offsets meaningful.
        segments: list[TranscriptSegment] = []
        cursor_ms = 0
        for index, piece in enumerate(s for s in text.split(". ") if s):
            sentence = piece if piece.endswith(".") else piece + "."
            duration_ms = max(400, len(sentence) * 60)
            segments.append(
                TranscriptSegment(
                    index=index, start_ms=cursor_ms, end_ms=cursor_ms + duration_ms, text=sentence
                )
            )
            cursor_ms += duration_ms

        audio_seconds = cursor_ms / 1000
        meta = _metadata(
            model_id="fixture",
            adapter_mode=AdapterMode.FIXTURE,
            decode_seconds=time.monotonic() - started,
            audio_seconds=audio_seconds,
            language=language or "en",
        )
        return text, segments, meta


class FasterWhisperTranscriber:
    """Self-hosted faster-whisper / CTranslate2 decoder.

    The model is loaded from a directory baked into the container image (or a
    controlled artifact cache) — never re-downloaded from the public internet
    per transcription. One decode at a time per container; concurrency is
    capped by the pipeline, not by threads here.
    """

    mode = AdapterMode.CLOUD

    def __init__(self, model_dir: str | None = None, model_id: str | None = None) -> None:
        cfg = settings().speech
        self._model_dir = model_dir or cfg.model_dir
        self._model_id = model_id or cfg.model_id
        self._model: Any | None = None
        self._library_version = "unknown"
        self._cold = True

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            import faster_whisper
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - container-only dependency
            raise MetisError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "faster-whisper is not installed in this process; install the 'asr' extra "
                "or run the speech container",
            ) from exc

        cfg = settings().speech
        local = Path(self._model_dir) / self._model_id
        source = str(local) if local.is_dir() else self._model_id
        if source == self._model_id:
            # Surfaced rather than silently downloading at request time.
            raise MetisError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"model artifacts for {self._model_id} are not present in {self._model_dir}; "
                "bake them into the image or mount the artifact cache",
            )
        self._library_version = getattr(faster_whisper, "__version__", "unknown")
        self._model = WhisperModel(
            source,
            device="cpu",
            compute_type=cfg.compute_type,
            cpu_threads=cfg.cpu_threads or 0,
            local_files_only=True,
        )
        return self._model

    def warm(self) -> None:
        """Load the model before claiming a job so cold-start cost is measured
        separately from decode time."""
        self._load()

    def transcribe(
        self,
        audio: bytes | ObjectRef,
        *,
        language: str | None = None,
        glossary: tuple[str, ...] = (),
        max_seconds: int = 300,
    ) -> tuple[str, list[TranscriptSegment], dict[str, Any]]:
        if isinstance(audio, ObjectRef):
            raise MetisError(
                ErrorCode.VALIDATION_FAILED,
                "the worker resolves object references before decoding; pass bytes",
            )
        cfg = settings().speech
        validate_envelope(audio, max_bytes=settings().limits.max_upload_bytes)
        decoded = decode_to_mono16k(audio, max_seconds=max_seconds)

        if looks_like_silence(decoded):
            # An empty/no-speech recording must produce no text, so nothing
            # downstream can create a task or a reminder from it.
            meta = _metadata(
                model_id=self._model_id,
                adapter_mode=AdapterMode.CLOUD,
                decode_seconds=0.0,
                audio_seconds=decoded.duration_seconds,
                language=language or cfg.language or "en",
                library_version=self._library_version,
                compute_type=cfg.compute_type,
                cold_start=self._cold,
            )
            meta["no_speech"] = True
            return "", [], meta

        was_cold = self._cold
        load_started = time.monotonic()
        model = self._load()
        load_seconds = time.monotonic() - load_started
        self._cold = False

        # A glossary biases spelling of known names; it never replaces the raw
        # output and must not turn an expected name into fabricated speech.
        prompt = ", ".join(glossary[:32]) if glossary else None

        decode_started = time.monotonic()
        try:
            import numpy as np

            samples = np.asarray(decoded.samples, dtype="float32")
            raw_segments, info = model.transcribe(
                samples,
                language=language or cfg.language,
                beam_size=cfg.beam_size,
                temperature=cfg.temperature,
                vad_filter=cfg.vad_enabled,
                condition_on_previous_text=cfg.condition_on_previous_text,
                initial_prompt=prompt,
                word_timestamps=False,  # not computed: no UX consumes word timing yet
            )
            segments: list[TranscriptSegment] = []
            for index, seg in enumerate(raw_segments):
                segments.append(
                    TranscriptSegment(
                        index=index,
                        start_ms=int(seg.start * 1000),
                        end_ms=int(seg.end * 1000),
                        text=seg.text.strip(),
                    )
                )
        except MetisError:
            raise
        except Exception as exc:
            raise MetisError(ErrorCode.INTERNAL, f"decoding failed: {exc}") from exc

        decode_seconds = time.monotonic() - decode_started
        text = " ".join(s.text for s in segments).strip()
        meta = _metadata(
            model_id=self._model_id,
            adapter_mode=AdapterMode.CLOUD,
            decode_seconds=decode_seconds,
            audio_seconds=decoded.duration_seconds,
            language=language or cfg.language or "en",
            language_detected=getattr(info, "language", None),
            cold_start=was_cold,
            library_version=self._library_version,
            compute_type=cfg.compute_type,
        )
        meta["model_load_seconds"] = round(load_seconds, 3)
        meta["input_codec"] = decoded.codec
        return text, segments, meta


def build_transcriber() -> Any:
    """Fixture transcriber locally; the real decoder in hybrid/cloud mode."""
    cfg = settings()
    if cfg.uses_real_inference:
        return FasterWhisperTranscriber()
    return FixtureTranscriber()
