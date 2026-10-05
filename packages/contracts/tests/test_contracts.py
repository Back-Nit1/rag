"""Contract invariants. These guard the rules every other service relies on."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import metis_contracts as m
import pytest
from metis_contracts.entities import ENTITY_MODELS
from metis_contracts.generate import build_schemas, build_typescript, models
from pydantic import ValidationError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
GENERATED = Path(__file__).resolve().parents[1] / "generated" / "metis.ts"

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)

FIXTURE_MODELS = {
    "capture_bloomberg_text.json": m.SourceArtifact,
    "proposal_incomplete_event.json": m.ExtractionProposal,
    "event_after_clarification.json": m.Event,
    "task_canceled.json": m.Task,
    "task_revised_deadline.json": m.Task,
    "reminder_pending_schedule.json": m.Reminder,
    "plan_overflow.json": m.Plan,
    "work_packet_cerno.json": m.WorkPacket,
    "event_envelope_transcript_finalized.json": m.EventEnvelope,
}


@pytest.mark.parametrize("name,model", sorted(FIXTURE_MODELS.items()))
def test_fixture_validates(name: str, model: type) -> None:
    data = json.loads((FIXTURES / name).read_text())
    model.model_validate(data)


def test_every_fixture_is_claimed() -> None:
    on_disk = {p.name for p in FIXTURES.glob("*.json")}
    assert on_disk == set(FIXTURE_MODELS), "add new fixtures to FIXTURE_MODELS"


def test_generated_artifacts_are_current() -> None:
    for name, body in build_schemas().items():
        assert (SCHEMAS / name).read_text() == body, f"stale schema {name}; run make contracts"
    assert GENERATED.read_text() == build_typescript(), "stale metis.ts; run make contracts"


def test_every_entity_model_is_exported() -> None:
    for name, model in ENTITY_MODELS.items():
        assert model.__name__ in m.__all__, f"{name} missing from metis_contracts.__all__"


def test_every_model_has_a_schema() -> None:
    assert set(models()) <= {p.stem for p in SCHEMAS.glob("*.json")}


# --------------------------------------------------------------------------- #
# Invariants that would otherwise be re-litigated in every service
# --------------------------------------------------------------------------- #


def test_naive_timestamps_rejected() -> None:
    with pytest.raises(ValidationError):
        m.Task(
            id="task_x",
            user_id="u",
            created_at=datetime(2026, 10, 5, 14, 0),
            updated_at=T0,
            title="t",
        )


def test_extracted_item_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="cite at least one source span"):
        m.ExtractedItem(kind=m.ExtractedItemKind.TASK, title="no evidence")


def test_memory_requires_source() -> None:
    with pytest.raises(ValidationError, match="must cite its source"):
        m.Memory(
            id="mem_x",
            user_id="u",
            created_at=T0,
            updated_at=T0,
            kind=m.MemoryKind.IDEA,
            content="floating claim",
            effective_at=T0,
            content_hash="0" * 64,
        )


def test_done_task_requires_completed_at() -> None:
    with pytest.raises(ValidationError, match="completed_at"):
        m.Task(id="task_x", user_id="u", created_at=T0, updated_at=T0, title="t", state=m.TaskState.DONE)


def test_task_cannot_depend_on_itself() -> None:
    with pytest.raises(ValidationError, match="depend on itself"):
        m.Task(id="task_x", user_id="u", created_at=T0, updated_at=T0, title="t", depends_on=["task_x"])


def test_precise_event_start_requires_timezone() -> None:
    with pytest.raises(ValidationError, match="requires a timezone"):
        m.Event(id="ev_x", user_id="u", created_at=T0, updated_at=T0, title="t", starts_at=T0)


def test_plan_block_targets_exactly_one_entity() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        m.PlanBlock(starts_at=T0, ends_at=datetime(2026, 10, 5, 15, 0, tzinfo=UTC), title="t")
    with pytest.raises(ValidationError, match="exactly one"):
        m.PlanBlock(
            task_id="task_a",
            event_id="ev_a",
            starts_at=T0,
            ends_at=datetime(2026, 10, 5, 15, 0, tzinfo=UTC),
            title="t",
        )


def test_plan_validation_cannot_be_attributed_to_the_model() -> None:
    with pytest.raises(ValidationError):
        m.PlanValidation(valid=True, checked_by="model", capacity_minutes=60, scheduled_minutes=30)


def test_revision_sensitive_event_requires_target_revision() -> None:
    with pytest.raises(ValueError, match="requires target_revision"):
        m.EventEnvelope(
            event_type=m.EventType.REMINDER_DUE, user_id="u", correlation_id="c", idempotency_key="k"
        )


def test_unknown_event_type_rejected() -> None:
    with pytest.raises(ValueError, match="unknown event_type"):
        m.EventEnvelope(event_type="reminder.explode", user_id="u", correlation_id="c", idempotency_key="k")


def test_unknown_fields_rejected_everywhere() -> None:
    with pytest.raises(ValidationError):
        m.Task(id="task_x", user_id="u", created_at=T0, updated_at=T0, title="t", sneaky_field=True)


def test_estimate_range_ordered() -> None:
    with pytest.raises(ValidationError, match="minutes_max"):
        m.Estimate(minutes=60, minutes_max=30, provenance="user")


def test_reminder_terminal_states_have_no_exits() -> None:
    assert m.REMINDER_TRANSITIONS[m.ReminderState.CANCELED] == frozenset()
    assert m.REMINDER_TRANSITIONS[m.ReminderState.SUPERSEDED] == frozenset()


def test_every_reminder_state_appears_in_the_transition_table() -> None:
    assert set(m.REMINDER_TRANSITIONS) == set(m.ReminderState)
    assert set(m.TASK_TRANSITIONS) == set(m.TaskState)


def test_only_transport_classes_are_retryable() -> None:
    assert m.FailureCategory.VALIDATION not in m.RETRYABLE_FAILURES
    assert m.FailureCategory.BUDGET_EXCEEDED not in m.RETRYABLE_FAILURES
    assert m.FailureCategory.TRANSIENT in m.RETRYABLE_FAILURES


def test_every_error_code_maps_to_a_status() -> None:
    assert set(m.HTTP_STATUS) == set(m.ErrorCode)
    assert m.HTTP_STATUS[m.ErrorCode.REVISION_CONFLICT] == 409


def test_unchanged_sentinel_is_falsy_and_singleton() -> None:
    assert not m.UNCHANGED
    assert m.Unchanged() is m.UNCHANGED


def test_patch_models_distinguish_unset_from_null() -> None:
    from metis_contracts.api import PatchTaskRequest

    patch = PatchTaskRequest(expected_revision=3, deadline_local_date=None)
    dumped = patch.model_dump(exclude_unset=True)
    assert "deadline_local_date" in dumped and dumped["deadline_local_date"] is None
    assert "title" not in dumped, "an omitted field must not appear as a clear"


def test_capture_request_rejects_inline_audio_text() -> None:
    from metis_contracts.api import CreateCaptureRequest

    with pytest.raises(ValidationError, match="must not inline text"):
        CreateCaptureRequest(
            kind=m.SourceKind.AUDIO, text="hi", capture_timezone="UTC", captured_at=T0, expected_bytes=1024
        )


def test_reminder_request_needs_a_resolvable_trigger() -> None:
    from metis_contracts.api import CreateReminderRequest

    with pytest.raises(ValidationError, match="resolvable"):
        CreateReminderRequest(
            target_id="task_a", target_kind="task", expected_target_revision=1, timezone="America/New_York"
        )


def test_local_deadline_is_not_an_instant() -> None:
    d = m.LocalDeadline(local_date=date(2026, 10, 12), timezone="America/New_York")
    assert d.model_dump(mode="json")["local_date"] == "2026-10-12"


def test_source_span_offsets_ordered() -> None:
    with pytest.raises(ValidationError, match="end_char"):
        m.SourceSpan(span_ref="s", source_id="src_a", source_version=1, start_char=10, end_char=5)


def test_bloomberg_fixture_invents_nothing() -> None:
    proposal = m.ExtractionProposal.model_validate(
        json.loads((FIXTURES / "proposal_incomplete_event.json").read_text())
    )
    intent = next(i for i in proposal.items if i.kind == m.ExtractedItemKind.EVENT_INTENT)
    assert intent.starts_at is None and intent.local_date is None
    assert intent.registration_status == m.RegistrationStatus.UNKNOWN
    assert intent.attendance_status == m.AttendanceStatus.TENTATIVE
    assert proposal.requires_clarification is True


def test_overflow_plan_does_not_hide_unscheduled_work() -> None:
    plan = m.Plan.model_validate(json.loads((FIXTURES / "plan_overflow.json").read_text()))
    assert plan.overflow_task_ids, "overflow must stay visible"
    assert plan.validation and plan.validation.checked_by == "application"
    assert plan.adapter_mode == m.AdapterMode.FIXTURE
