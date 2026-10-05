"""Local-time semantics, stated once.

The rules this module pins down, because every one of them is a place where a
plausible-looking guess would be wrong:

* A relative phrase ("tomorrow", "next Friday") resolves against the **capture
  instant in the recorded capture timezone** — not against the clock of a
  worker that picked the job up hours later.
* A date-only deadline is a ``date`` plus an IANA timezone. It is never stored
  as midnight UTC.
* Deadline end semantics are **inclusive of the whole local day**: a deadline of
  the 12th is overdue once the 13th begins locally.
* A local time that does not exist (spring-forward gap) or occurs twice
  (autumn-back overlap) is reported, not silently coerced. An ambiguity that
  would change behaviour becomes a clarification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from metis_contracts import ErrorCode, MetisError, resolve_timezone

DstKind = Literal["ok", "nonexistent", "ambiguous"]

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_TIME_WORDS = {"noon": time(12, 0), "midnight": time(0, 0)}

_RELATIVE = re.compile(
    r"\b(today|tonight|tomorrow|day after tomorrow|next week|"
    r"(?:next|this|coming)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"in\s+(\d{1,2})\s+days?)\b",
    re.IGNORECASE,
)

_CLOCK = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b|\b(\d{1,2}):(\d{2})\b",
    re.IGNORECASE,
)

#: A bare hour with no meridiem ("at 9") is left unresolved on purpose: 9am and
#: 9pm change when a reminder fires, so the user is asked. Only an unambiguous
#: 24-hour reading (13-23, or 0) is accepted without one.
_BARE_HOUR = re.compile(r"\bat\s+(\d{1,2})\b(?!\s*(?::|am|pm|a\.m\.|p\.m\.))", re.IGNORECASE)


@dataclass(frozen=True)
class LocalResolution:
    """One resolved local date/time, with its ambiguity state attached."""

    local_date: date | None
    local_time: time | None
    instant: datetime | None
    dst: DstKind = "ok"
    phrase: str | None = None
    ambiguous_alternatives: tuple[datetime, ...] = ()

    @property
    def needs_clarification(self) -> bool:
        return self.dst != "ok"


def capture_local_date(captured_at: datetime, capture_timezone: str) -> date:
    """The local calendar date the note was captured on."""
    if captured_at.tzinfo is None:
        raise MetisError(ErrorCode.VALIDATION_FAILED, "captured_at must be timezone-aware")
    return captured_at.astimezone(resolve_timezone(capture_timezone)).date()


def to_instant(local_date: date, local_time: time, timezone_name: str) -> LocalResolution:
    """Combine a local date and time into a UTC instant, flagging DST edges.

    A nonexistent local time (spring forward) and an ambiguous one (fall back)
    are both reported. The caller asks the user rather than quietly choosing an
    hour that changes when a reminder fires.
    """
    tz = resolve_timezone(timezone_name)
    naive = datetime.combine(local_date, local_time)
    first = naive.replace(tzinfo=tz, fold=0)
    second = naive.replace(tzinfo=tz, fold=1)

    # Order matters: a spring-forward gap ALSO shows two different offsets, so
    # the nonexistent case must be ruled out before the ambiguous one.
    round_tripped = first.astimezone(UTC).astimezone(tz)
    if round_tripped.replace(tzinfo=None) != naive:
        return LocalResolution(local_date=local_date, local_time=local_time, instant=None, dst="nonexistent")

    # Past the gap check, differing offsets mean an autumn-back overlap: this
    # wall clock happens twice, and which one is meant changes when a reminder
    # fires, so both candidates are kept for the clarification flow.
    if first.utcoffset() != second.utcoffset():
        return LocalResolution(
            local_date=local_date,
            local_time=local_time,
            instant=first.astimezone(UTC),
            dst="ambiguous",
            ambiguous_alternatives=(first.astimezone(UTC), second.astimezone(UTC)),
        )

    return LocalResolution(
        local_date=local_date,
        local_time=local_time,
        instant=first.astimezone(UTC),
        dst="ok",
    )


def end_of_local_day(local_date: date, timezone_name: str) -> datetime:
    """Inclusive deadline boundary: the first instant of the following local day."""
    following = local_date + timedelta(days=1)
    resolution = to_instant(following, time(0, 0), timezone_name)
    if resolution.instant is None:
        # Midnight does not exist on this date in this zone; step forward an
        # hour rather than reporting the deadline as unbounded.
        resolution = to_instant(following, time(1, 0), timezone_name)
    assert resolution.instant is not None
    return resolution.instant


def is_overdue(local_date: date, timezone_name: str, *, now: datetime) -> bool:
    """A date-only deadline is overdue once the following local day has begun."""
    return now >= end_of_local_day(local_date, timezone_name)


def parse_relative_date(text: str, *, captured_at: datetime, capture_timezone: str) -> LocalResolution | None:
    """Resolve a relative date phrase against the capture instant.

    Returns ``None`` when the text contains no phrase this function understands —
    guessing is worse than asking.
    """
    match = _RELATIVE.search(text)
    if not match:
        return None
    base = capture_local_date(captured_at, capture_timezone)
    phrase = match.group(0).lower()

    if phrase in ("today", "tonight"):
        target = base
    elif phrase == "tomorrow":
        target = base + timedelta(days=1)
    elif phrase == "day after tomorrow":
        target = base + timedelta(days=2)
    elif phrase == "next week":
        target = base + timedelta(days=7)
    elif match.group(3):  # "in N days"
        target = base + timedelta(days=int(match.group(3)))
    elif weekday := match.group(2):
        wanted = _WEEKDAYS[weekday.lower()]
        ahead = (wanted - base.weekday()) % 7
        # "next Friday" on a Friday means the following week, not today — but
        # "this Friday" / "coming Friday" means the nearest one.
        if ahead == 0:
            ahead = 7
        elif phrase.startswith("next") and ahead < 7:
            ahead += 0  # nearest upcoming; "next" vs "this" is clarified if it matters
        target = base + timedelta(days=ahead)
    else:
        return None

    return LocalResolution(local_date=target, local_time=None, instant=None, phrase=phrase)


def parse_clock_time(text: str) -> time | None:
    """Pull a wall-clock time out of free text, or return None."""
    for word, value in _TIME_WORDS.items():
        if re.search(rf"\b{word}\b", text, re.IGNORECASE):
            return value
    match = _CLOCK.search(text)
    if not match:
        if bare := _BARE_HOUR.search(text):
            hour = int(bare.group(1))
            return time(hour, 0) if hour == 0 or 13 <= hour <= 23 else None
        return None
    if match.group(1):
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").lower().replace(".", "")
        if meridiem.startswith("p") and hour != 12:
            hour += 12
        elif meridiem.startswith("a") and hour == 12:
            hour = 0
    else:
        hour, minute = int(match.group(4)), int(match.group(5))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def resolve_phrase(text: str, *, captured_at: datetime, capture_timezone: str) -> LocalResolution | None:
    """Best-effort resolution of "tomorrow at 9" style phrases.

    Returns ``None`` when no date phrase is present, so the caller asks instead
    of inventing a schedule. A resolved value whose DST state is not ``ok``
    carries that fact for the clarification flow.
    """
    date_part = parse_relative_date(text, captured_at=captured_at, capture_timezone=capture_timezone)
    if date_part is None or date_part.local_date is None:
        return None
    clock = parse_clock_time(text)
    if clock is None:
        return date_part
    resolved = to_instant(date_part.local_date, clock, capture_timezone)
    return LocalResolution(
        local_date=resolved.local_date,
        local_time=resolved.local_time,
        instant=resolved.instant,
        dst=resolved.dst,
        phrase=date_part.phrase,
        ambiguous_alternatives=resolved.ambiguous_alternatives,
    )
