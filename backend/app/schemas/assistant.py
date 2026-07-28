from pydantic import BaseModel, ConfigDict
from typing import Any
from uuid import UUID

class GuidanceRequest(BaseModel):
    conversation_id: UUID | None = None
    product: str
    pct_code: str
    destination: str
    planned_shipment_date: str | None = None
    question: str

class ShipmentChatRequest(BaseModel):
    conversation_id: UUID | None = None
    question: str
    technical_detail: bool = False

class SourceSchema(BaseModel):
    source_kind: str
    display_name: str | None = None
    document_type: str | None = None
    document_name: str | None = None
    page_number: int | None = None
    snippet: str | None = None
    document_id: str | None = None
    audit_revision_number: int | None = None
    status: str | None = None
    source_document: str | None = None
    evidence_status: str | None = None
    source_type: str | None = None
    
    model_config = ConfigDict(extra="allow")

class ChatResponse(BaseModel):
    conversation_id: UUID
    message_id: UUID
    mode: str
    answer_type: str | None = None
    answer: str
    audit_status: str | None = None
    audit_revision_number: int | None = None
    sources: list[SourceSchema] = []
    limitations: list[str] = []
    suggested_questions: list[str] = []

class DocumentGuidanceSchema(BaseModel):
    document_type: str
    display_name: str
    requirement: str
    reason: str
    evidence_status: str
    citations: list[Any] = []

class GuidanceResponse(BaseModel):
    conversation_id: UUID
    message_id: UUID
    mode: str = "pre_submission_guidance"
    supported_scope: bool = True
    pct_code: str | None = None
    product: str | None = None
    destination: str | None = None
    planned_shipment_date: str | None = None
    documents: list[DocumentGuidanceSchema] = []
    limitations: list[str] = []
    answer: str | None = None # Used for errors/clarifications outside scope
