"""Embedder adapters.

Embeddings are reused aggressively: they are keyed by a content hash plus the
model id and dimension, so unchanged text is never re-embedded and an index
written with one model is never queried with another.
"""

from __future__ import annotations

import hashlib
import logging
import math
import struct
from collections.abc import Sequence
from typing import Any

from metis_contracts import AdapterMode, ErrorCode, MetisError, settings

log = logging.getLogger("metis.retrieval.embedder")


def content_hash(text: str) -> str:
    """Stable key for embedding reuse. Normalised so whitespace edits do not
    invalidate a cached vector."""
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


def normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(v * v for v in vector))
    return [v / magnitude for v in vector] if magnitude else vector


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        # A dimension mismatch means the index was written with another model.
        raise MetisError(
            ErrorCode.VALIDATION_FAILED,
            f"embedding dimension mismatch: {len(a)} vs {len(b)}; reindex before querying",
        )
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


class FixtureEmbedder:
    """Deterministic hashed embedding for local mode and tests.

    Not semantically meaningful across paraphrases — it is a stand-in so the
    indexing, filtering, eviction and evidence-hydration paths are all
    exercisable offline. Lexical retrieval carries the local demo's recall.
    """

    mode = AdapterMode.FIXTURE
    model_id = "fixture-embed-v1"

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            # Hashed bag of words: shared vocabulary produces similarity, which
            # is enough to exercise ranking without pretending to be a model.
            vector = [0.0] * self.dimensions
            for token in " ".join(text.split()).lower().split():
                digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
                index = struct.unpack("<Q", digest)[0] % self.dimensions
                vector[index] += 1.0
            vectors.append(normalize(vector))
        return vectors


class BedrockEmbedder:
    """Amazon Titan Text Embeddings via Bedrock.

    The output dimension and normalisation are explicit configuration, stored
    alongside every vector, so an index is never queried with an incompatible
    embedding.
    """

    mode = AdapterMode.CLOUD

    def __init__(self, model_id: str | None = None, dimensions: int | None = None) -> None:
        import boto3

        cfg = settings()
        self.model_id = model_id or cfg.models.embedding_model_id
        self.dimensions = dimensions or cfg.models.embedding_dimensions
        self._normalize = cfg.models.embedding_normalize
        self._client = boto3.client("bedrock-runtime", region_name=cfg.aws.region)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        import json

        vectors: list[list[float]] = []
        for text in texts:
            body = json.dumps(
                {"inputText": text, "dimensions": self.dimensions, "normalize": self._normalize}
            )
            try:
                response = self._client.invoke_model(modelId=self.model_id, body=body)
                payload = json.loads(response["body"].read())
            except Exception as exc:
                raise MetisError(
                    ErrorCode.PROVIDER_UNAVAILABLE, f"embedding call failed: {exc}"
                ) from exc
            vector = payload.get("embedding")
            if not isinstance(vector, list) or len(vector) != self.dimensions:
                raise MetisError(
                    ErrorCode.VALIDATION_FAILED,
                    f"embedding model returned {len(vector or [])} dimensions, expected {self.dimensions}",
                )
            vectors.append(vector)
        return vectors

    def estimate_micro_usd(self, *, characters: int) -> int:
        rate = settings().models.embedding_micro_usd_per_1k
        tokens = max(1, characters // 4)
        return -(-tokens * rate // 1000)


def build_embedder() -> Any:
    cfg = settings()
    if cfg.uses_real_inference:
        return BedrockEmbedder()
    return FixtureEmbedder()
