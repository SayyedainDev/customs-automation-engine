"""Persistent dense vector store for regulatory child chunks.

Backed by the project's SQL database (Postgres in production, the SQLite index /
test DB otherwise), reusing the existing ``regulatory_chunks`` records and their
metadata. This is the persistent collection; a ChromaDB backend could be swapped
in behind the same functions, but the SQL store keeps one system, survives
restarts and needs no extra service.

Idempotency and freshness:
- ``chunk_id`` is the stable vector record ID (one vector per child chunk).
- A vector is (re)embedded only when it is new, the source ``document_checksum``
  changed, the embedding model changed, the index version changed, or a full
  rebuild is requested.
- Vectors whose child chunk no longer exists are deleted (stale removal).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.regulatory import RegulatoryChunk, RegulatoryChunkVector
from app.services.regulatory.embeddings import (
    EmbeddingProvider,
    get_embedding_provider,
)

VECTOR_INDEX_VERSION = "vec-index-v1"


@dataclass
class VectorIndexReport:
    chunks_discovered: int = 0
    chunks_embedded: int = 0
    chunks_skipped: int = 0
    chunks_updated: int = 0
    stale_removed: int = 0
    failures: int = 0
    embedding_model: str = ""
    embedding_dim: int = 0
    index_version: str = VECTOR_INDEX_VERSION
    collection: str = ""
    degraded: bool = False
    execution_seconds: float = 0.0


def _chunk_meta(chunk: RegulatoryChunk) -> dict:
    return {
        "source_document": chunk.source_document,
        "source_path": chunk.source_path,
        "source_url": chunk.source_url,
        "sro_number": chunk.sro_number,
        "page_number": chunk.page_number,
        "section": chunk.section,
        "issuing_authority": chunk.issuing_authority,
        "document_type": chunk.document_type,
        "pct_codes": chunk.pct_codes,
        "validation_status": chunk.validation_status,
        "effective_date": chunk.effective_date.isoformat() if chunk.effective_date else None,
        "legal_cutoff_date": (
            chunk.legal_cutoff_date.isoformat() if chunk.legal_cutoff_date else None
        ),
        "rule_data_version": chunk.rule_data_version,
    }


def build_vector_index(
    db: Session,
    embedder: EmbeddingProvider | None = None,
    *,
    rebuild: bool = False,
) -> VectorIndexReport:
    """Embed child chunks into the persistent vector store (idempotently)."""
    embedder = embedder or get_embedding_provider()
    settings = get_settings()
    collection = settings.regulatory_vector_collection
    started = time.perf_counter()

    children = list(
        db.execute(
            select(RegulatoryChunk).where(RegulatoryChunk.is_parent.is_(False))
        ).scalars()
    )
    existing = {
        vector.chunk_id: vector
        for vector in db.execute(
            select(RegulatoryChunkVector).where(
                RegulatoryChunkVector.collection == collection
            )
        ).scalars()
    }

    report = VectorIndexReport(
        chunks_discovered=len(children),
        embedding_model=embedder.model_name,
        embedding_dim=embedder.dimension,
        collection=collection,
        degraded=embedder.degraded,
    )

    child_ids: set[str] = set()
    to_embed: list[RegulatoryChunk] = []
    for child in children:
        child_ids.add(child.chunk_id)
        vector = existing.get(child.chunk_id)
        fresh = (
            vector is not None
            and not rebuild
            and vector.document_checksum == child.document_checksum
            and vector.embedding_model == embedder.model_name
            and vector.index_version == VECTOR_INDEX_VERSION
        )
        if fresh:
            report.chunks_skipped += 1
        else:
            to_embed.append(child)

    if to_embed:
        try:
            vectors = embedder.embed([child.text for child in to_embed])
        except Exception:
            report.failures += len(to_embed)
            vectors = None
        if vectors is not None:
            for child, vector_row in zip(to_embed, vectors):
                embedding = [float(value) for value in vector_row.tolist()]
                row = existing.get(child.chunk_id)
                if row is not None:
                    row.embedding = embedding
                    row.embedding_model = embedder.model_name
                    row.embedding_dim = embedder.dimension
                    row.document_checksum = child.document_checksum
                    row.index_version = VECTOR_INDEX_VERSION
                    row.parent_chunk_id = child.parent_chunk_id
                    row.meta = _chunk_meta(child)
                    report.chunks_updated += 1
                else:
                    db.add(
                        RegulatoryChunkVector(
                            chunk_id=child.chunk_id,
                            parent_chunk_id=child.parent_chunk_id,
                            collection=collection,
                            embedding=embedding,
                            embedding_model=embedder.model_name,
                            embedding_dim=embedder.dimension,
                            document_checksum=child.document_checksum,
                            index_version=VECTOR_INDEX_VERSION,
                            meta=_chunk_meta(child),
                        )
                    )
                    report.chunks_embedded += 1

    for chunk_id, vector in existing.items():
        if chunk_id not in child_ids:
            db.delete(vector)
            report.stale_removed += 1

    db.commit()
    report.execution_seconds = round(time.perf_counter() - started, 4)
    return report


def get_vectors_for(
    db: Session,
    chunk_ids: list[str],
    *,
    embedding_model: str,
    embedding_dim: int,
) -> dict[str, np.ndarray]:
    """Return persisted embeddings compatible with the active provider.

    A vector produced by a different model is not comparable even when it has
    the same length. Incompatible rows are deliberately treated as cache misses
    by retrieval and embedded in memory with the active provider; the stored
    index remains untouched.
    """
    if not chunk_ids:
        return {}
    settings = get_settings()
    rows = db.execute(
        select(RegulatoryChunkVector).where(
            RegulatoryChunkVector.collection == settings.regulatory_vector_collection,
            RegulatoryChunkVector.chunk_id.in_(chunk_ids),
            RegulatoryChunkVector.embedding_model == embedding_model,
            RegulatoryChunkVector.embedding_dim == embedding_dim,
        )
    ).scalars()
    return {
        row.chunk_id: np.asarray(row.embedding, dtype=np.float32) for row in rows
    }


def get_vector_index_meta(db: Session) -> dict:
    settings = get_settings()
    rows = list(
        db.execute(
            select(RegulatoryChunkVector).where(
                RegulatoryChunkVector.collection
                == settings.regulatory_vector_collection
            )
        ).scalars()
    )
    models = {row.embedding_model for row in rows}
    versions = {row.index_version for row in rows}
    return {
        "collection": settings.regulatory_vector_collection,
        "vector_count": len(rows),
        "embedding_model": next(iter(models)) if len(models) == 1 else sorted(models),
        "index_version": next(iter(versions)) if len(versions) == 1 else sorted(versions),
        "dimension": rows[0].embedding_dim if rows else 0,
    }
