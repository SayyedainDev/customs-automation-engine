"""Process-local cache of the regulatory dense-vector matrix.

Profiling the live corpus showed the dense stage, not BM25, was the dominant
per-query cost: ``get_vectors_for`` read every stored embedding back out of
PostgreSQL as JSON on every request - 296 ms for 374 chunks, and linear in the
corpus. BM25 construction over the same corpus was 4 ms.

PostgreSQL 15 here has no ``pgvector``, so there is no server-side ANN index to
delegate to. The smallest fix that removes the per-request cost is to read the
matrix once and keep it: embeddings only change when the vector index is
rebuilt, which is an explicit maintenance action.

Staleness is detected with one cheap aggregate (row count plus the latest
``updated_at``) rather than by trusting a TTL, so a rebuilt index is picked up
on the next query without a restart.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.regulatory import RegulatoryChunkVector

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorMatrix:
    """Row-normalized embeddings plus the chunk ids they belong to."""

    chunk_ids: tuple[str, ...]
    matrix: np.ndarray  # shape (n_chunks, dim), L2-normalized rows
    embedding_model: str
    dimension: int

    @property
    def index_by_chunk_id(self) -> dict[str, int]:
        return {chunk_id: i for i, chunk_id in enumerate(self.chunk_ids)}

    def similarities(self, query_vector: np.ndarray) -> np.ndarray:
        if self.matrix.size == 0:
            return np.zeros(0)
        query = query_vector / (np.linalg.norm(query_vector) or 1.0)
        if query.size != self.matrix.shape[1]:
            return np.zeros(self.matrix.shape[0])
        return self.matrix @ query


_lock = threading.Lock()
_cached: VectorMatrix | None = None
_cached_signature: tuple[str, int, int, str] | None = None


def _signature(db: Session, embedding_model: str, dimension: int) -> tuple[str, int, int, str]:
    """Cheap staleness probe: how many vectors, and when was the newest written."""
    row = db.execute(
        select(func.count(), func.max(RegulatoryChunkVector.updated_at)).where(
            RegulatoryChunkVector.embedding_model == embedding_model,
            RegulatoryChunkVector.embedding_dim == dimension,
        )
    ).one()
    return (embedding_model, dimension, int(row[0] or 0), str(row[1]))


def get_vector_matrix(
    db: Session, *, embedding_model: str, dimension: int
) -> VectorMatrix:
    """The corpus embedding matrix, loaded at most once per index generation."""
    global _cached, _cached_signature
    signature = _signature(db, embedding_model, dimension)
    with _lock:
        if _cached is not None and _cached_signature == signature:
            return _cached

    rows = db.execute(
        select(RegulatoryChunkVector.chunk_id, RegulatoryChunkVector.embedding).where(
            RegulatoryChunkVector.embedding_model == embedding_model,
            RegulatoryChunkVector.embedding_dim == dimension,
        )
    ).all()
    chunk_ids: list[str] = []
    vectors: list[list[float]] = []
    for chunk_id, embedding in rows:
        if not embedding or len(embedding) != dimension:
            continue
        chunk_ids.append(chunk_id)
        vectors.append(embedding)

    if vectors:
        matrix = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix = matrix / norms
    else:
        matrix = np.zeros((0, dimension), dtype=np.float32)

    built = VectorMatrix(
        chunk_ids=tuple(chunk_ids),
        matrix=matrix,
        embedding_model=embedding_model,
        dimension=dimension,
    )
    with _lock:
        _cached = built
        _cached_signature = signature
    logger.info(
        "Loaded regulatory vector matrix: %d vectors, dim %d, model %s.",
        len(chunk_ids),
        dimension,
        embedding_model,
    )
    return built


def reset_vector_matrix_cache() -> None:
    """Drop the cached matrix. Used by tests and after an index rebuild."""
    global _cached, _cached_signature
    with _lock:
        _cached = None
        _cached_signature = None
