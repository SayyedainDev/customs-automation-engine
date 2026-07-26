"""Persistent dense vector store for historical-shipment semantic search.

Mirrors ``app/models/regulatory.py``'s ``RegulatoryChunkVector`` pattern
(embeddings stored as a JSON list of floats in the project's own SQL
database, no separate vector service) but for a different domain: one row
per finalized ``CustomsAuditWorkflow``, not per regulatory legal-text chunk.
The two are intentionally not the same table - a shipment summary has none
of a regulatory chunk's legal provenance fields (SRO number, legal cutoff
date, issuing authority), and conflating them would blur two unrelated
concepts under one schema.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ShipmentSummaryVector(Base):
    """One dense vector per finalized shipment (workflow), for semantic search.

    ``workflow_id`` is the primary key rather than a separate chunk id: unlike
    regulatory text, a shipment gets exactly one summary, not a parent/child
    split, so there is nothing to key on besides the workflow itself.
    """

    __tablename__ = "shipment_summary_vectors"

    workflow_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("customs_audit_workflows.id"), primary_key=True
    )
    summary_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[Any]] = mapped_column(JSON)
    embedding_model: Mapped[str] = mapped_column(String(255))
    embedding_dim: Mapped[int] = mapped_column(Integer)
    summary_checksum: Mapped[str] = mapped_column(String(80), index=True)
    index_version: Mapped[str] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
