"""Memory ingestion: typed records -> short memory units -> vectors.

Chunking rules that matter more than the search algorithm:

* one unit per *meaning* (observation, attempt, decision, blocker, idea,
  intention), never one per JSON property
* enough surrounding context to preserve negation, conditionals and the
  difference between an idea and a decision — if "we abandoned this" is lost by
  chunking, the fix belongs here, not in the ranker
* embeddings computed only for new or materially changed text, keyed by content
  hash plus model id and dimension
"""

from __future__ import annotations

import logging
from typing import Any

from metis_contracts import (
    Memory,
    MemoryKind,
    MemoryValidity,
    SourceArtifact,
    SourceRef,
    new_id,
)
from metis_contracts.enums import ExtractedItemKind

from .embedder import content_hash

log = logging.getLogger("metis.retrieval.ingestion")

#: Extraction item kinds -> memory kinds. Items with no mapping create no memory.
KIND_MAP: dict[ExtractedItemKind, MemoryKind] = {
    ExtractedItemKind.THOUGHT: MemoryKind.IDEA,
    ExtractedItemKind.DECISION: MemoryKind.DECISION,
    ExtractedItemKind.WORK_LOG: MemoryKind.WORK_LOG,
    ExtractedItemKind.TASK: MemoryKind.COMMITMENT,
    ExtractedItemKind.EVENT_INTENT: MemoryKind.IDEA,
    ExtractedItemKind.PREFERENCE: MemoryKind.PREFERENCE,
}

#: Phrases that must survive chunking. Their presence widens the window so a
#: unit never loses the clause that reverses its meaning.
MEANING_CARRIERS = (
    "not", "no", "never", "without", "abandoned", "failed", "stale", "instead",
    "but", "unless", "stopped", "gave up", "doesn't", "don't", "won't",
)


def context_window(text: str, *, start: int, end: int, pad: int = 160) -> str:
    """Widen a span to its sentence-ish neighbourhood.

    The window is widened further when a nearby clause carries negation or an
    abandonment marker, because dropping it would invert the meaning.
    """
    lo, hi = max(0, start - pad), min(len(text), end + pad)
    snippet = text[lo:hi]
    if any(carrier in text.lower() for carrier in MEANING_CARRIERS):
        lo, hi = max(0, start - pad * 2), min(len(text), end + pad * 2)
        snippet = text[lo:hi]
    return " ".join(snippet.split())


def memories_from_proposal(
    proposal: Any, source: SourceArtifact, *, clock: Any, embedding_model_id: str, dimensions: int
) -> list[Memory]:
    """Build one memory unit per meaningful extracted item."""
    text = source.current_text() or ""
    spans = {span.span_ref: span for span in source.spans}
    now = clock.now()
    memories: list[Memory] = []

    for item in proposal.items:
        kind = KIND_MAP.get(item.kind)
        if kind is None:
            continue
        ref = item.evidence[0] if item.evidence else None
        if ref is None:
            continue  # a memory without a source cannot be evidence
        span = spans.get(ref.span_ref or "")
        content = (
            context_window(text, start=span.start_char, end=span.end_char)
            if span
            else " ".join(filter(None, [item.title, item.body or ""]))
        )
        if not content.strip():
            continue
        memories.append(
            Memory(
                id=new_id("mem"),
                user_id=source.user_id,
                created_at=now,
                updated_at=now,
                kind=kind,
                content=content[:8000],
                project_id=None,
                validity=MemoryValidity.CURRENT,
                effective_at=source.captured_at,
                source_refs=[
                    SourceRef(
                        source_id=ref.source_id,
                        source_version=ref.source_version,
                        span_ref=ref.span_ref,
                    )
                ],
                content_hash=content_hash(content),
                embedding_model_id=embedding_model_id,
                embedding_dimensions=dimensions,
                indexed_source_version=ref.source_version,
            )
        )
    return memories


def needs_embedding(memory: Memory, known_hashes: set[str]) -> bool:
    """Reuse an unchanged embedding rather than paying for it again."""
    return memory.content_hash not in known_hashes


def mark_superseded(previous: Memory, *, successor_id: str, clock: Any) -> Memory:
    """Supersede rather than delete: past alternatives stay inspectable.

    A high similarity score must not keep a superseded version in play as if it
    were current.
    """
    return previous.model_copy(
        update={
            "validity": MemoryValidity.SUPERSEDED,
            "supersedes_id": previous.supersedes_id,
            "updated_at": clock.now(),
            "entity_refs": [*previous.entity_refs, successor_id],
        }
    )
