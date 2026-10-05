"""Stable API error codes shared by the API, the web client and tests."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from .common import StrictModel


class ErrorCode(StrEnum):
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    REVISION_CONFLICT = "revision_conflict"
    EPOCH_CONFLICT = "epoch_conflict"
    IDEMPOTENCY_MISMATCH = "idempotency_mismatch"
    VALIDATION_FAILED = "validation_failed"
    SEMANTIC_VALIDATION_FAILED = "semantic_validation_failed"
    DEPENDENCY_CYCLE = "dependency_cycle"
    INVALID_STATE_TRANSITION = "invalid_state_transition"
    UPLOAD_TOO_LARGE = "upload_too_large"
    UNSUPPORTED_MEDIA = "unsupported_media"
    OBJECT_MISSING = "object_missing"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXCEEDED = "budget_exceeded"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TOMBSTONED = "tombstoned"
    AMBIGUOUS_TARGET = "ambiguous_target"
    INCOMPLETE_INPUTS = "incomplete_inputs"
    INTERNAL = "internal"


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.REVISION_CONFLICT: 409,
    ErrorCode.EPOCH_CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_MISMATCH: 409,
    ErrorCode.VALIDATION_FAILED: 422,
    ErrorCode.SEMANTIC_VALIDATION_FAILED: 422,
    ErrorCode.DEPENDENCY_CYCLE: 422,
    ErrorCode.INVALID_STATE_TRANSITION: 422,
    ErrorCode.UPLOAD_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_MEDIA: 415,
    ErrorCode.OBJECT_MISSING: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.BUDGET_EXCEEDED: 402,
    ErrorCode.PROVIDER_UNAVAILABLE: 503,
    ErrorCode.TOMBSTONED: 410,
    ErrorCode.AMBIGUOUS_TARGET: 409,
    ErrorCode.INCOMPLETE_INPUTS: 409,
    ErrorCode.INTERNAL: 500,
}


class ErrorBody(StrictModel):
    """One shape for every failure. `current_revision` lets the client reload."""

    code: ErrorCode
    message: Annotated[str, Field(max_length=500)]
    correlation_id: str | None = None
    current_revision: int | None = None
    retry_after_seconds: int | None = None
    details: dict[str, str] = Field(default_factory=dict)


class MetisError(Exception):
    """Raised by services; mapped to ErrorBody + HTTP status at the boundary."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        current_revision: int | None = None,
        retry_after_seconds: int | None = None,
        details: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.current_revision = current_revision
        self.retry_after_seconds = retry_after_seconds
        self.details = details or {}

    @property
    def status_code(self) -> int:
        return HTTP_STATUS[self.code]

    def body(self, correlation_id: str | None = None) -> ErrorBody:
        return ErrorBody(
            code=self.code,
            message=self.message,
            correlation_id=correlation_id,
            current_revision=self.current_revision,
            retry_after_seconds=self.retry_after_seconds,
            details=self.details,
        )
