"""Local-time semantics. Each test pins a rule that a guess would get wrong."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from metis_contracts import ErrorCode, MetisError
from metis_pipeline.timeref import (
    capture_local_date,
    end_of_local_day,
    is_overdue,
    parse_clock_time,
    parse_relative_date,
    resolve_phrase,
    to_instant,
)

NY = "America/New_York"


def test_tomorrow_resolves_against_the_capture_instant_not_the_worker_clock():
    # Captured 2026-10-05 23:30 local; a worker running the next afternoon must
    # still resolve "tomorrow" to the 6th.
    captured = datetime(2026, 10, 6, 3, 30, tzinfo=UTC)  # 23:30 on the 5th in NY
    resolution = parse_relative_date("remind me tomorrow", captured_at=captured, capture_timezone=NY)
    assert resolution is not None
    assert resolution.local_date == date(2026, 10, 6)


def test_capture_near_midnight_uses_the_users_timezone_not_server_time():
    captured = datetime(2026, 10, 6, 3, 30, tzinfo=UTC)
    assert captured.date() == date(2026, 10, 6)  # UTC has already rolled over
    assert capture_local_date(captured, NY) == date(2026, 10, 5)  # the user's day has not


def test_a_naive_capture_instant_is_rejected():
    with pytest.raises(MetisError) as exc:
        capture_local_date(datetime(2026, 10, 5, 14, 0), NY)
    assert exc.value.code is ErrorCode.VALIDATION_FAILED


def test_an_unknown_timezone_is_an_error_not_a_fallback():
    with pytest.raises(ValueError, match="unknown timezone"):
        to_instant(date(2026, 10, 8), time(18, 0), "Mars/Olympus")


def test_no_recognised_phrase_returns_none_so_the_caller_asks():
    captured = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)
    assert (
        parse_relative_date("I want to go to that event", captured_at=captured, capture_timezone=NY) is None
    )
    assert resolve_phrase("sometime soon would be good", captured_at=captured, capture_timezone=NY) is None


def test_six_pm_local_becomes_the_right_utc_instant():
    resolved = to_instant(date(2026, 10, 8), time(18, 0), NY)
    assert resolved.dst == "ok"
    assert resolved.instant == datetime(2026, 10, 8, 22, 0, tzinfo=UTC)


def test_a_nonexistent_spring_forward_time_is_reported_not_coerced():
    # 2026-03-08 02:30 does not exist in America/New_York.
    resolved = to_instant(date(2026, 3, 8), time(2, 30), NY)
    assert resolved.dst == "nonexistent"
    assert resolved.instant is None
    assert resolved.needs_clarification is True


def test_an_ambiguous_fall_back_time_keeps_both_candidates():
    # 2026-11-01 01:30 occurs twice in America/New_York.
    resolved = to_instant(date(2026, 11, 1), time(1, 30), NY)
    assert resolved.dst == "ambiguous"
    assert len(resolved.ambiguous_alternatives) == 2
    assert resolved.ambiguous_alternatives[0] != resolved.ambiguous_alternatives[1]
    assert resolved.needs_clarification is True


def test_a_date_only_deadline_is_inclusive_of_the_whole_local_day():
    boundary = end_of_local_day(date(2026, 10, 12), NY)
    assert boundary == datetime(2026, 10, 13, 4, 0, tzinfo=UTC)
    # 23:59 local on the 12th is still on time.
    assert is_overdue(date(2026, 10, 12), NY, now=datetime(2026, 10, 13, 3, 59, tzinfo=UTC)) is False
    assert is_overdue(date(2026, 10, 12), NY, now=boundary) is True


def test_midnight_deadline_boundary_survives_a_dst_transition():
    # The day after 2026-03-07 has a 2am gap, not a missing midnight, but the
    # helper must still return a usable boundary for any date.
    assert end_of_local_day(date(2026, 3, 7), NY).tzinfo is UTC


@pytest.mark.parametrize(
    "text,expected",
    [
        # A bare hour is ambiguous between am and pm, so it stays unresolved.
        ("remind me at 9", None),
        ("at 18", time(18, 0)),
        ("at 9am", time(9, 0)),
        ("at 9 pm", time(21, 0)),
        ("at 12am", time(0, 0)),
        ("at 12pm", time(12, 0)),
        ("18:30 works", time(18, 30)),
        ("around noon", time(12, 0)),
        ("at midnight", time(0, 0)),
        ("no time here", None),
        ("at 99pm", None),
    ],
)
def test_clock_parsing(text, expected):
    assert parse_clock_time(text) == expected


def test_tomorrow_at_nine_am_resolves_to_one_instant():
    captured = datetime(2026, 10, 6, 3, 30, tzinfo=UTC)  # 23:30 on the 5th in NY
    resolved = resolve_phrase(
        "remind me tomorrow at 9am to register", captured_at=captured, capture_timezone=NY
    )
    assert resolved is not None
    assert resolved.local_date == date(2026, 10, 6)
    assert resolved.local_time == time(9, 0)
    assert resolved.instant == datetime(2026, 10, 6, 13, 0, tzinfo=UTC)


def test_tomorrow_at_a_bare_nine_keeps_the_time_unresolved():
    captured = datetime(2026, 10, 6, 3, 30, tzinfo=UTC)
    resolved = resolve_phrase(
        "remind me tomorrow at 9 to register", captured_at=captured, capture_timezone=NY
    )
    assert resolved is not None
    assert resolved.local_date == date(2026, 10, 6)
    assert resolved.local_time is None, "9am vs 9pm must be clarified, not guessed"
    assert resolved.instant is None


def test_in_n_days_is_counted_from_the_capture_date():
    captured = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)
    resolved = parse_relative_date("in 3 days", captured_at=captured, capture_timezone=NY)
    assert resolved is not None and resolved.local_date == date(2026, 10, 8)


def test_next_friday_never_resolves_to_the_same_day():
    captured = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)  # a Friday in NY
    resolved = parse_relative_date("next friday", captured_at=captured, capture_timezone=NY)
    assert resolved is not None and resolved.local_date == date(2026, 10, 16)
