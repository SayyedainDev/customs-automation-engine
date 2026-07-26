from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.supporting_documents import SupportingDocumentRef


class StartWorkflowRequest(BaseModel):
    commercial_invoice_document_id: UUID
    packing_list_document_id: UUID | None = None
    additional_document_ids: list[UUID] = Field(default_factory=list)
    shipment_date: date | None = None
    letter_of_credit_date: date | None = None
    additional_uploaded_document_types: list[str] = Field(default_factory=list)
    # Actual uploaded supporting documents. Unlike the type strings above, these
    # are read, classified and cross-checked before they can count as present.
    supporting_documents: list[SupportingDocumentRef] = Field(default_factory=list)


class WorkflowStatusResponse(BaseModel):
    workflow_id: str
    thread_id: str
    status: str
    current_node: str | None
    deterministic_status: str | None
    requires_human_review: bool
    rule_data_version: str | None = None
    vector_index_version: str | None = None
    final_report: dict[str, Any] | None = None
    errors: list[Any] | None = None
    created_at: str | None = None
    completed_at: str | None = None


class ReviewTaskResponse(BaseModel):
    task_id: str
    workflow_id: str
    status: str
    reason: str
    disputed_fields: list[Any]
    requested_actions: list[Any]
    evidence: list[Any]
    deterministic_status: str | None
    created_at: str | None


class SubmitReviewRequest(BaseModel):
    action: str
    field_path: str | None = None
    original_value: Any = None
    corrected_value: Any = None
    provided_document_ids: list[str] = Field(default_factory=list)
    reviewer_reference: str
    reason: str | None = None
    review_note: str | None = None
    source: str | None = None


class AuditEventResponse(BaseModel):
    event_type: str
    node_name: str | None
    actor_type: str
    actor_reference: str | None
    new_state_hash: str | None
    event_payload: dict[str, Any] | None
    created_at: str | None


class AuditEventsResponse(BaseModel):
    workflow_id: str
    events: list[AuditEventResponse]
