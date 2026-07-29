from datetime import date
from pydantic import BaseModel, ConfigDict, Field
from typing import Any
from uuid import UUID

class GuidanceRequest(BaseModel):
    conversation_id: UUID | None = None
    product: str
    pct_code: str
    destination: str
    planned_shipment_date: str | None = None
    question: str = ""

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
    # Provenance for regulatory citations. Declared rather than passed through
    # `extra="allow"` so the console can rely on the shape and a curated
    # summary is never rendered with an official badge by omission.
    source_kind_label: str | None = None
    is_official: bool | None = None
    issuing_authority: str | None = None
    section: str | None = None
    sro_number: str | None = None
    source_url: str | None = None
    referenced_official_source: str | None = None

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

class RegulatoryChatRequest(BaseModel):
    """A question for the global regulatory assistant.

    No shipment, upload, audit or supported PCT code is required. The optional
    filters narrow retrieval only when the caller explicitly sets them.
    """

    question: str = Field(min_length=1)
    conversation_id: UUID | None = None
    pct_code: str | None = None
    destination: str | None = None
    source_document: str | None = None
    top_k: int = Field(default=5, ge=1, le=10)


class RegulatoryCitationSchema(BaseModel):
    """One accepted passage, with the provenance actually recorded for it.

    Every optional field is None when the ingested source did not record it.
    Missing metadata is never filled in with a plausible-looking guess.
    """

    title: str
    source_kind: str
    source_kind_label: str
    is_official: bool
    issuing_authority: str | None = None
    page_number: int | None = None
    section: str | None = None
    publication_date: date | None = None
    effective_date: date | None = None
    corpus_snapshot_date: date | None = None
    accepted_passage: str
    evidence_status: str
    source_url: str | None = None
    sro_number: str | None = None
    #: For a curated summary, the official document it cites (when recorded).
    referenced_official_source: str | None = None


class ChecklistDocumentSchema(BaseModel):
    """One line of an exporter-facing document checklist."""

    display_name: str
    requirement: str  # "required" | "conditional"
    condition: str | None = None


class ProductCandidateSchema(BaseModel):
    """A supported product the question could have meant."""

    pct_code: str
    product_name: str


class RegulatoryChatResponse(BaseModel):
    conversation_id: UUID
    message_id: UUID
    mode: str = "regulatory_assistant"
    answer: str
    intent: str
    evidence_status: str  # "accepted" | "evidence_not_found" | "not_applicable"
    #: Whether the passages are about the asked-for PCT code or only its
    #: broader product category: exact_pct | broader_category |
    #: not_pct_specific | none.
    evidence_scope: str = "not_pct_specific"
    #: How the answer is presented: checklist | clarification | explanation |
    #: evidence_lookup | document_search | refusal. The console renders on this
    #: rather than on the intent name, which is internal vocabulary.
    answer_mode: str = "explanation"
    #: Populated for checklist and clarification answers so the console can lay
    #: the documents out instead of printing a paragraph.
    required_documents: list[ChecklistDocumentSchema] = []
    conditional_documents: list[ChecklistDocumentSchema] = []
    product_candidates: list[ProductCandidateSchema] = []
    #: Spelling the assistant interpreted, shown rather than silently applied.
    interpreted_as: dict[str, str] = {}
    resolved_product: str | None = None
    resolved_pct_code: str | None = None
    destination: str | None = None
    sources: list[RegulatoryCitationSchema] = []
    limitations: list[str] = []
    supported_compliance_scope: list[str] = []
    informational_only: bool = True
    suggested_questions: list[str] = []


class DocumentGuidanceSchema(BaseModel):
    document_type: str
    display_name: str
    requirement: str
    reason: str
    evidence_status: str
    #: How the retrieved passage relates to this specific requirement. One of
    #: direct_evidence, indirect_support, configured_rule_only,
    #: evidence_unavailable, conflicting_evidence.
    evidence_class: str = "configured_rule_only"
    #: One sentence for the collapsed card; the full reason stays in `reason`.
    summary: str = ""
    #: Pre-upload the checklist describes paperwork to obtain, not absence.
    preparation_status: str = "to_prepare"
    rule_sources: list[str] = []
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
