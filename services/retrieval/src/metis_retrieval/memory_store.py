"""MemoryStore adapters, plus the capability map that keeps them honest.

Providers differ in what they support (metadata filters, lexical search, filter
operators). Rather than leaving that in comments, each adapter declares a
capability map and the retrieval service degrades explicitly when something is
missing.

Ownership filtering is applied twice on purpose: once in the query and once
when hydrating results. If a store ever returns another user's row it is
dropped before any text can reach a prompt.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from metis_contracts import AdapterMode, ErrorCode, Memory, MetisError, settings

from .embedder import cosine

log = logging.getLogger("metis.retrieval.store")


@dataclass(frozen=True)
class Capabilities:
    """What this provider can actually do."""

    metadata_filter: bool
    lexical: bool
    filter_operators: tuple[str, ...]
    max_filter_keys: int
    max_top_k: int


SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS vectors (
    user_id       TEXT NOT NULL,
    memory_id     TEXT NOT NULL,
    project_id    TEXT,
    kind          TEXT NOT NULL,
    validity      TEXT NOT NULL,
    source_id     TEXT,
    source_version INTEGER,
    content_hash  TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    dimensions    INTEGER NOT NULL,
    vector        TEXT NOT NULL,
    content       TEXT NOT NULL,
    indexed_at    TEXT NOT NULL,
    PRIMARY KEY (user_id, memory_id)
);

CREATE INDEX IF NOT EXISTS idx_vectors_scope ON vectors (user_id, project_id, validity);
CREATE INDEX IF NOT EXISTS idx_vectors_hash  ON vectors (user_id, content_hash, model_id);

-- Lexical index. Identifiers (project names, filenames, error codes) survive
-- here even when ordinary tokenisation or a paraphrase-tuned embedding loses them.
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    memory_id UNINDEXED, user_id UNINDEXED, content, tokenize = 'unicode61'
);
"""


