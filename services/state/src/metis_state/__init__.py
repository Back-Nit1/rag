"""Metis authoritative state adapters."""

from __future__ import annotations

from typing import Any

from metis_contracts import Mode, settings

from .sqlite_store import PLANNING_RELEVANT_KINDS, SqliteStateStore

__all__ = ["PLANNING_RELEVANT_KINDS", "SqliteStateStore", "build_state_store"]


def build_state_store(clock: Any | None = None) -> Any:
    """Return the StateStore for the configured mode.

    Cloud mode imports the DynamoDB adapter lazily so local development never
    needs boto3 installed.
    """
    cfg = settings()
    if cfg.mode is Mode.CLOUD:
        from .dynamo_store import DynamoStateStore

        if not cfg.aws.table_name:
            raise ValueError("METIS_TABLE_NAME is required in cloud mode")
        return DynamoStateStore(table_name=cfg.aws.table_name, region=cfg.aws.region, clock=clock)
    return SqliteStateStore(clock=clock)
