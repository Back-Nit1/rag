"""Clock implementations. Workers take a Clock so tests can control time."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def resolve_timezone(name: str) -> ZoneInfo:
    """Validate an IANA identifier. A bad timezone is a validation error, not
    a silent fallback to server time."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {name!r}") from exc


class SystemClock:
    """Real time, always UTC-aware."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def today_local(self, timezone_name: str) -> date:
        return self.now().astimezone(resolve_timezone(timezone_name)).date()


class FixedClock:
    """Deterministic clock for tests; advance it explicitly."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware instant")
        self._now = start.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._now

    def today_local(self, timezone_name: str) -> date:
        return self._now.astimezone(resolve_timezone(timezone_name)).date()

    def advance(self, **delta: float) -> datetime:
        self._now = self._now + timedelta(**delta)
        return self._now

    def set(self, instant: datetime) -> None:
        self._now = instant.astimezone(timezone.utc)