class SqliteMemoryStore:
    """Local semantic + lexical memory.

    Exact cosine over the user's own rows. Personal-scale working sets are
    small, so an approximate index would be complexity with no payoff.

    ponytail: linear scan per query, bounded by the owner's row count. If a
    single user ever holds enough memories for this to matter, swap in the
    cloud adapter — the interface does not change.
    """

    mode = AdapterMode.LOCAL
    capabilities = Capabilities(
        metadata_filter=True,
        lexical=True,
        filter_operators=("eq", "in"),
        max_filter_keys=8,
        max_top_k=200,
    )

    def __init__(self, path: str | None = None) -> None:
        target = path or str(Path(settings().sqlite_path).with_name("memory.sqlite"))
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(target, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    @property
    def supports_metadata_filter(self) -> bool:
        return self.capabilities.metadata_filter

    @property
    def supports_lexical(self) -> bool:
        return self.capabilities.lexical

    def known_hashes(self, user_id: str, model_id: str) -> set[str]:
        """Content hashes already embedded with this model, for reuse."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT content_hash FROM vectors WHERE user_id=? AND model_id=?", (user_id, model_id)
            ).fetchall()
        return {r["content_hash"] for r in rows}

    def upsert(self, user_id: str, memories: Sequence[Memory], vectors: Sequence[Sequence[float]]) -> None:
        if len(memories) != len(vectors):
            raise MetisError(ErrorCode.VALIDATION_FAILED, "memories and vectors must be the same length")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for memory, vector in zip(memories, vectors, strict=True):
                    if memory.user_id != user_id:
                        raise MetisError(ErrorCode.FORBIDDEN, "memory owner mismatch")
                    source = memory.source_refs[0] if memory.source_refs else None
                    self._conn.execute(
                        """INSERT INTO vectors (user_id, memory_id, project_id, kind, validity,
                               source_id, source_version, content_hash, model_id, dimensions,
                               vector, content, indexed_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(user_id, memory_id) DO UPDATE SET
                               project_id=excluded.project_id, kind=excluded.kind,
                               validity=excluded.validity, source_id=excluded.source_id,
                               source_version=excluded.source_version,
                               content_hash=excluded.content_hash, model_id=excluded.model_id,
                               dimensions=excluded.dimensions, vector=excluded.vector,
                               content=excluded.content, indexed_at=excluded.indexed_at""",
                        (
                            user_id,
                            memory.id,
                            memory.project_id,
                            memory.kind.value,
                            memory.validity.value,
                            source.source_id if source else None,
                            source.source_version if source else None,
                            memory.content_hash,
                            memory.embedding_model_id or "unknown",
                            len(vector),
                            json.dumps(list(vector)),
                            memory.content,
                            (memory.indexed_at or memory.updated_at).isoformat(),
                        ),
                    )
                    self._conn.execute(
                        "DELETE FROM memory_fts WHERE memory_id=? AND user_id=?", (memory.id, user_id)
                    )
                    self._conn.execute(
                        "INSERT INTO memory_fts (memory_id, user_id, content) VALUES (?,?,?)",
                        (memory.id, user_id, memory.content),
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def query(
        self,
        user_id: str,
        *,
        vector: Sequence[float] | None = None,
        text: str | None = None,
        project_id: str | None = None,
        kinds: Sequence[str] = (),
        include_historical: bool = False,
        limit: int = 20,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Return ``(memory_id, score, metadata)``, highest score first.

        Scores rank candidates. They are not probabilities, urgency or
        priority, and the caller re-validates every hit against authoritative
        state before using it.
        """
        bounded = max(1, min(limit, self.capabilities.max_top_k))
        clauses = ["user_id = ?"]
        params: list[Any] = [user_id]
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if kinds:
            clauses.append(f"kind IN ({','.join('?' for _ in kinds)})")
            params.extend(kinds)
        if not include_historical:
            clauses.append("validity = 'current'")

        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM vectors WHERE {' AND '.join(clauses)}", params
            ).fetchall()

        lexical_scores: dict[str, float] = {}
        if text:
            lexical_scores = self._lexical(user_id, text, bounded * 3)

        scored: list[tuple[str, float, dict[str, Any]]] = []
        for row in rows:
            if row["user_id"] != user_id:  # defensive: never leak across owners
                continue
            semantic = 0.0
            if vector is not None:
                stored = json.loads(row["vector"])
                if len(stored) != len(vector):
                    # Written with a different model; skip rather than compare.
                    continue
                semantic = cosine(vector, stored)
            lexical = lexical_scores.get(row["memory_id"], 0.0)
            score = max(semantic, lexical) if vector is not None else lexical
            if score <= 0:
                continue
            scored.append(
                (
                    row["memory_id"],
                    score,
                    {
                        "project_id": row["project_id"],
                        "kind": row["kind"],
                        "validity": row["validity"],
                        "source_id": row["source_id"],
                        "source_version": row["source_version"],
                        "content": row["content"],
                        "semantic": semantic,
                        "lexical": lexical,
                    },
                )
            )
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:bounded]

    def _lexical(self, user_id: str, text: str, limit: int) -> dict[str, float]:
        """FTS5 match. Carries exact identifiers that embeddings blur."""
        terms = [t for t in "".join(c if c.isalnum() or c in "-_." else " " for c in text).split() if t]
        if not terms:
            return {}
        query = " OR ".join(f'"{t}"' for t in terms[:20])
        try:
            with self._lock:
                rows = self._conn.execute(
                    """SELECT memory_id, bm25(memory_fts) AS rank FROM memory_fts
                       WHERE user_id = ? AND memory_fts MATCH ? ORDER BY rank LIMIT ?""",
                    (user_id, query, limit),
                ).fetchall()
        except sqlite3.OperationalError:
            return {}
        # bm25 is negative-better; map into 0..1 without pretending it is a probability.
        return {r["memory_id"]: 1.0 / (1.0 + abs(float(r["rank"]))) for r in rows}

    def delete(self, user_id: str, memory_ids: Sequence[str]) -> None:
        if not memory_ids:
            return
        marks = ",".join("?" for _ in memory_ids)
        with self._lock:
            self._conn.execute(
                f"DELETE FROM vectors WHERE user_id=? AND memory_id IN ({marks})",
                (user_id, *memory_ids),
            )
            self._conn.execute(
                f"DELETE FROM memory_fts WHERE user_id=? AND memory_id IN ({marks})",
                (user_id, *memory_ids),
            )

    def health(self) -> tuple[bool, str | None]:
        try:
            with self._lock:
                self._conn.execute("SELECT 1 FROM vectors LIMIT 1").fetchone()
            return True, None
        except sqlite3.Error as exc:
            return False, str(exc)


