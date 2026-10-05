"""The one test that proves real neural decoding, not a mock.

Marked `integration`: it needs the ASR extra installed (or the speech
container) plus a real browser-format recording. Mocks cannot catch a codec
failure, which is precisely why this exists.

    # record your own short clip first:
    #   services/speech/fixtures/audio/hello.webm
    uv run pytest -m integration services/speech
"""

from __future__ import annotations

from pathlib import Path

import pytest

AUDIO = Path(__file__).resolve().parents[1] / "fixtures" / "audio" / "hello.webm"


@pytest.mark.integration
def test_real_decode_produces_text_without_an_external_stt_credential():
    pytest.importorskip("faster_whisper", reason="install the 'asr' extra or use the container")
    pytest.importorskip("av")
    if not AUDIO.is_file():
        pytest.skip(f"record a browser-format clip at {AUDIO} to run this check")

    from metis_speech.transcriber import FasterWhisperTranscriber

    transcriber = FasterWhisperTranscriber()
    text, segments, meta = transcriber.transcribe(AUDIO.read_bytes(), max_seconds=60)

    assert text.strip(), "the decoder returned no text"
    assert segments and segments[0].end_ms > segments[0].start_ms
    assert meta["adapter_mode"] == "cloud"
    assert meta["cold_start"] is True
    assert meta["audio_seconds"] > 0
    # Proof the result is reproducible: the artifact revision is recorded.
    assert meta["artifact_revision"]


@pytest.mark.integration
def test_a_corrupt_file_fails_safely():
    pytest.importorskip("av")
    from metis_contracts import ErrorCode, MetisError
    from metis_speech.audio import decode_to_mono16k

    with pytest.raises(MetisError) as exc:
        decode_to_mono16k(b"this is not audio at all", max_seconds=60)
    assert exc.value.code is ErrorCode.UNSUPPORTED_MEDIA
