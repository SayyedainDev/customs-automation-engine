from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentType(str, Enum):
    COMMERCIAL_INVOICE = "commercial_invoice"
    PACKING_LIST = "packing_list"
    EXPORT_FORM = "export_form"


class DocumentBase(BaseModel):
    file_name: str = Field(min_length=1, max_length=255, examples=["invoice_001.pdf"])
    document_type: DocumentType
    exporter_name: str = Field(min_length=2, examples=["Sialkot Surgical Exports Pvt Ltd"])


class DocumentCreate(DocumentBase):
    """Payload the client sends when registering a new document."""

    pass


class DocumentResponse(DocumentBase):
    """What the API returns — includes server-generated fields."""

    id: int
    status: str = "pending_extraction"
    uploaded_at: datetime


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    original_filename: str
    stored_filename: str
    size_bytes: int
    status: str


class DocumentMetadataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID = Field(validation_alias="id")
    original_filename: str
    stored_filename: str
    file_extension: str
    content_type: str = Field(validation_alias="mime_type")
    size_bytes: int
    status: str
    page_count: int | None
    character_count: int | None
    extracted_at: datetime | None
    structured_extraction_status: str
    structured_extracted_at: datetime | None
    uploaded_at: datetime


class DocumentExtractionResponse(BaseModel):
    """Small extraction summary; the potentially large text stays in PostgreSQL."""

    document_id: UUID
    status: str
    page_count: int
    character_count: int
