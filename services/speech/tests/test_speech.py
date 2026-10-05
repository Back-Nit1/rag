"""Speech adapter and evaluation invariants (no real audio decoding here).

The one test that proves actual neural decoding lives in
`test_container_decode.py` and is marked `integration`.
"""

from __future__ import annotations

import pytest
from metis_contracts import AdapterMode, ErrorCode, MetisError, ObjectRef, TranscriptionJobInput
from metis_speech.audio import DecodedAudio, looks_like_silence, validate_envelope
from metis_speech.evaluate import evaluate, normalize, wer
from metis_speech.transcriber import FixtureTranscriber
from metis_speech.worker import result_key, run


def test_empty_recording_is_unsupported_media():
    with pytest.raises(MetisError) as exc:
        validate_envelope(b"", max_bytes=1024)
    assert exc.value.code is ErrorCode.UNSUPPORTED_MEDIA


def test_oversize_recording_is_rejected_before_decoding():
    with pytest.raises(MetisError) as exc:
        validate_envelope(b"x" * 2048, max_bytes=1024)
    assert exc.value.code is ErrorCode.UPLOAD_TOO_LARGE


def test_silence_is_detected():
    quiet = DecodedAudio(samples=[0.0] * 1000, sample_rate=16000, duration_seconds=0.06,
                         channels_in=1, codec="opus")
    loud = DecodedAudio(samples=[0.4, -0.4] * 500, sample_rate=16000, duration_seconds=0.06,
                        channels_in=1, codec="opus")
    assert looks_like_silence(quiet) is True
    assert looks_like_silence(loud) is False


def test_fixture_transcriber_never_claims_to_be_production():
    text, segments, meta = FixtureTranscriber().transcribe(
        ObjectRef(bucket="local", key="users/abc/captures/src_demo_bloomberg.webm")
    )
    assert "Bloomberg" in text
    assert meta["adapter_mode"] == AdapterMode.FIXTURE.value
    assert meta["model_id"] == "fixture"
    assert segments and segments[0].start_ms == 0


def test_fixture_transcriber_is_deterministic():
    t = FixtureTranscriber()
    first, _, _ = t.transcribe(b"same bytes")
    second, _, _ = t.transcribe(b"same bytes")
    assert first == second


def test_segments_are_ordered_and_non_overlapping():
    _, segments, _ = FixtureTranscriber().transcribe(
        ObjectRef(bucket="local", key="users/abc/captures/src_demo_cerno.webm")
    )
    assert len(segments) > 1
    for previous, current in zip(segments, segments[1:], strict=False):
        assert current.index == previous.index + 1
        assert current.start_ms >= previous.end_ms


def test_silence_fixture_produces_no_text():
    text, segments, _ = FixtureTranscriber().transcribe(
        ObjectRef(bucket="local", key="users/abc/captures/src_demo_silence.webm")
    )
    assert text == "" and segments == []


# --- worker boundary -------------------------------------------------------- #


class _Store:
    def __init__(self, *, owns: bool = True, size: int = 100) -> None:
        self._owns = owns
        self._size = size
        self.written: dict[str, bytes] = {}

    def owns_key(self, user_id, key):
        return self._owns

    def head(self, ref):
        return ref.model_copy(update={"size_bytes": self._size}) if self._size >= 0 else None

    def get_bytes(self, ref):
        return b"audio-bytes"

    def put_bytes(self, ref, body, *, content_type):
        self.written[ref.key] = body
        return ref


def job(**kw) -> TranscriptionJobInput:
    base = {
        "recording_id": "src_1",
        "user_id": "user_a",
        "source_version": 2,
        "s3_bucket": "b",
        "s3_key": "users/abc/captures/src_1.webm",
        "idempotency_key": "transcribe:src_1:2",
        "correlation_id": "corr_1",
        "job_id": "job_1",
    }
    base.update(kw)
    return TranscriptionJobInput(**base)


def test_worker_rejects_a_key_outside_the_owner_prefix():
    with pytest.raises(MetisError) as exc:
        run(job(), source_store=_Store(owns=False), transcriber=FixtureTranscriber())
    assert exc.value.code is ErrorCode.FORBIDDEN


def test_worker_rejects_a_missing_object():
    with pytest.raises(MetisError) as exc:
        run(job(), source_store=_Store(size=-1), transcriber=FixtureTranscriber())
    assert exc.value.code is ErrorCode.OBJECT_MISSING


def test_worker_rechecks_the_stored_size_against_the_limit():
    with pytest.raises(MetisError) as exc:
        run(job(), source_store=_Store(size=99_999_999), transcriber=FixtureTranscriber())
    assert exc.value.code is ErrorCode.UPLOAD_TOO_LARGE


def test_worker_result_key_is_version_scoped_under_the_owner_prefix():
    key = result_key(job())
    assert key == "users/abc/transcripts/src_1.v2.json"


def test_worker_persists_its_own_result():
    store = _Store()
    result = run(job(), source_store=store, transcriber=FixtureTranscriber())
    assert result.ok and result.segments_ref in store.written


# --- evaluation ------------------------------------------------------------- #


def test_normalisation_is_case_and_punctuation_insensitive():
    assert normalize("The eighth, not the ninth!") == "the eighth not the ninth"


def test_wer_counts_substitutions():
    assert wer("I tried caching whole files", "I tried caching all files") == pytest.approx(0.2)


def test_a_dropped_negation_is_reported_even_when_wer_looks_fine():
    items = [{"id": "a", "split": "dev", "reference": "I am not going", "names": []}]
    report = evaluate(items, {"a": "I am going"})
    dev = report["splits"]["dev"]
    assert dev["negation_fidelity"] == 0.0
    assert dev["mean_wer"] < 0.3, "plain WER stays low while the meaning inverted"


def test_a_mangled_project_name_is_reported_separately():
    items = [{"id": "a", "split": "dev", "reference": "On Cerno the review re-reads files", "names": ["Cerno"]}]
    report = evaluate(items, {"a": "On Serno the review re-reads files"})
    assert report["splits"]["dev"]["name_fidelity"] == 0.0


def test_missing_hypotheses_are_listed_not_silently_scored():
    items = [{"id": "a", "split": "dev", "reference": "hello", "names": []}]
    report = evaluate(items, {})
    assert report["missing_hypotheses"] == ["a"]
    assert report["splits"] == {}


def test_dev_and_heldout_are_reported_separately():
    items = [
        {"id": "a", "split": "dev", "reference": "hello there", "names": []},
        {"id": "b", "split": "heldout", "reference": "goodbye now", "names": []},
    ]
    report = evaluate(items, {"a": "hello there", "b": "goodbye then"})
    assert set(report["splits"]) == {"dev", "heldout"}
    assert report["splits"]["dev"]["mean_wer"] == 0.0
    assert report["splits"]["heldout"]["mean_wer"] > 0.0
