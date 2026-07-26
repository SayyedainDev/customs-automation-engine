"""Persistent dense vector store for historical-shipment semantic search.

Reuses the exact architecture ``app/services/regulatory/vector_store.py``
already established (local/free sentence-transformer embeddings, one JSON
list-of-floats column, idempotent re-embed on checksum/model/version
change) - and the same embedding-provider singleton, so the model is loaded
once for the whole process regardless of which feature asks for it first.
"""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from app.models.customs_audit import CustomsAuditWorkflow
from app.models.shipment_search import ShipmentSummaryVector
from app.services.regulatory.embeddings import EmbeddingProvider, get_embedding_provider
from app.services.shipment_search.summary import build_shipment_summary_text

SHIPMENT_VECTOR_INDEX_VERSION = "shipment-vec-index-v1"


def index_shipment_summary(
    db: Session,
    workflow: CustomsAuditWorkflow,
    embedder: EmbeddingProvider | None = None,
) -> ShipmentSummaryVector | None:
    """Embed and persist one shipment's summary. Idempotent on unchanged input.

    Returns ``None`` (no-op) when the summary content, embedding model and
    index version all already match the stored row - no Groq/embedding cost
    for repeated indexing of an unchanged shipment.
    """
    embedder = embedder or get_embedding_provider()
    text = build_shipment_summary_text(workflow)
    checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()

    existing = db.get(ShipmentSummaryVector, workflow.id)
    fresh = (
        existing is not None
        and existing.summary_checksum == checksum
        and existing.embedding_model == embedder.model_name
        and existing.index_version == SHIPMENT_VECTOR_INDEX_VERSION
    )
    if fresh:
        return None

    vector = embedder.embed([text])[0]
    embedding = [float(value) for value in vector.tolist()]
    meta = {
        "deterministic_status": workflow.deterministic_status,
        "requires_human_review": workflow.requires_human_review,
    }

    if existing is not None:
        existing.summary_text = text
        existing.embedding = embedding
        existing.embedding_model = embedder.model_name
        existing.embedding_dim = embedder.dimension
        existing.summary_checksum = checksum
        existing.index_version = SHIPMENT_VECTOR_INDEX_VERSION
        existing.meta = meta
        row = existing
    else:
        row = ShipmentSummaryVector(
            workflow_id=workflow.id,
            summary_text=text,
            embedding=embedding,
            embedding_model=embedder.model_name,
            embedding_dim=embedder.dimension,
            summary_checksum=checksum,
            index_version=SHIPMENT_VECTOR_INDEX_VERSION,
            meta=meta,
        )
        db.add(row)
    db.commit()
    return row
