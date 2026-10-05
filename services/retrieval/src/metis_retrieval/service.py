"""The narrow, server-controlled retrieval operations the planner may call.

Separation of duties, enforced here:

* the authoritative store answers *what is true now* — deadlines, completion,
  cancellation, ownership, versions
* semantic memory supplies *why a task exists and what was tried*

A retrieved memory can never override newer authoritative state, and every
candidate is hydrated against that state before it is admitted as evidence.
Deleted, tombstoned and version-mismatched hits are dropped; a cross-owner hit
is dropped and logged as a bug.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from metis_contracts import (
    Event,
    Evidence,
    Memory,
    Project,
    Task,
    settings,
)
from metis_contracts.entities import AvailabilityBlock
from metis_contracts.enums import TASK_OPEN_STATES
from metis_contracts.ports import Clock, Embedder, MemoryStore, StateStore

log = logging.getLogger("metis.retrieval.service")


#: Rough token estimate for the shared evidence budget.
def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class ObligationScan:
    """Everything due or open in the horizon, plus whether the scan completed."""

    tasks: list[Task] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    complete: bool = True
    pages_read: int = 0

    @property
    def incomplete_reason(self) -> str | None:
        return None if self.complete else "the obligation scan hit its configured read limit"


@dataclass
class EvidenceBudget:
    """Total evidence budget across the whole plan, not per task.

    Without this, "at most 6 per task" multiplied by a dozen tasks quietly
    becomes an enormous prompt.
    """

    max_tokens: int
    used_tokens: int = 0

    def admit(self, text: str) -> bool:
        cost = _tokens(text)
        if self.used_tokens + cost > self.max_tokens:
            return False
        self.used_tokens += cost
        return True


class RetrievalService:
    """Implements the planner-facing operations and nothing wider."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        memory_store: MemoryStore,
        embedder: Embedder,
        clock: Clock,
    ) -> None:
        self._state = state_store
        self._memory = memory_store
        self._embedder = embedder
        self._clock = clock

    # -- authoritative ------------------------------------------------- #

    def list_eligible_obligations(
        self, user_id: str, *, local_date: date, horizon_days: int | None = None
    ) -> ObligationScan:
        """Every near-term obligation, found by structured query — not by similarity.

        A task with an explicit date must appear even when it is semantically
        unrelated to the planning request, so this never consults the vector
        store. Pagination is bounded; hitting the bound sets ``complete=False``
        and the plan may then not claim full coverage.
        """
        horizon = local_date + timedelta(days=horizon_days or settings().limits.planning_horizon_days)
        page_limit = settings().limits.planning_read_page_limit
        scan = ObligationScan()

        cursor: str | None = None
        while scan.pages_read < page_limit:
            page = self._state.list_tasks(user_id, cursor=cursor, limit=100)
            scan.pages_read += 1
            scan.tasks.extend(t for t in page.items if t.state in TASK_OPEN_STATES)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        else:
            scan.complete = False

        cursor = None
        while scan.pages_read < page_limit * 2:
            event_page = self._state.list_events(user_id, cursor=cursor, limit=100)
            scan.pages_read += 1
            scan.events.extend(
                e
                for e in event_page.items
                if e.attendance_status.value not in ("canceled", "declined")
                and (e.local_date is None or e.local_date <= horizon)
            )
            if not event_page.next_cursor:
                break
            cursor = event_page.next_cursor
        else:
            scan.complete = False

        return scan

    def get_current_task_state(self, user_id: str, task_id: str) -> Task | None:
        task = self._state.get(user_id, "task", task_id)
        return task if isinstance(task, Task) else None

    def get_availability(self, user_id: str, local_date: date) -> list[AvailabilityBlock]:
        return self._state.list_availability(user_id, local_date)

    def lookup_identifier(self, user_id: str, identifier: str) -> list[Project]:
        """Literal lookup for project names, repo names and error codes.

        Aliases are matched directly because ordinary tokenisation — and a
        paraphrase-tuned embedding — both lose exact identifiers.
        """
        needle = identifier.strip().casefold()
        if not needle:
            return []
        matches: list[Project] = []
        cursor: str | None = None
        lister = getattr(self._state, "list_projects", None)
        while lister is not None:
            page = lister(user_id, cursor=cursor, limit=100)
            for project in page.items:
                names = [project.title, *project.aliases]
                if any(needle == n.strip().casefold() for n in names):
                    matches.insert(0, project)  # exact match ranks first
                elif any(needle in n.casefold() for n in names):
                    matches.append(project)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        return matches

    # -- semantic ------------------------------------------------------- #

    def get_project_history(self, user_id: str, project_id: str, *, limit: int = 20) -> list[Memory]:
        """Chronological project history, read directly rather than searched.

        When the project identity is already known, a similarity search is
        strictly worse than reading its own records.
        """
        page = self._state.list_memories(user_id, project_id=project_id, limit=limit)
        return sorted(page.items, key=lambda m: m.effective_at, reverse=True)

    def retrieve_supporting_memories(
        self,
        user_id: str,
        *,
        query: str,
        project_id: str | None = None,
        limit: int | None = None,
        include_historical: bool = True,
    ) -> tuple[list[tuple[Memory, float]], str | None]:
        """Scoped candidate retrieval, hydrated against authoritative state.

        ``include_historical`` defaults to True because a failed attempt is
        exactly the evidence that explains a blocker — filtering all history
        away would make Metis re-suggest an approach the user already tried.
        """
        candidates = limit or settings().limits.retrieval_candidates
        available, degraded = self._memory.health()
        if not available:
            # Planning and reminders must keep working without the index.
            return [], degraded or "the memory index is unavailable"

        vector = None
        if getattr(self._memory, "supports_lexical", False) is False or query.strip():
            try:
                vector = self._embedder.embed([query])[0]
            except Exception as exc:
                log.warning("embedding the query failed: %s", exc)
                vector = None

        try:
            hits = self._memory.query(
                user_id,
                vector=vector,
                text=query if getattr(self._memory, "supports_lexical", False) else None,
                project_id=project_id,
                include_historical=include_historical,
                limit=candidates,
            )
        except Exception as exc:
            return [], f"retrieval degraded: {exc}"

        hydrated: list[tuple[Memory, float]] = []
        stale = 0
        for memory_id, score, _metadata in hits:
            memory = self._state.get(user_id, "memory", memory_id)
            if memory is None:
                stale += 1  # deleted or tombstoned since indexing
                continue
            if memory.user_id != user_id:
                log.error("memory %s belongs to another owner; dropped before any prompt", memory_id)
                continue
            if not self._source_is_current(user_id, memory):
                stale += 1
                continue
            hydrated.append((memory, score))

        if stale:
            log.info("dropped %d stale or inaccessible candidates", stale)
        return hydrated, None

    def _source_is_current(self, user_id: str, memory: Memory) -> bool:
        """A memory indexed from an older source revision is not evidence for now."""
        for ref in memory.source_refs:
            source = self._state.get(user_id, "source", ref.source_id)
            if source is None:
                return False
            if ref.source_version < source.current_version and memory.validity.value == "current":
                return False
        return True

    # -- evidence assembly ----------------------------------------------- #

    def evidence_for_task(
        self,
        user_id: str,
        task: Task,
        *,
        budget: EvidenceBudget,
        per_task: int | None = None,
    ) -> list[Evidence]:
        """At most ``per_task`` items, within the plan-wide token budget.

        Related spans from the same source are deduplicated so one note cannot
        occupy the whole budget.
        """
        cap = per_task or settings().limits.retrieval_evidence_per_task
        query = " ".join(filter(None, [task.title, task.body or ""]))[:500]
        memories, degraded = self.retrieve_supporting_memories(
            user_id, query=query, project_id=task.project_id
        )
        if degraded:
            log.info("no evidence for task %s: %s", task.id, degraded)
            return []

        seen_sources: set[str] = set()
        evidence: list[Evidence] = []
        for memory, _score in memories:
            if len(evidence) >= cap:
                break
            ref = memory.source_refs[0] if memory.source_refs else None
            if ref is None:
                continue
            key = f"{ref.source_id}:{ref.source_version}"
            if key in seen_sources:
                continue
            if not budget.admit(memory.content):
                break
            seen_sources.add(key)
            evidence.append(
                Evidence(
                    source_id=ref.source_id,
                    source_version=ref.source_version,
                    span_ref=ref.span_ref,
                    memory_id=memory.id,
                    excerpt=memory.content[:2000],
                    recorded_at=memory.effective_at,
                    # A failed attempt stays retrievable, labelled historical.
                    validity="historical"
                    if memory.kind.value in ("attempt", "work_log")
                    else memory.validity.value,
                )
            )
        return evidence
