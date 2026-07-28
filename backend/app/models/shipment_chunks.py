"""Persistence for shipment document chunks and vectors."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ShipmentDocumentChunk(Base):
    __tablename__ = "shipment_document_chunks"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    shipment_id: Mapped[UUID] = mapped_column(
        Uuid, index=True
    )
    workflow_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    document_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("document_uploads.id"), index=True
    )
    document_version: Mapped[int] = mapped_column(Integer, default=1)
    document_type: Mapped[str] = mapped_column(String(128), index=True)
    document_name: Mapped[str] = mapped_column(String(255))
    page_number: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(255))
    pct_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(64), default="uploaded_document")
    parent_chunk_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    child_chunk_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    is_parent: Mapped[bool] = mapped_column(Boolean, default=False)
    child_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    
    text: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
