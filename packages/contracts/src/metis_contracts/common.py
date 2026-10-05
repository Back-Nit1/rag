"""Shared primitives for every Metis contract.

Rules enforced here rather than per-entity:
- every persisted record carries id / user_id / schema_version / created_at /
  updated_at / revision
- instants are timezone-aware UTC; local dates are plain dates paired with an
  IANA timezone identifier, never midnight-UTC timestamps
- unknown values are ``None``; a field absent from a patch means "unchanged"
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1

# An IANA timezone identifier, e.g. "America/New_York". Validated at the
# application boundary against zoneinfo, not by regex alone.
TimezoneName = Annotated[str, Field(min_length=1, max_length=64, examples=["America/New_York"])]
Revision = Annotated[int, Field(ge=1)]
EntityId = Annotated[str, Field(min_length=3, max_length=128)]
UserId = Annotated[str, Field(min_length=1, max_length=128)]


def utcnow() -> datetime:
    """Current instant, timezone-aware UTC. Workers must use Clock, not this."""
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """Opaque prefixed identifier. The prefix aids debugging, grants no authority."""
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


class StrictModel(BaseModel):
    """Reject unknown fields: a typo in a queue payload must fail loudly."""

    model_config = ConfigDict(extra="forbid", frozen=False, use_enum_values=False)


class Unchanged:
    """Sentinel distinguishing `unchanged` from an explicit `null` (clear)."""

    _instance: Unchanged | None = None

    def __new__(cls) -> Unchanged:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "UNCHANGED"

    def __bool__(self) -> bool:
        return False


UNCHANGED = Unchanged()


class Record(StrictModel):
    """Base for every owner-scoped persisted entity."""

    id: EntityId
    user_id: UserId
    schema_version: Literal[1] = SCHEMA_VERSION
    revision: Revision = 1
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _require_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return v.astimezone(UTC)


class LocalDeadline(StrictModel):
    """A date-only deadline. Kept distinct from a precise instant on purpose."""

    local_date: date
    timezone: TimezoneName


class Estimate(StrictModel):
    """A duration estimate that always declares where it came from."""

    minutes: Annotated[int, Field(ge=0, le=24 * 60)]
    minutes_max: Annotated[int | None, Field(default=None, ge=0, le=24 * 60)] = None
    provenance: Literal["user", "inferred", "default"]

    @field_validator("minutes_max")
    @classmethod
    def _ordered(cls, v: int | None, info: Any) -> int | None:
        low = info.data.get("minutes")
        if v is not None and low is not None and v < low:
            raise ValueError("minutes_max must be >= minutes")
        return v


class SourceRef(StrictModel):
    """Pointer to an immutable source revision, optionally a span within it."""

    source_id: EntityId
    source_version: Revision
    span_ref: str | None = None


class Evidence(StrictModel):
    """One retrieved, source-backed justification shown to the user."""

    source_id: EntityId
    source_version: Revision
    span_ref: str | None = None
    memory_id: EntityId | None = None
    excerpt: Annotated[str, Field(max_length=2000)]
    recorded_at: datetime
    validity: Literal["current", "historical", "superseded", "uncertain"] = "current"


class SourceSpan(StrictModel):
    """Character range inside an immutable source revision.

    Offsets are Unicode code points of the source text — never bytes and never
    UTF-16 code units. Audio offsets are optional and independent.
    """

    span_ref: str
    source_id: EntityId
    source_version: Revision
    start_char: Annotated[int, Field(ge=0)]
    end_char: Annotated[int, Field(ge=0)]
    audio_start_ms: Annotated[int | None, Field(default=None, ge=0)] = None
    audio_end_ms: Annotated[int | None, Field(default=None, ge=0)] = None

    @field_validator("end_char")
    @classmethod
    def _range(cls, v: int, info: Any) -> int:
        start = info.data.get("start_char")
        if start is not None and v < start:
            raise ValueError("end_char must be >= start_char")
        return v


T = TypeVar("T")


class Page[T](StrictModel):
    """Bounded, cursor-paginated result. `complete=False` forbids claiming
    full coverage (used by the planner's completeness invariant)."""

    items: list[T]
    next_cursor: str | None = None
    complete: bool = True


class Money(StrictModel):
    """Cost figures are always labelled estimated vs measured."""

    micro_usd: Annotated[int, Field(ge=0)]
    basis: Literal["measured", "estimated"]


class ConfidenceHint(StrictModel):
    """A routing hint only. Never evidence that a value is true."""

    value: Annotated[float, Field(ge=0.0, le=1.0)]
    producer: str


__all__ = [
    "SCHEMA_VERSION",
    "UNCHANGED",
    "ConfidenceHint",
    "EntityId",
    "Estimate",
    "Evidence",
    "LocalDeadline",
    "Money",
    "Page",
    "Record",
    "Revision",
    "SourceRef",
    "SourceSpan",
    "StrictModel",
    "TimezoneName",
    "Unchanged",
    "UserId",
    "new_id",
    "utcnow",
]
