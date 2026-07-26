from datetime import date
from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.compliance import ComplianceCheckStatus


class ExplanationStatus(str, Enum):
    EXPLAINED = "explained"
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class ExplanationRequest(BaseModel):
    # The deterministic decision to be explained. It is echoed back unchanged;
    # the explanation layer never recomputes or alters it.
    original_status: ComplianceCheckStatus
    check_id: str
    check_name: str | None = None
    pct_code: str | None = None
    destination_country: str | None = None
    required_document: str | None = None
    sro_number: str | None = None
    source_document: str | None = None
    message: str | None = None
    rule_data_version: str | None = None
    user_question: str | None = None
    top_k: int = Field(default=5, ge=1, le=10)


class Citation(BaseModel):
    source_document: str
    sro_number: str | None
    page_number: int | None
    source_url: str | None
    validation_status: str
    evidence_text: str


class ExplanationResponse(BaseModel):
    original_status: ComplianceCheckStatus
    check_id: str
    explanation_status: ExplanationStatus
    answer: str | None
    citations: list[Citation]
    rule_data_version: str | None
    legal_cutoff_date: date
    retrieval_mode: str
    degraded_mode: bool
    embedding_model: str
    reranker_model: str
    # How the prose was produced: "llm" | "deterministic_grounded_summary" | "none".
    explanation_generator: str