class S3VectorsMemoryStore:
    """Direct S3 Vectors index behind the same interface.

    Capabilities are declared rather than assumed: the published metadata
    filtering support is narrower than a general search engine's, and there is
    no lexical mode, so the retrieval service keeps identifier lookups in the
    authoritative store instead of expecting this index to find them.
    """

    mode = AdapterMode.CLOUD
    capabilities = Capabilities(
        metadata_filter=True,
        lexical=False,
        filter_operators=("eq", "in"),
        max_filter_keys=8,
        max_top_k=100,
    )

    def __init__(self, bucket: str | None = None, index: str | None = None) -> None:
        import boto3

        cfg = settings().aws
        self._bucket = bucket or cfg.vector_bucket
        self._index = index or cfg.vector_index
        if not self._bucket or not self._index:
            raise ValueError("METIS_VECTOR_BUCKET and METIS_VECTOR_INDEX are required in cloud mode")
        self._client = boto3.client("s3vectors", region_name=cfg.region)

    @property
    def supports_metadata_filter(self) -> bool:
        return self.capabilities.metadata_filter

    @property
    def supports_lexical(self) -> bool:
        return self.capabilities.lexical

    def upsert(self, user_id: str, memories: Sequence[Memory], vectors: Sequence[Sequence[float]]) -> None:
        items = []
        for memory, vector in zip(memories, vectors, strict=True):
            if memory.user_id != user_id:
                raise MetisError(ErrorCode.FORBIDDEN, "memory owner mismatch")
            source = memory.source_refs[0] if memory.source_refs else None
            items.append(
                {
                    "key": f"{user_id}/{memory.id}",
                    "data": {"float32": list(vector)},
                    "metadata": {
                        # user_id is server-enforced metadata, not a hint.
                        "user_id": user_id,
                        "memory_id": memory.id,
                        "project_id": memory.project_id or "",
                        "kind": memory.kind.value,
                        "validity": memory.validity.value,
                        "source_id": source.source_id if source else "",
                        "source_version": str(source.source_version) if source else "",
                        "model_id": memory.embedding_model_id or "unknown",
                        "content": memory.content[:2000],
                    },
                }
            )
        for chunk in (items[i : i + 25] for i in range(0, len(items), 25)):
            try:
                self._client.put_vectors(vectorBucketName=self._bucket, indexName=self._index, vectors=chunk)
            except Exception as exc:
                raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, f"vector upsert failed: {exc}") from exc

    def query(
        self,
        user_id: str,
        *,
        vector: Sequence[float] | None = None,
        text: str | None = None,
        project_id: str | None = None,
        kinds: Sequence[str] = (),
        include_historical: bool = False,
        limit: int = 20,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        if vector is None:
            # No lexical mode here; the caller falls back to the authoritative store.
            return []
        metadata_filter: dict[str, Any] = {"user_id": user_id}
        if project_id:
            metadata_filter["project_id"] = project_id
        if kinds:
            metadata_filter["kind"] = {"$in": list(kinds)}
        if not include_historical:
            metadata_filter["validity"] = "current"
        try:
            response = self._client.query_vectors(
                vectorBucketName=self._bucket,
                indexName=self._index,
                queryVector={"float32": list(vector)},
                topK=max(1, min(limit, self.capabilities.max_top_k)),
                filter=metadata_filter,
                returnMetadata=True,
                returnDistance=True,
            )
        except Exception as exc:
            raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, f"vector query failed: {exc}") from exc

        results: list[tuple[str, float, dict[str, Any]]] = []
        for hit in response.get("vectors", []):
            metadata = hit.get("metadata", {}) or {}
            if metadata.get("user_id") != user_id:
                # Must never happen; dropped before any text reaches a prompt.
                log.error("vector store returned a row for another owner; dropped")
                continue
            distance = float(hit.get("distance", 1.0))
            results.append((str(metadata.get("memory_id")), max(0.0, 1.0 - distance), metadata))
        return results

    def delete(self, user_id: str, memory_ids: Sequence[str]) -> None:
        if not memory_ids:
            return
        keys = [f"{user_id}/{mid}" for mid in memory_ids]
        for chunk in (keys[i : i + 100] for i in range(0, len(keys), 100)):
            self._client.delete_vectors(vectorBucketName=self._bucket, indexName=self._index, keys=chunk)

    def health(self) -> tuple[bool, str | None]:
        try:
            self._client.get_index(vectorBucketName=self._bucket, indexName=self._index)
            return True, None
        except Exception as exc:
            return False, str(exc)


def build_memory_store() -> Any:
    from metis_contracts import Mode

    if settings().mode is Mode.CLOUD:
        return S3VectorsMemoryStore()
    return SqliteMemoryStore()
