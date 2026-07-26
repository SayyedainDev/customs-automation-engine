"""Persistence for ingested regulatory evidence chunks (Stage 3).

Parent/child chunking is stored in one table: child chunks (smaller, used for
search) point to their parent chunk (larger legal section, returned as final
evidence) via ``parent_chunk_id``. Every chunk carries full provenance so a
retrieval result can always be traced back to an exact official source, page
and clause.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RegulatoryChunk(Base):
    __tablename__ = "regulatory_chunks"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    # Deterministic, human-readable identity; stable across re-ingestion.
    chunk_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    parent_chunk_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # "parent" | "child"
    is_parent: Mapped[bool] = mapped_column(Boolean, default=False)
    chunk_index: Mapped[int] = mapped_column(Integer)

    # Source provenance.
    source_document: Mapped[str] = mapped_column(String(512))
    source_path: Mapped[str] = mapped_column(String(1024), index=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    document_checksum: Mapped[str] = mapped_column(String(80), index=True)
    issuing_authority: Mapped[str | None] = mapped_column(String(512), nullable=True)
    document_type: Mapped[str] = mapped_column(String(128))
    sro_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pct_codes: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    # Legal dates and validation.
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    legal_cutoff_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(64))

    # Versioning.
    rule_data_version: Mapped[str] = mapped_column(String(120))
    ingestion_version: Mapped[str] = mapped_column(String(64))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Payload.
    text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)


class RegulatoryChunkVector(Base):
    """Persistent dense vector for one regulatory child chunk (the vector store).

    ``chunk_id`` is the stable vector record ID. Legal metadata is copied into
    ``meta`` so this table works as a standalone collection (Chroma-parity),
    while still tracing back to the ``regulatory_chunks`` row of the same id.
    """

    __tablename__ = "regulatory_chunk_vectors"

    chunk_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    parent_chunk_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    collection: Mapped[str] = mapped_column(String(128), index=True)
    embedding: Mapped[list[Any]] = mapped_column(JSON)
    embedding_model: Mapped[str] = mapped_column(String(255))
    embedding_dim: Mapped[int] = mapped_column(Integer)
    document_checksum: Mapped[str] = mapped_column(String(80), index=True)
    index_version: Mapped[str] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
