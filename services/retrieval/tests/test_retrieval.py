"""Retrieval invariants: authority, scoping, staleness and evidence budgets."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from metis_contracts import (
    FixedClock,
    Memory,
    MemoryKind,
    MemoryValidity,
    Project,
    SourceArtifact,
    SourceRef,
    Task,
    TaskState,
    reset_settings_cache,
)
from metis_contracts.entities import TranscriptRevision
from metis_contracts.enums import SourceKind, SourceLifecycle
from metis_retrieval import (
    EvidenceBudget,
    FixtureEmbedder,
    RetrievalService,
    SqliteMemoryStore,
    content_hash,
    context_window,
    needs_embedding,
)
from metis_state.sqlite_store import SqliteStateStore

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
USER = "user_a"
OTHER = "user_b"
NY = "America/New_York"


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture()
def store(tmp_path, clock) -> SqliteStateStore:
    reset_settings_cache()
    yield SqliteStateStore(str(tmp_path / "s.sqlite"), clock=clock)
    reset_settings_cache()


@pytest.fixture()
def memory_store() -> SqliteMemoryStore:
    return SqliteMemoryStore(":memory:")


@pytest.fixture()
def service(store, memory_store, clock) -> RetrievalService:
    return RetrievalService(
        state_store=store, memory_store=memory_store, embedder=FixtureEmbedder(), clock=clock
    )


def add_source(store, clock, *, source_id="src_cerno", text="placeholder", version=1) -> SourceArtifact:
    source = SourceArtifact(
        id=source_id,
        user_id=USER,
        created_at=clock.now(),
        updated_at=clock.now(),
        kind=SourceKind.TEXT,
        lifecycle=SourceLifecycle.READY,
        capture_timezone=NY,
        captured_at=clock.now(),
        current_version=version,
        transcripts=[TranscriptRevision(source_version=version, text=text, created_at=clock.now())],
    )
    with store.transaction(USER) as tx:
        return tx.put("source", source)


def add_memory(
    store,
    memory_store,
    clock,
    *,
    memory_id,
    content,
    kind=MemoryKind.OBSERVATION,
    project_id=None,
    source_id="src_cerno",
    source_version=1,
    user=USER,
    validity=MemoryValidity.CURRENT,
) -> Memory:
    memory = Memory(
        id=memory_id,
        user_id=user,
        created_at=clock.now(),
        updated_at=clock.now(),
        kind=kind,
        content=content,
        project_id=project_id,
        validity=validity,
        effective_at=clock.now(),
        source_refs=[SourceRef(source_id=source_id, source_version=source_version)],
        content_hash=content_hash(content),
        embedding_model_id="fixture-embed-v1",
        embedding_dimensions=64,
    )
    with store.transaction(user) as tx:
        stored = tx.put("memory", memory)
    memory_store.upsert(user, [stored], FixtureEmbedder().embed([content]))
    return stored


def add_task(store, clock, *, task_id="task_1", state=TaskState.READY, **kw) -> Task:
    task = Task(
        id=task_id,
        user_id=USER,
        created_at=clock.now(),
        updated_at=clock.now(),
        title=kw.pop("title", "Test diff-aware invalidation"),
        state=state,
        **kw,
    )
    with store.transaction(USER) as tx:
        return tx.put("task", task)


# --- obligations come from structured queries, not similarity --------------- #


def test_a_dated_task_is_included_even_when_semantically_unrelated(store, clock, service):
    add_task(
        store,
        clock,
        task_id="task_dentist",
        title="Call the dentist",
        deadline_local_date=date(2026, 10, 6),
        timezone=NY,
    )
    scan = service.list_eligible_obligations(USER, local_date=date(2026, 10, 6))
    assert "task_dentist" in [t.id for t in scan.tasks]
    assert scan.complete is True


def test_completed_and_canceled_work_is_excluded(store, clock, service):
    add_task(store, clock, task_id="task_open")
    add_task(store, clock, task_id="task_done", state=TaskState.DONE, completed_at=clock.now())
    add_task(store, clock, task_id="task_cancel", state=TaskState.CANCELED, canceled_at=clock.now())
    scan = service.list_eligible_obligations(USER, local_date=date(2026, 10, 6))
    assert [t.id for t in scan.tasks] == ["task_open"]


def test_a_truncated_scan_is_marked_incomplete(store, clock, service, monkeypatch):
    monkeypatch.setenv("METIS_PLAN_READ_PAGES", "1")
    reset_settings_cache()
    for i in range(250):
        add_task(store, clock, task_id=f"task_{i:03d}")
    scan = service.list_eligible_obligations(USER, local_date=date(2026, 10, 6))
    assert scan.complete is False
    assert scan.incomplete_reason and "read limit" in scan.incomplete_reason
    reset_settings_cache()


# --- scoping and leakage ---------------------------------------------------- #


def test_another_users_memory_is_never_returned(store, memory_store, clock, service):
    add_source(store, clock, text="shared vocabulary about caching files")
    add_memory(
        store,
        memory_store,
        clock,
        memory_id="mem_mine",
        content="caching whole files went stale after changes",
    )
    other = Memory(
        id="mem_theirs",
        user_id=OTHER,
        created_at=clock.now(),
        updated_at=clock.now(),
        kind=MemoryKind.OBSERVATION,
        content="caching whole files went stale after changes",
        validity=MemoryValidity.CURRENT,
        effective_at=clock.now(),
        source_refs=[SourceRef(source_id="src_other", source_version=1)],
        content_hash=content_hash("other"),
        embedding_model_id="fixture-embed-v1",
        embedding_dimensions=64,
    )
    with store.transaction(OTHER) as tx:
        stored = tx.put("memory", other)
    memory_store.upsert(OTHER, [stored], FixtureEmbedder().embed([other.content]))

    results, degraded = service.retrieve_supporting_memories(USER, query="caching files stale")
    assert degraded is None
    assert [m.id for m, _ in results] == ["mem_mine"]


def test_project_scope_filters_candidates(store, memory_store, clock, service):
    add_source(store, clock)
    add_memory(
        store,
        memory_store,
        clock,
        memory_id="mem_cerno",
        content="cerno review re-reads files",
        project_id="proj_cerno",
    )
    add_memory(
        store,
        memory_store,
        clock,
        memory_id="mem_themis",
        content="cerno review re-reads files",
        project_id="proj_themis",
    )
    results, _ = service.retrieve_supporting_memories(
        USER, query="review re-reads files", project_id="proj_cerno"
    )
    assert [m.id for m, _ in results] == ["mem_cerno"]


# --- staleness and supersession --------------------------------------------- #


def test_a_memory_from_a_superseded_source_version_is_dropped(store, memory_store, clock, service):
    source = add_source(store, clock, text="original transcript about caching", version=1)
    add_memory(
        store,
        memory_store,
        clock,
        memory_id="mem_old",
        content="caching files stale",
        source_id=source.id,
        source_version=1,
    )
    # The user corrects the transcript: the source moves to version 2.
    with store.transaction(USER) as tx:
        tx.put(
            "source",
            source.model_copy(
                update={
                    "current_version": 2,
                    "transcripts": [
                        *source.transcripts,
                        TranscriptRevision(
                            source_version=2,
                            text="corrected transcript",
                            edited_by_user=True,
                            created_at=clock.now(),
                        ),
                    ],
                }
            ),
            expected_revision=source.revision,
        )
    results, _ = service.retrieve_supporting_memories(USER, query="caching files stale")
    assert results == [], "evidence from an outdated transcript revision is not current evidence"


def test_a_deleted_memory_is_dropped_even_while_still_in_the_index(store, memory_store, clock, service):
    add_source(store, clock)
    add_memory(store, memory_store, clock, memory_id="mem_gone", content="caching files stale")
    store.tombstone(USER, "memory", "mem_gone")
    results, _ = service.retrieve_supporting_memories(USER, query="caching files stale")
    assert results == []


def test_a_failed_attempt_remains_retrievable_as_history(store, memory_store, clock, service):
    add_source(store, clock, text="I tried caching whole files but it got stale after changes")
    add_memory(
        store,
        memory_store,
        clock,
        memory_id="mem_attempt",
        content="I tried caching whole files but it got stale after changes",
        kind=MemoryKind.ATTEMPT,
        project_id="proj_cerno",
    )
    task = add_task(
        store, clock, project_id="proj_cerno", title="caching whole files stale changes invalidation"
    )
    evidence = service.evidence_for_task(USER, task, budget=EvidenceBudget(max_tokens=3000))
    assert evidence, "a failed attempt must not be filtered away"
    assert evidence[0].validity == "historical"


# --- degradation ------------------------------------------------------------ #


def test_an_unavailable_index_degrades_instead_of_failing(store, clock):
    class _Down:
        supports_lexical = True
        supports_metadata_filter = True

        def health(self):
            return False, "the vector index is unreachable"

        def query(self, *a, **k):
            raise AssertionError("query must not be attempted when the index is down")

    service = RetrievalService(
        state_store=store, memory_store=_Down(), embedder=FixtureEmbedder(), clock=clock
    )
    results, degraded = service.retrieve_supporting_memories(USER, query="anything")
    assert results == []
    assert degraded and "unreachable" in degraded


def test_a_query_failure_is_reported_not_raised(store, clock):
    class _Flaky:
        supports_lexical = True
        supports_metadata_filter = True

        def health(self):
            return True, None

        def query(self, *a, **k):
            raise RuntimeError("provider blew up")

    service = RetrievalService(
        state_store=store, memory_store=_Flaky(), embedder=FixtureEmbedder(), clock=clock
    )
    results, degraded = service.retrieve_supporting_memories(USER, query="anything")
    assert results == [] and degraded and "degraded" in degraded


# --- evidence budget -------------------------------------------------------- #


def test_the_evidence_budget_is_shared_across_the_whole_plan():
    budget = EvidenceBudget(max_tokens=20)
    assert budget.admit("a" * 40) is True  # ~10 tokens
    assert budget.admit("b" * 40) is True  # ~20 tokens
    assert budget.admit("c" * 40) is False, "the plan-wide budget must stop here"


def test_evidence_is_capped_per_task_and_deduplicated_by_source(store, memory_store, clock, service):
    add_source(store, clock, text="one note mentioning caching several times")
    for i in range(10):
        add_memory(
            store,
            memory_store,
            clock,
            memory_id=f"mem_{i}",
            content=f"caching files stale note {i}",
            project_id="proj_cerno",
        )
    task = add_task(store, clock, project_id="proj_cerno", title="caching files stale")
    evidence = service.evidence_for_task(USER, task, budget=EvidenceBudget(max_tokens=10_000), per_task=6)
    assert len(evidence) <= 6
    keys = {(e.source_id, e.source_version) for e in evidence}
    assert len(keys) == len(evidence), "related spans from one source are deduplicated"


# --- identifier lookup ------------------------------------------------------ #


def test_identifier_lookup_matches_aliases_exactly_first(store, clock, service):
    for project_id, title, aliases in [
        ("proj_cerno", "Cerno", ["cerno-review", "CRN-1"]),
        ("proj_other", "Cerno Notes", []),
    ]:
        project = Project(
            id=project_id,
            user_id=USER,
            created_at=clock.now(),
            updated_at=clock.now(),
            title=title,
            aliases=aliases,
        )
        with store.transaction(USER) as tx:
            tx.put("project", project)
    matches = service.lookup_identifier(USER, "Cerno")
    assert matches[0].id == "proj_cerno", "an exact title match ranks first"
    assert service.lookup_identifier(USER, "CRN-1")[0].id == "proj_cerno"
    assert service.lookup_identifier(USER, "nothing-here") == []


def test_project_history_is_read_directly_and_newest_first(store, memory_store, clock, service):
    add_source(store, clock)
    first = add_memory(
        store, memory_store, clock, memory_id="mem_old", content="first observation", project_id="proj_cerno"
    )
    clock.advance(hours=5)
    add_memory(
        store, memory_store, clock, memory_id="mem_new", content="second observation", project_id="proj_cerno"
    )
    history = service.get_project_history(USER, "proj_cerno")
    assert [m.id for m in history] == ["mem_new", "mem_old"]
    assert first.id in [m.id for m in history]


# --- chunking --------------------------------------------------------------- #


def test_chunking_keeps_the_clause_that_reverses_the_meaning():
    text = (
        "I looked at whole-file caching for the review. "
        "I tried caching whole files but it got stale after changes, so we abandoned it. "
        "Next I want to test diff-aware invalidation."
    )
    start = text.index("I tried")
    window = context_window(text, start=start, end=start + 10)
    assert "abandoned" in window, "dropping 'abandoned' would invert the meaning"
    assert "but" in window


def test_embedding_reuse_skips_unchanged_content(store, memory_store, clock):
    memory = Memory(
        id="mem_1",
        user_id=USER,
        created_at=clock.now(),
        updated_at=clock.now(),
        kind=MemoryKind.OBSERVATION,
        content="caching files stale",
        validity=MemoryValidity.CURRENT,
        effective_at=clock.now(),
        source_refs=[SourceRef(source_id="src_a", source_version=1)],
        content_hash=content_hash("caching files stale"),
        embedding_model_id="fixture-embed-v1",
        embedding_dimensions=64,
    )
    assert needs_embedding(memory, set()) is True
    assert needs_embedding(memory, {memory.content_hash}) is False


def test_content_hash_ignores_whitespace_only_edits():
    assert content_hash("a  b\n c") == content_hash("a b c")


def test_a_dimension_mismatch_is_rejected_rather_than_compared():
    from metis_contracts import ErrorCode, MetisError
    from metis_retrieval import cosine

    with pytest.raises(MetisError) as exc:
        cosine([0.1, 0.2], [0.1, 0.2, 0.3])
    assert exc.value.code is ErrorCode.VALIDATION_FAILED
