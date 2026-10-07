"""Parity for the public reads that replaced API access to the private pager."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from metis_contracts import (
    AdapterMode,
    ExtractionProposal,
    FixedClock,
    Plan,
    PlanStatus,
    SourceArtifact,
    reset_settings_cache,
)
from metis_state.dynamo_store import DynamoStateStore
from metis_state.sqlite_store import SqliteStateStore

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
USER, OTHER = "user_a", "user_b"


def _source(sid: str, user: str = USER, client_id: str | None = None) -> SourceArtifact:
    return SourceArtifact(
        id=sid, user_id=user, created_at=T0, updated_at=T0, kind="text", lifecycle="ready",
        capture_timezone="UTC", captured_at=T0, client_capture_id=client_id,
    )  # fmt: skip


def _proposal(pid: str, source_id: str, user: str = USER) -> ExtractionProposal:
    return ExtractionProposal(
        id=pid, user_id=user, created_at=T0, updated_at=T0, source_id=source_id, source_version=1,
        extractor_version="v", model_id="m", adapter_mode=AdapterMode.FIXTURE, prompt_version="p",
    )  # fmt: skip


def _plan(pid: str, status: PlanStatus, user: str = USER) -> Plan:
    return Plan(
        id=pid, user_id=user, created_at=T0, updated_at=T0, local_date=date(2026, 10, 5),
        timezone="UTC", planning_epoch=0, time_budget_minutes=60, status=status,
    )  # fmt: skip


@pytest.fixture()
def sqlite(tmp_path) -> SqliteStateStore:
    reset_settings_cache()
    s = SqliteStateStore(str(tmp_path / "s.sqlite"), clock=FixedClock(T0))
    with s.transaction(USER) as tx:
        tx.put("source", _source("src_1", client_id="cli-1"))
        tx.put("source", _source("src_2"))
        for i in range(3):
            tx.put("proposal", _proposal(f"prop_{i}", "src_1"))
        tx.put("proposal", _proposal("prop_x", "src_2"))
        tx.put("plan", _plan("plan_1", PlanStatus.PROPOSED))
        tx.put("plan", _plan("plan_2", PlanStatus.ACCEPTED))
    with s.transaction(OTHER) as tx:
        tx.put("source", _source("src_o", OTHER, client_id="cli-1"))
        tx.put("proposal", _proposal("prop_o", "src_1", OTHER))
        tx.put("plan", _plan("plan_o", PlanStatus.PROPOSED, OTHER))
    return s


@pytest.fixture()
def dynamo(sqlite) -> DynamoStateStore:
    """A Dynamo store whose kind-range query is served from the SQLite rows."""
    store = DynamoStateStore.__new__(DynamoStateStore)

    def query_kind(user_id, kind, *, cursor, limit, page_limit=None):
        page = sqlite._page(kind, user_id, "", [], cursor=cursor, limit=limit)
        return page.items, page.next_cursor, page.complete

    store._query_kind = query_kind  # type: ignore[method-assign]
    return store


@pytest.fixture(params=["sqlite", "dynamo"])
def store(request):
    return request.getfixturevalue(request.param)


def test_find_source_by_client_id_is_owner_scoped(store):
    assert store.find_source_by_client_id(USER, "cli-1").id == "src_1"
    assert store.find_source_by_client_id(OTHER, "cli-1").id == "src_o"
    assert store.find_source_by_client_id(USER, "nope") is None


def test_list_proposals_filters_by_source_and_owner(store):
    assert [p.id for p in store.list_proposals(USER, source_id="src_1").items] == [
        "prop_0",
        "prop_1",
        "prop_2",
    ]
    assert len(store.list_proposals(USER).items) == 4
    assert [p.id for p in store.list_proposals(OTHER).items] == ["prop_o"]


def test_list_proposals_cursor_is_stable(store):
    first = store.list_proposals(USER, source_id="src_1", limit=2)
    assert len(first.items) == 2 and first.next_cursor
    rest = store.list_proposals(USER, source_id="src_1", cursor=first.next_cursor, limit=2)
    assert [p.id for p in first.items + rest.items] == ["prop_0", "prop_1", "prop_2"]


def test_list_plans_filters_by_status_and_owner(store):
    assert [p.id for p in store.list_plans(USER, status=PlanStatus.PROPOSED).items] == ["plan_1"]
    assert len(store.list_plans(USER).items) == 2
    assert [p.id for p in store.list_plans(OTHER).items] == ["plan_o"]


def test_list_sources_is_owner_scoped_and_cursor_is_stable(store):
    assert [s.id for s in store.list_sources(USER).items] == ["src_1", "src_2"]
    assert [s.id for s in store.list_sources(OTHER).items] == ["src_o"]
    first = store.list_sources(USER, limit=1)
    assert [s.id for s in first.items] == ["src_1"] and first.next_cursor
    rest = store.list_sources(USER, cursor=first.next_cursor, limit=1)
    assert [s.id for s in rest.items] == ["src_2"]


def test_list_sources_excludes_tombstoned(sqlite):
    sqlite.tombstone(USER, "source", "src_1")
    assert [s.id for s in sqlite.list_sources(USER).items] == ["src_2"]
