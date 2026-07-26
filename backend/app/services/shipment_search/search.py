"""Deterministic semantic search over indexed shipment summaries.

No LLM synthesis: results are a plain ranked list of shipment summaries,
which is sufficient for the target questions ("which shipments had weight
discrepancies", "which exporter had the most failed checks") and keeps
retrieval fully local and free. If the embedding provider is degraded or
unavailable, search falls back to a deterministic recency-ordered list
rather than failing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shipment_search import ShipmentSummaryVector
from app.services.regulatory.embeddings import EmbeddingProvider, get_embedding_provider


@dataclass
class ShipmentSearchMatch:
    workflow_id: str
    score: float
    summary: str


@dataclass
class ShipmentSearchOutput:
    status: str  # "ok" | "no_shipments_indexed"
    retrieval_mode: str  # "semantic" | "recency_fallback"
    results: list[ShipmentSearchMatch]


def _recency_fallback(rows: list[ShipmentSummaryVector], top_k: int) -> ShipmentSearchOutput:
    ordered = sorted(rows, key=lambda row: row.updated_at, reverse=True)[:top_k]
    return ShipmentSearchOutput(
        status="ok",
        retrieval_mode="recency_fallback",
        results=[
            ShipmentSearchMatch(workflow_id=str(row.workflow_id), score=0.0, summary=row.summary_text)
            for row in ordered
        ],
    )


def search_shipments(
    db: Session,
    query: str,
    *,
    top_k: int = 5,
    embedder: EmbeddingProvider | None = None,
) -> ShipmentSearchOutput:
    rows = list(db.execute(select(ShipmentSummaryVector)).scalars())
    if not rows:
        return ShipmentSearchOutput(status="no_shipments_indexed", retrieval_mode="semantic", results=[])

    embedder = embedder or get_embedding_provider()
    try:
        query_vector = np.asarray(embedder.embed_query(query), dtype=np.float32)
    except Exception:
        # The embedding provider itself is unavailable (e.g. a real model
        # failed to load with no fallback constructed) - a plain
        # recency-ordered list is still useful and needs no ranking model.
        return _recency_fallback(rows, top_k)

    compatible = (
        query_vector.ndim == 1
        and query_vector.size == embedder.dimension
        and all(
            row.embedding_model == embedder.model_name
            and row.embedding_dim == embedder.dimension
            and np.asarray(row.embedding, dtype=np.float32).ndim == 1
            and np.asarray(row.embedding, dtype=np.float32).size == query_vector.size
            for row in rows
        )
    )
    if not compatible:
        # Comparing vectors from different models (or merely different
        # dimensions) is invalid. Keep this read path free and deterministic;
        # the next normal indexing pass can refresh stale rows.
        return _recency_fallback(rows, top_k)

    scored = sorted(
        (
            (float(np.dot(query_vector, np.asarray(row.embedding, dtype=np.float32))), row)
            for row in rows
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )[:top_k]
    return ShipmentSearchOutput(
        status="ok",
        # A degraded (hashing) embedder still ranks meaningfully; it is
        # reported, not treated as a reason to discard ranking entirely -
        # the same pattern app/services/regulatory/retrieval.py already uses.
        retrieval_mode="semantic_degraded" if embedder.degraded else "semantic",
        results=[
            ShipmentSearchMatch(workflow_id=str(row.workflow_id), score=score, summary=row.summary_text)
            for score, row in scored
        ],
    )
