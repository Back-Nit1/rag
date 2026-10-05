"""Budget gate around every paid model call.

The order is always reserve -> call -> settle, never call -> record. A
conservative upper bound is held atomically before the request so two
concurrent workers cannot both pass on the same remaining budget, and an
uncertain outcome keeps the hold because a timed-out request may still be
billed.

Deterministic work must never pass through here: an ordinary templated
reminder makes zero model calls and so takes no reservation.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from metis_contracts import ErrorCode, MetisError, settings

log = logging.getLogger("metis.pipeline.budget")


@dataclass
class Spend:
    """Filled in by the caller after the model returns."""

    actual_micro_usd: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    uncertain: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def record(
        self,
        *,
        micro_usd: int,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        self.actual_micro_usd = micro_usd
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.uncertain = False

    def mark_uncertain(self) -> None:
        """The call may have been billed but the outcome is unknown."""
        self.uncertain = True


def estimate_micro_usd(
    *, input_tokens: int, output_tokens: int, input_rate: int | None = None, output_rate: int | None = None
) -> int:
    """Conservative cost estimate from configured per-1k rates.

    Rates are configuration, never hard-coded facts; verify them against the
    selected region's rate card before trusting a figure derived from them.
    """
    cfg = settings().models
    rate_in = input_rate if input_rate is not None else cfg.input_micro_usd_per_1k
    rate_out = output_rate if output_rate is not None else cfg.output_micro_usd_per_1k
    # Round up: a reservation must never under-reserve.
    return -(-input_tokens * rate_in // 1000) + -(-output_tokens * rate_out // 1000)


@contextmanager
def reserved(
    state_store: Any,
    user_id: str,
    *,
    local_date: date,
    operation: str,
    correlation_id: str,
    estimated_micro_usd: int,
) -> Iterator[Spend]:
    """Hold budget for one paid call, then settle against actual usage.

    Raises ``MetisError(BUDGET_EXCEEDED)`` *before* the call when the cap would
    be crossed — the caller then degrades to a deterministic path rather than
    silently switching provider or ignoring the cap.
    """
    reservation_id = state_store.reserve_budget(
        user_id,
        local_date=local_date,
        micro_usd=max(1, estimated_micro_usd),
        operation=operation,
        correlation_id=correlation_id,
    )
    spend = Spend()
    try:
        yield spend
    except MetisError as exc:
        # A provider timeout or an unknown failure may still have been billed.
        if exc.code in (ErrorCode.PROVIDER_UNAVAILABLE, ErrorCode.INTERNAL):
            spend.mark_uncertain()
        raise
    except BaseException:
        spend.mark_uncertain()
        raise
    finally:
        state_store.settle_budget(
            reservation_id,
            user_id=user_id,
            actual_micro_usd=spend.actual_micro_usd,
            input_tokens=spend.input_tokens,
            output_tokens=spend.output_tokens,
            uncertain=spend.uncertain,
        )
        log.info(
            "budget settled operation=%s correlation=%s micro_usd=%s uncertain=%s in_tok=%s out_tok=%s",
            operation,
            correlation_id,
            spend.actual_micro_usd,
            spend.uncertain,
            spend.input_tokens,
            spend.output_tokens,
        )


def budget_available(state_store: Any, user_id: str, local_date: date) -> bool:
    """Cheap pre-check for UI and for deciding whether to defer optional work.

    Not a substitute for :func:`reserved` — only the atomic reservation is
    authoritative.
    """
    usage = state_store.get_usage(user_id, local_date)
    return usage.committed_micro_usd < settings().limits.daily_micro_usd
