"""Retrieval handlers: asynchronous indexing and vector purge."""

from __future__ import annotations

import logging

from metis_contracts import ErrorCode, EventEnvelope, EventType, MetisError
from metis_contracts.events import MemoryIndexPayload, PurgePayload
from metis_pipeline.dispatcher import DispatchContext, HandlerRegistry

from .ingestion import needs_embedding

log = logging.getLogger("metis.retrieval.handlers")

registry = HandlerRegistry()


@registry.register(EventType.MEMORY_INDEX_REQUESTED)
def handle_memory_index(envelope: EventEnvelope, context: DispatchContext) -> None:
    """Embed and index one memory, verifying freshness before writing.

    Indexing is asynchronous on purpose: the authoritative task view must stay
    usable before its vector update lands.
    """
    if context.memory_store is None or context.embedder is None:
        raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "no memory store or embedder is configured")

    payload = MemoryIndexPayload.model_validate(envelope.payload)
    state = context.state_store
    memory = state.get(envelope.user_id, "memory", payload.memory_id)
    if memory is None:
        raise MetisError(ErrorCode.TOMBSTONED, "the memory was deleted before indexing")

    source = state.get(envelope.user_id, "source", payload.source_id)
    if source is None:
        raise MetisError(ErrorCode.TOMBSTONED, "the source was deleted before indexing")
    if payload.source_version < source.current_version:
        raise MetisError(
            ErrorCode.REVISION_CONFLICT,
            "a corrected transcript supersedes this index request",
        )
    if memory.content_hash != payload.content_hash:
        raise MetisError(ErrorCode.REVISION_CONFLICT, "the memory content changed; a newer request exists")

    known = getattr(context.memory_store, "known_hashes", None)
    known_hashes = known(envelope.user_id, context.embedder.model_id) if known else set()
    if not needs_embedding(memory, known_hashes):
        log.info("reusing the existing embedding for %s", memory.id)
        vectors = None
    else:
        vectors = context.embedder.embed([memory.content])

    if vectors is not None:
        context.memory_store.upsert(envelope.user_id, [memory], vectors)

    with state.transaction(envelope.user_id) as tx:
        tx.put(
            "memory",
            memory.model_copy(
                update={
                    "indexed_at": context.clock.now(),
                    "indexed_source_version": payload.source_version,
                    "embedding_model_id": context.embedder.model_id,
                    "embedding_dimensions": context.embedder.dimensions,
                }
            ),
            expected_revision=memory.revision,
        )


@registry.register(EventType.MEMORY_PURGE_REQUESTED)
def handle_memory_purge(envelope: EventEnvelope, context: DispatchContext) -> None:
    """Remove vectors for deleted memories. Resumable via the payload cursor."""
    if context.memory_store is None:
        return
    payload = PurgePayload.model_validate(envelope.payload)
    context.memory_store.delete(envelope.user_id, [payload.target_id])
    context.state_store.purge(envelope.user_id, "memory", payload.target_id)
    log.info("purged memory %s from the index", payload.target_id)


__all__ = ["registry"]
