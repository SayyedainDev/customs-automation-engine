from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DocumentRecord(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_name: Mapped[str] = mapped_column(String(255))
    document_type: Mapped[str] = mapped_column(String(50))
    exporter_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="pending_extraction")
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class DocumentUploadRecord(Base):
    __tablename__ = "document_uploads"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(255), unique=True)
    file_extension: Mapped[str] = mapped_column(String(10))
    mime_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int]
    status: Mapped[str] = mapped_column(String(50), default="uploaded")
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_pages: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    ocr_pages: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    character_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    structured_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    structured_extraction_status: Mapped[str] = mapped_column(
        String(50),
        default="pending",
    )
    structured_extraction_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    structured_extraction_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    structured_extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    indexing_status: Mapped[str] = mapped_column(
        String(50),
        default="pending",
    )
    indexing_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
