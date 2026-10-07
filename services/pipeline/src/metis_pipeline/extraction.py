"""Transcript -> validated ExtractionProposal.

Shape of the step, and why each guard exists:

1. **Bounded input.** Only the transcript window goes to the model — never the
   user's whole history. A note too long for one call is split into semantically
   coherent character ranges with per-range completion, so a retry never
   restarts finished ranges and no obligation is silently truncated away.
2. **Budget first.** The call happens inside a reservation; at the cap the
   capture is still saved and the proposal is marked unresolved.
3. **Schema then semantics.** JSON-schema conformity is necessary but proves
   nothing about meaning, so date/enum/registration checks run afterwards.
4. **Spans are verified.** Every evidence offset is validated against the
   immutable source text. A model-supplied quotation is never trusted as
   verbatim.
5. **Nothing is promoted.** The output is a proposal. Tasks, events and
   reminders are created only when the user (or an explicit instruction in the
   note) resolves it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from metis_contracts import (
    AdapterMode,
    ErrorCode,
    ExtractedItem,
    ExtractionProposal,
    MetisError,
    ProposalDisposition,
    SourceArtifact,
    SourceRef,
    SourceSpan,
    new_id,
    settings,
)
from metis_contracts.enums import (
    TASK_OPEN_STATES,
    AttendanceStatus,
    ExtractedItemKind,
    RegistrationStatus,
)

from .budget import estimate_micro_usd, reserved
from .inference import SYSTEM_GUARDRAIL, rough_token_count
from .timeref import capture_local_date

log = logging.getLogger("metis.pipeline.extraction")

EXTRACTOR_VERSION = "extract-1"

#: Sentence-ish boundaries used to split a long source without cutting mid-clause.
_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

#: A deadline further out than this is almost certainly a model error, not a
#: user intent. It is rejected rather than stored as a real commitment.
MAX_DEADLINE_HORIZON_DAYS = 365 * 3


def extraction_schema() -> dict[str, Any]:
    """Compact schema sent to the model.

    Deliberately smaller than the full ExtractedItem contract: the model only
    fills what a note can state, and the server supplies ids, ownership,
    revisions and timestamps.
    """
    return {
        "type": "object",
        "required": ["items", "requires_clarification"],
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "required": ["kind", "title", "evidence"],
                    "additionalProperties": False,
                    "properties": {
                        "kind": {"type": "string", "enum": [k.value for k in ExtractedItemKind]},
                        "title": {"type": "string", "maxLength": 200},
                        "body": {"type": ["string", "null"], "maxLength": 4000},
                        "project_hint": {"type": ["string", "null"]},
                        "deadline_local_date": {"type": ["string", "null"], "format": "date"},
                        "location_text": {"type": ["string", "null"]},
                        "local_date": {"type": ["string", "null"], "format": "date"},
                        "remind_local_time": {"type": ["string", "null"], "pattern": "^[0-9]{2}:[0-9]{2}$"},
                        "attendance_status": {
                            "type": ["string", "null"],
                            "enum": [*[s.value for s in AttendanceStatus], None],
                        },
                        "registration_status": {
                            "type": ["string", "null"],
                            "enum": [*[s.value for s in RegistrationStatus], None],
                        },
                        "requested_actions": {"type": "array", "items": {"type": "string"}},
                        "update_action": {
                            "type": ["string", "null"],
                            "enum": ["registered", "canceled", None],
                        },
                        "target_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                        "missing_fields": {"type": "array", "items": {"type": "string"}},
                        "evidence": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "required": ["start_char", "end_char"],
                                "additionalProperties": False,
                                "properties": {
                                    "span_ref": {"type": ["string", "null"]},
                                    "start_char": {"type": "integer", "minimum": 0},
                                    "end_char": {"type": "integer", "minimum": 0},
                                },
                            },
                        },
                    },
                },
            },
            "requires_clarification": {"type": "boolean"},
            "notes": {"type": ["string", "null"], "maxLength": 500},
        },
    }


def split_ranges(text: str, *, max_chars: int, max_ranges: int) -> list[tuple[int, int]]:
    """Split on sentence boundaries into at most ``max_ranges`` windows.

    Returns inclusive-start / exclusive-end character offsets. When the text
    needs more ranges than allowed, the remainder is *not* dropped silently:
    the caller sees fewer ranges than the text requires and marks the excess
    as deferred.
    """
    if len(text) <= max_chars:
        return [(0, len(text))]
    ranges: list[tuple[int, int]] = []
    start = 0
    cursor = 0
    for piece in _BOUNDARY.split(text):
        piece_len = len(piece)
        gap = 1 if cursor + piece_len < len(text) else 0
        if cursor - start + piece_len > max_chars and cursor > start:
            ranges.append((start, cursor))
            start = cursor
            if len(ranges) >= max_ranges:
                return ranges
        cursor += piece_len + gap
    if start < len(text):
        ranges.append((start, len(text)))
    return ranges[:max_ranges]


def build_prompt(
    text: str,
    *,
    captured_local_date: date,
    capture_timezone: str,
    candidates: list[tuple[str, str, str]] = (),  # type: ignore[assignment]
) -> str:
    """Bounded, typed context — not a concatenation of the user's history.

    ``candidates`` are (id, "event"|"task", title) rows a status update may target.
    """
    block = ""
    if candidates:
        rows = "\n".join(f"{i} | {k} | {t[:80]}" for i, k, t in candidates)
        block = (
            "CANDIDATES (open records a status update such as 'I registered' or 'cancel that' may "
            "target; return the ids in target_ids, never guess):\n" + rows + "\n\n"
        )
    return (
        f"Captured on {captured_local_date.isoformat()} in timezone {capture_timezone}.\n"
        "Resolve any relative date against that capture date. If the note does not "
        "state a date, a time, a venue, a registration status or completion, leave it "
        "null and list it in missing_fields.\n\n"
        f"{block}"
        "NOTE:\n"
        f"{text}"
    )


@dataclass
class ExtractionOutcome:
    proposal: ExtractionProposal
    usage: dict[str, Any]
    deferred_ranges: int = 0
    degraded_reason: str | None = None


class ExtractionService:
    """Owns the extraction step. Holds no state between calls."""

    def __init__(self, *, state_store: Any, inference: Any, clock: Any) -> None:
        self._state = state_store
        self._inference = inference
        self._clock = clock

    # ------------------------------------------------------------------ #

    def extract(
        self,
        source: SourceArtifact,
        *,
        source_version: int,
        range_index: int = 0,
        char_start: int = 0,
        char_end: int | None = None,
    ) -> ExtractionOutcome:
        text = self._text_for(source, source_version)
        limits = settings().limits
        window = text[char_start : char_end if char_end is not None else len(text)]
        if not window.strip():
            # A silent or empty source creates nothing at all.
            return ExtractionOutcome(
                proposal=self._empty_proposal(
                    source, source_version, range_index, "no speech in this source"
                ),
                usage={},
                degraded_reason="empty_source",
            )

        ranges = split_ranges(
            window,
            max_chars=limits.extraction_max_input_tokens * 3,
            max_ranges=limits.extraction_max_ranges,
        )
        deferred = max(0, _ranges_needed(window, limits.extraction_max_input_tokens * 3) - len(ranges))

        candidates = self._candidates(source.user_id)
        prompt = build_prompt(
            window,
            candidates=candidates,
            captured_local_date=capture_local_date(source.captured_at, source.capture_timezone),
            capture_timezone=source.capture_timezone,
        )
        estimated_in = rough_token_count(SYSTEM_GUARDRAIL) + rough_token_count(prompt)
        estimate = estimate_micro_usd(
            input_tokens=estimated_in, output_tokens=limits.extraction_max_output_tokens
        )

        local_today = self._clock.today_local(source.capture_timezone)
        with reserved(
            self._state,
            source.user_id,
            local_date=local_today,
            operation="extraction",
            correlation_id=f"extract:{source.id}:{source_version}:{range_index}",
            estimated_micro_usd=estimate,
        ) as spend:
            payload, usage = self._inference.complete_json(
                prompt_version=settings().models.prompt_version_extraction,
                system=SYSTEM_GUARDRAIL,
                user_content=prompt,
                json_schema=extraction_schema(),
                max_input_tokens=limits.extraction_max_input_tokens,
                max_output_tokens=limits.extraction_max_output_tokens,
                timeout_seconds=limits.model_timeout_seconds,
            )
            spend.record(
                micro_usd=int(usage.get("micro_usd", 0)),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
            )

        items, spans, issues = self._validate(
            payload,
            source=source,
            source_version=source_version,
            text=text,
            offset=char_start,
            window=window,
            offered={c[0] for c in candidates},
        )

        requires_clarification = bool(payload.get("requires_clarification")) or any(
            item.missing_fields for item in items
        )
        now = self._clock.now()
        proposal = ExtractionProposal(
            id=new_id("prop"),
            user_id=source.user_id,
            created_at=now,
            updated_at=now,
            source_id=source.id,
            source_version=source_version,
            items=items,
            requires_clarification=requires_clarification,
            disposition=ProposalDisposition.PENDING if items else ProposalDisposition.UNRESOLVED,
            extractor_version=EXTRACTOR_VERSION,
            model_id=str(usage.get("model_id", getattr(self._inference, "model_id", "unknown"))),
            adapter_mode=AdapterMode(usage.get("adapter_mode", AdapterMode.FIXTURE.value)),
            prompt_version=settings().models.prompt_version_extraction,
            range_index=range_index,
            range_count=len(ranges),
            unresolved_reason="; ".join(issues) if issues and not items else None,
        )
        log.info(
            "extracted source=%s v=%s items=%d clarify=%s deferred_ranges=%d issues=%d",
            source.id,
            source_version,
            len(items),
            requires_clarification,
            deferred,
            len(issues),
        )
        return ExtractionOutcome(
            proposal=proposal,
            usage={**usage, "spans": len(spans), "rejected": len(issues)},
            deferred_ranges=deferred,
            degraded_reason="; ".join(issues) or None,
        )

    # ------------------------------------------------------------------ #

    def _candidates(self, user_id: str) -> list[tuple[str, str, str]]:
        """Open events then open tasks, at most 10 of each. Ids only ever come from here."""
        events = [
            e
            for e in self._state.list_events(user_id, limit=100).items
            if e.attendance_status not in (AttendanceStatus.CANCELED, AttendanceStatus.DECLINED)
        ][:10]
        tasks = self._state.list_tasks(user_id, states=sorted(TASK_OPEN_STATES), limit=10).items[:10]
        return [(e.id, "event", e.title) for e in events] + [(t.id, "task", t.title) for t in tasks]

    def _text_for(self, source: SourceArtifact, source_version: int) -> str:
        for transcript in source.transcripts:
            if transcript.source_version == source_version:
                return transcript.text
        raise MetisError(
            ErrorCode.NOT_FOUND, f"source {source.id} has no transcript at version {source_version}"
        )

    def _empty_proposal(
        self, source: SourceArtifact, source_version: int, range_index: int, reason: str
    ) -> ExtractionProposal:
        now = self._clock.now()
        return ExtractionProposal(
            id=new_id("prop"),
            user_id=source.user_id,
            created_at=now,
            updated_at=now,
            source_id=source.id,
            source_version=source_version,
            items=[],
            requires_clarification=False,
            disposition=ProposalDisposition.UNRESOLVED,
            extractor_version=EXTRACTOR_VERSION,
            model_id="none",
            adapter_mode=AdapterMode.FIXTURE,
            prompt_version=settings().models.prompt_version_extraction,
            range_index=range_index,
            unresolved_reason=reason,
        )

    def _validate(
        self,
        payload: dict[str, Any],
        *,
        source: SourceArtifact,
        source_version: int,
        text: str,
        offset: int,
        window: str,
        offered: set[str] = frozenset(),  # type: ignore[assignment]
    ) -> tuple[list[ExtractedItem], list[SourceSpan], list[str]]:
        """Schema-shaped output still has to survive semantic checks."""
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise MetisError(ErrorCode.SEMANTIC_VALIDATION_FAILED, "extraction payload has no items array")

        horizon = self._clock.now().date() + timedelta(days=MAX_DEADLINE_HORIZON_DAYS)
        lowered = window.lower()
        items: list[ExtractedItem] = []
        spans: list[SourceSpan] = []
        issues: list[str] = []

        event_registers = any(
            isinstance(r, dict) and "remind_to_register" in (r.get("requested_actions") or [])
            for r in raw_items
        )
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                issues.append(f"item {index}: not an object")
                continue
            if (
                event_registers
                and raw.get("kind") == "task"
                and str(raw.get("title", "")).strip().lower().startswith("register")
            ):
                issues.append(
                    f"item {index}: duplicate registration task dropped; event carries remind_to_register"
                )
                continue
            try:
                evidence_refs, item_spans = self._build_spans(
                    raw.get("evidence") or [],
                    source=source,
                    source_version=source_version,
                    text=text,
                    offset=offset,
                    index=index,
                )
            except MetisError as exc:
                issues.append(f"item {index}: {exc.message}")
                continue

            cleaned = {
                key: value
                for key, value in raw.items()
                if key in ExtractedItem.model_fields and key != "evidence"
            }
            cleaned["evidence"] = evidence_refs

            # Semantic check: an absurd deadline is a model error, not intent.
            if deadline := cleaned.get("deadline_local_date"):
                parsed = _as_date(deadline)
                if parsed is None:
                    issues.append(f"item {index}: deadline_local_date is not a date")
                    cleaned["deadline_local_date"] = None
                elif parsed > horizon:
                    issues.append(f"item {index}: deadline {parsed} is beyond the supported horizon")
                    cleaned["deadline_local_date"] = None
                elif not _deadline_is_grounded(parsed, lowered):
                    issues.append(f"item {index}: deadline {parsed} is not stated in the note")
                    cleaned["deadline_local_date"] = None

            if cleaned.get("kind") == ExtractedItemKind.STATUS_UPDATE.value:
                # Targets come only from the offered candidates; never matched by title.
                given = list(cleaned.get("target_ids") or [])
                cleaned["target_ids"] = [t for t in given if t in offered]
                if len(cleaned["target_ids"]) != len(given):
                    issues.append(f"item {index}: target_ids not among the offered candidates dropped")
                if len(set(cleaned["target_ids"])) != 1:
                    cleaned["missing_fields"] = [*(cleaned.get("missing_fields") or []), "target"]
                if cleaned.get("update_action") == "registered" and not any(
                    p in lowered for p in ("registered", "signed up", "got a ticket", "booked")
                ):
                    issues.append(f"item {index}: status_update registered is unsupported by the note")
                    continue

            # Semantic check: registration is never inferred from intent.
            if cleaned.get("registration_status") == RegistrationStatus.REGISTERED.value and not any(
                p in lowered for p in ("registered", "signed up", "got a ticket", "booked")
            ):
                issues.append(f"item {index}: registration_status=registered is unsupported by the note")
                cleaned["registration_status"] = RegistrationStatus.UNKNOWN.value

            # Semantic check: committing to attend needs an explicit statement.
            if cleaned.get("attendance_status") == AttendanceStatus.COMMITTED.value and not any(
                p in lowered for p in ("i am going", "i'm going", "i will go", "confirmed", "i am attending")
            ):
                issues.append(f"item {index}: attendance 'committed' downgraded to tentative")
                cleaned["attendance_status"] = AttendanceStatus.TENTATIVE.value

            # Semantic check: a resolved date on an event the note left open.
            if cleaned.get("local_date") and "event_date_time" in (cleaned.get("missing_fields") or []):
                issues.append(f"item {index}: local_date set while event_date_time is listed missing")
                cleaned["local_date"] = None

            cleaned["requested_actions"] = [
                action
                for action in (cleaned.get("requested_actions") or [])
                if action in {"remind_to_register", "prepare_for_event", "remind_generic", "plan_time"}
            ]

            try:
                items.append(ExtractedItem.model_validate(cleaned))
                spans.extend(item_spans)
            except Exception as exc:
                issues.append(f"item {index}: {exc}")

        return items, spans, issues

    def _build_spans(
        self,
        raw_evidence: list[Any],
        *,
        source: SourceArtifact,
        source_version: int,
        text: str,
        offset: int,
        index: int,
    ) -> tuple[list[SourceRef], list[SourceSpan]]:
        """Validate each offset against the immutable source text.

        Offsets are Unicode code points of the full source, so the window
        offset is added back. A span outside the text is rejected: a generated
        description must not be presented as a verbatim quotation.
        """
        refs: list[SourceRef] = []
        spans: list[SourceSpan] = []
        for position, raw in enumerate(raw_evidence):
            if not isinstance(raw, dict):
                raise MetisError(ErrorCode.SEMANTIC_VALIDATION_FAILED, "evidence entry is not an object")
            try:
                start = int(raw["start_char"]) + offset
                end = int(raw["end_char"]) + offset
            except (KeyError, TypeError, ValueError) as exc:
                raise MetisError(
                    ErrorCode.SEMANTIC_VALIDATION_FAILED, "evidence offsets are missing"
                ) from exc
            if not (0 <= start <= end <= len(text)):
                raise MetisError(
                    ErrorCode.SEMANTIC_VALIDATION_FAILED,
                    f"evidence span {start}:{end} is outside the source text (len {len(text)})",
                )
            span_ref = str(raw.get("span_ref") or f"span_{index:02d}_{position:02d}")
            spans.append(
                SourceSpan(
                    span_ref=span_ref,
                    source_id=source.id,
                    source_version=source_version,
                    start_char=start,
                    end_char=end,
                )
            )
            refs.append(SourceRef(source_id=source.id, source_version=source_version, span_ref=span_ref))
        if not refs:
            raise MetisError(ErrorCode.SEMANTIC_VALIDATION_FAILED, "no usable evidence span")
        return refs, spans


def _ranges_needed(text: str, max_chars: int) -> int:
    return max(1, -(-len(text) // max_chars))


_DATE_WORDS = re.compile(
    r"\b(today|tonight|tomorrow|week|weekend|month|eod|eow|end of|next|monday|tuesday|wednesday|thursday"
    r"|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|jan(uary)?|feb(ruary)?|mar(ch)?"
    r"|apr(il)?|may|june?|july?|aug(ust)?|sep(t|tember)?|oct(ober)?|nov(ember)?|dec(ember)?"
    r"|in \d+ (day|week|month)s?)\b|\d{1,4}[/.-]\d{1,2}"
)


def _deadline_is_grounded(parsed: date, lowered: str) -> bool:
    """A deadline must come from the note: its ISO form, or explicit/relative date words."""
    return parsed.isoformat() in lowered or _DATE_WORDS.search(lowered) is not None


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None
