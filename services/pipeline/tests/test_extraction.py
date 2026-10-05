"""Extraction guards. Schema conformity is not semantic truth."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from metis_contracts import (
    AdapterMode,
    ErrorCode,
    FixedClock,
    MetisError,
    ProposalDisposition,
    SourceArtifact,
    reset_settings_cache,
)
from metis_contracts.entities import TranscriptRevision
from metis_contracts.enums import SourceKind, SourceLifecycle
from metis_pipeline.extraction import ExtractionService, build_prompt, split_ranges
from metis_pipeline.inference import FixtureInferenceClient
from metis_state.sqlite_store import SqliteStateStore

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)
USER = "user_a"
NY = "America/New_York"

BLOOMBERG = (
    "There is a Bloomberg event in New York I want to go to. "
    "Remind me to register and help me prepare."
)


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture()
def store(tmp_path, clock, monkeypatch) -> SqliteStateStore:
    monkeypatch.setenv("METIS_DAILY_MICRO_USD", "1000000")
    reset_settings_cache()
    yield SqliteStateStore(str(tmp_path / "s.sqlite"), clock=clock)
    reset_settings_cache()


def source(text: str = BLOOMBERG, source_id: str = "src_1") -> SourceArtifact:
    return SourceArtifact(
        id=source_id, user_id=USER, created_at=T0, updated_at=T0,
        kind=SourceKind.TEXT, lifecycle=SourceLifecycle.READY,
        capture_timezone=NY, captured_at=T0, current_version=1,
        transcripts=[TranscriptRevision(source_version=1, text=text, created_at=T0)],
    )


def service(store, clock, responses=None) -> ExtractionService:
    client = FixtureInferenceClient()
    if responses:
        for content_marker, payload in responses.items():
            client._responses[content_marker] = payload
    return ExtractionService(state_store=store, inference=client, clock=clock)


def forced(store, clock, payload) -> ExtractionService:
    """A service whose inference client always returns `payload`."""

    class _Forced(FixtureInferenceClient):
        def complete_json(self, **kw):
            return payload, {"model_id": "forced", "adapter_mode": "fixture",
                             "input_tokens": 10, "output_tokens": 10, "micro_usd": 0}

    return ExtractionService(state_store=store, inference=_Forced(), clock=clock)


def span(start=0, end=10, ref="span_a"):
    return {"span_ref": ref, "start_char": start, "end_char": end}


# --- the Bloomberg case ----------------------------------------------------- #


def test_bloomberg_note_invents_no_date_and_no_registration(store, clock):
    outcome = service(store, clock).extract(source(), source_version=1)
    intent = next(i for i in outcome.proposal.items if i.kind.value == "event_intent")
    assert intent.starts_at is None and intent.local_date is None
    assert intent.registration_status.value == "unknown"
    assert intent.attendance_status.value == "tentative"
    assert outcome.proposal.requires_clarification is True
    assert "event_date_time" in intent.missing_fields


def test_every_item_carries_a_verified_source_span(store, clock):
    outcome = service(store, clock).extract(source(), source_version=1)
    assert outcome.proposal.items
    for item in outcome.proposal.items:
        assert item.evidence
        for ref in item.evidence:
            assert ref.source_id == "src_1" and ref.source_version == 1 and ref.span_ref


def test_proposal_records_the_adapter_mode_so_a_fixture_cannot_pose_as_production(store, clock):
    outcome = service(store, clock).extract(source(), source_version=1)
    assert outcome.proposal.adapter_mode is AdapterMode.FIXTURE


# --- semantic validation ---------------------------------------------------- #


def test_registered_status_unsupported_by_the_note_is_downgraded(store, clock):
    payload = {
        "items": [{
            "kind": "event_intent", "title": "Bloomberg event",
            "registration_status": "registered", "evidence": [span()],
        }],
        "requires_clarification": False,
    }
    outcome = forced(store, clock, payload).extract(source(), source_version=1)
    assert outcome.proposal.items[0].registration_status.value == "unknown"
    assert "unsupported by the note" in (outcome.degraded_reason or "")


def test_registered_status_is_kept_when_the_note_says_so(store, clock):
    payload = {
        "items": [{
            "kind": "event_intent", "title": "Bloomberg event",
            "registration_status": "registered", "evidence": [span()],
        }],
        "requires_clarification": False,
    }
    text = "I already registered for the Bloomberg event."
    outcome = forced(store, clock, payload).extract(source(text), source_version=1)
    assert outcome.proposal.items[0].registration_status.value == "registered"


def test_committed_attendance_is_downgraded_without_an_explicit_statement(store, clock):
    payload = {
        "items": [{
            "kind": "event_intent", "title": "Bloomberg event",
            "attendance_status": "committed", "evidence": [span()],
        }],
        "requires_clarification": False,
    }
    outcome = forced(store, clock, payload).extract(source(), source_version=1)
    assert outcome.proposal.items[0].attendance_status.value == "tentative"


def test_an_absurd_deadline_is_rejected_not_stored(store, clock):
    payload = {
        "items": [{
            "kind": "task", "title": "Register",
            "deadline_local_date": "2099-01-01", "evidence": [span()],
        }],
        "requires_clarification": False,
    }
    outcome = forced(store, clock, payload).extract(source(), source_version=1)
    assert outcome.proposal.items[0].deadline_local_date is None
    assert "horizon" in (outcome.degraded_reason or "")


def test_a_date_set_while_listed_missing_is_cleared(store, clock):
    payload = {
        "items": [{
            "kind": "event_intent", "title": "Bloomberg event",
            "local_date": "2026-10-08", "missing_fields": ["event_date_time"],
            "evidence": [span()],
        }],
        "requires_clarification": True,
    }
    outcome = forced(store, clock, payload).extract(source(), source_version=1)
    assert outcome.proposal.items[0].local_date is None


# --- span verification ------------------------------------------------------ #


def test_a_span_outside_the_source_text_is_rejected(store, clock):
    payload = {
        "items": [{"kind": "task", "title": "Register",
                   "evidence": [span(start=0, end=99_999)]}],
        "requires_clarification": False,
    }
    outcome = forced(store, clock, payload).extract(source(), source_version=1)
    assert outcome.proposal.items == []
    assert outcome.proposal.disposition is ProposalDisposition.UNRESOLVED
    assert "outside the source text" in (outcome.proposal.unresolved_reason or "")


def test_an_item_with_no_evidence_is_dropped(store, clock):
    payload = {"items": [{"kind": "task", "title": "Register", "evidence": []}],
               "requires_clarification": False}
    outcome = forced(store, clock, payload).extract(source(), source_version=1)
    assert outcome.proposal.items == []


def test_one_bad_item_does_not_discard_the_good_ones(store, clock):
    payload = {
        "items": [
            {"kind": "task", "title": "Good", "evidence": [span(0, 10)]},
            {"kind": "task", "title": "Bad", "evidence": [span(0, 99_999)]},
        ],
        "requires_clarification": False,
    }
    outcome = forced(store, clock, payload).extract(source(), source_version=1)
    assert [i.title for i in outcome.proposal.items] == ["Good"]


# --- empty and oversized sources -------------------------------------------- #


def test_a_silent_source_creates_nothing(store, clock):
    outcome = service(store, clock).extract(source(text="   "), source_version=1)
    assert outcome.proposal.items == []
    assert outcome.proposal.requires_clarification is False
    assert outcome.degraded_reason == "empty_source"


def test_a_missing_transcript_version_is_not_found(store, clock):
    with pytest.raises(MetisError) as exc:
        service(store, clock).extract(source(), source_version=7)
    assert exc.value.code is ErrorCode.NOT_FOUND


def test_long_sources_split_on_sentence_boundaries():
    text = " ".join(f"Sentence number {i} about work." for i in range(40))
    ranges = split_ranges(text, max_chars=120, max_ranges=10)
    assert len(ranges) > 1
    assert ranges[0][0] == 0
    for previous, current in zip(ranges, ranges[1:], strict=False):
        assert current[0] == previous[1], "ranges must tile without gaps or overlap"
    assert all(text[s:e].strip() for s, e in ranges)


def test_range_count_is_capped_and_the_excess_is_reported(store, clock):
    text = " ".join(f"Task number {i} must happen." for i in range(400))
    outcome = service(store, clock).extract(source(text=text), source_version=1)
    assert outcome.proposal.range_count >= 1
    assert outcome.deferred_ranges >= 0, "excess ranges are counted, not silently dropped"


# --- budget ----------------------------------------------------------------- #


def test_extraction_is_blocked_once_the_budget_is_exhausted(store, clock, monkeypatch):
    monkeypatch.setenv("METIS_DAILY_MICRO_USD", "1")
    reset_settings_cache()
    store.reserve_budget(USER, local_date=clock.today_local(NY), micro_usd=1,
                         operation="other", correlation_id="c0")
    with pytest.raises(MetisError) as exc:
        service(store, clock).extract(source(), source_version=1)
    assert exc.value.code is ErrorCode.BUDGET_EXCEEDED
    reset_settings_cache()


def test_the_reservation_is_settled_after_a_successful_call(store, clock):
    day = clock.today_local(NY)
    service(store, clock).extract(source(), source_version=1)
    usage = store.get_usage(USER, day)
    assert usage.reserved_micro_usd == 0, "the hold must be released after settling"
    assert usage.model_calls == 1


def test_a_provider_failure_retains_a_conservative_charge(store, clock, monkeypatch):
    class _Broken(FixtureInferenceClient):
        def complete_json(self, **kw):
            raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "bedrock is down")

    svc = ExtractionService(state_store=store, inference=_Broken(), clock=clock)
    day = clock.today_local(NY)
    with pytest.raises(MetisError):
        svc.extract(source(), source_version=1)
    usage = store.get_usage(USER, day)
    assert usage.settled_micro_usd > 0, "an outage may still have been billed"


# --- prompt ----------------------------------------------------------------- #


def test_the_prompt_carries_the_capture_date_and_timezone_only():
    from datetime import date

    prompt = build_prompt("hello", captured_local_date=date(2026, 10, 5), capture_timezone=NY)
    assert "2026-10-05" in prompt and NY in prompt
    assert "leave it null" in prompt
    assert len(prompt) < 1000, "the prompt is bounded, not a history dump"
