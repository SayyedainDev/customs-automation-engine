"""Typed graph state and structured report models for the customs-audit workflow.

The LangGraph state is a ``TypedDict`` holding only JSON-serializable values so
any checkpointer (memory / sqlite / postgres) can persist and resume it. The
Pydantic report models below are used to build and validate agent output, then
dumped to plain dicts for the state.

Provenance discipline: every surfaced value carries a label so the final report
can distinguish machine-extracted, OCR, LLM-structured, deterministic-check,
retrieved-evidence, agent-observation and human-correction data.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, TypedDict

from pydantic import BaseModel, Field


class WorkflowStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    RESUMING = "resuming"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class ActorType(str, Enum):
    SYSTEM = "system"
    BROKER = "broker"
    AUDITOR = "auditor"
    HUMAN = "human"


class ProvenanceLabel(str, Enum):
    MACHINE_EXTRACTED = "machine_extracted"
    OCR_EXTRACTED = "ocr_extracted"
    LLM_STRUCTURED = "llm_structured"
    DETERMINISTIC_CHECK = "deterministic_check"
    RETRIEVED_EVIDENCE = "retrieved_evidence"
    AGENT_OBSERVATION = "agent_observation"
    HUMAN_CORRECTION = "human_correction"


class AuditorRecommendation(str, Enum):
    CONTINUE = "continue"
    HUMAN_REVIEW = "human_review"
    RERUN_EXTRACTION = "rerun_extraction"
    EVIDENCE_MISSING = "evidence_missing"
    TECHNICAL_FAILURE = "technical_failure"


class HumanAction(str, Enum):
    CONFIRM_EXTRACTED_VALUE = "confirm_extracted_value"
    CORRECT_EXTRACTED_VALUE = "correct_extracted_value"
    PROVIDE_MISSING_DOCUMENT = "provide_missing_document"
    ACCEPT_MANUAL_REVIEW = "accept_manual_review"
    REQUEST_REPROCESSING = "request_reprocessing"
    REJECT_SUBMISSION = "reject_submission"
    ADD_REVIEW_NOTE = "add_review_note"


# --------------------------------------------------------------------------- #
# Structured agent reports.
# --------------------------------------------------------------------------- #
class ExtractedFieldView(BaseModel):
    field_path: str
    value: Any = None
    provenance: ProvenanceLabel
    confidence: float | None = None
    source_page: int | None = None
    validation_status: str | None = None


class BrokerReport(BaseModel):
    extracted_fields: list[ExtractedFieldView] = Field(default_factory=list)
    matched_items: list[dict[str, Any]] = Field(default_factory=list)
    unmatched_items: list[dict[str, Any]] = Field(default_factory=list)
    document_discrepancies: list[dict[str, Any]] = Field(default_factory=list)
    deterministic_check_results: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_products: list[str] = Field(default_factory=list)
    missing_documents: list[str] = Field(default_factory=list)
    ocr_or_manual_review_fields: list[str] = Field(default_factory=list)
    preliminary_summary: str = ""
    report_confidence: float = 0.0
    report_limitations: list[str] = Field(default_factory=list)
    #: Supporting documents the Broker reports as verified, taken from the
    #: deterministic verifier's own result. The Auditor re-derives the same
    #: question independently; a difference between the two is a disagreement
    #: that routes to a human.
    verified_supporting_documents: list[str] = Field(default_factory=list)
    unverified_supporting_documents: list[str] = Field(default_factory=list)
    # The deterministic status the Broker observed (never authored by the agent).
    observed_deterministic_status: str | None = None


class AuditorReport(BaseModel):
    confirmed_findings: list[str] = Field(default_factory=list)
    challenged_findings: list[str] = Field(default_factory=list)
    newly_detected_issues: list[str] = Field(default_factory=list)
    extraction_disagreements: list[dict[str, Any]] = Field(default_factory=list)
    compliance_status_confirmed: bool = False
    evidence_support_status: str = "not_checked"  # supported|partial|not_found|conflicting
    missing_provenance: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    critical_anomalies: list[str] = Field(default_factory=list)
    recommended_workflow_action: AuditorRecommendation = AuditorRecommendation.CONTINUE
    audit_notes: list[str] = Field(default_factory=list)
    observed_deterministic_status: str | None = None

    # Supporting-document review. The Auditor re-derives every comparison from
    # the extracted values rather than reading the verifier's conclusions, so a
    # bug in the verifier shows up as a disagreement instead of being echoed.
    # None of these fields may change the deterministic status; the strongest
    # thing they can do is route the workflow to a human.
    confirmed_supporting_documents: list[str] = Field(default_factory=list)
    challenged_supporting_documents: list[str] = Field(default_factory=list)
    document_type_disagreements: list[str] = Field(default_factory=list)
    field_mismatches: list[str] = Field(default_factory=list)
    missing_document_fields: list[str] = Field(default_factory=list)
    low_confidence_documents: list[str] = Field(default_factory=list)
    authenticity_limitations: list[str] = Field(default_factory=list)


class ConsensusResult(BaseModel):
    consensus_reached: bool
    agreed_fields: list[str] = Field(default_factory=list)
    disagreed_fields: list[str] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)
    requires_human_review: bool = False
    reason: str = ""
    deterministic_status: str | None = None
    broker_recommendation: str | None = None
    auditor_recommendation: str | None = None


class HumanReviewRequest(BaseModel):
    reason: str
    disputed_fields: list[str] = Field(default_factory=list)
    source_document_pages: list[dict[str, Any]] = Field(default_factory=list)
    deterministic_status: str | None = None
    evidence_passages: list[dict[str, Any]] = Field(default_factory=list)
    allowed_actions: list[HumanAction] = Field(default_factory=list)
    created_at: str | None = None
    review_status: str = "open"


class HumanCorrection(BaseModel):
    field_path: str
    original_value: Any = None
    corrected_value: Any = None
    reviewer_reference: str
    reason: str
    source: str | None = None
    timestamp: str


class HumanReviewDecision(BaseModel):
    action: HumanAction
    corrections: list[HumanCorrection] = Field(default_factory=list)
    provided_document_ids: list[str] = Field(default_factory=list)
    reviewer_reference: str
    reason: str | None = None
    review_note: str | None = None
    timestamp: str


# --------------------------------------------------------------------------- #
# LangGraph state.
# --------------------------------------------------------------------------- #
class CustomsAuditState(TypedDict, total=False):
    workflow_id: str
    thread_id: str
    workflow_status: str
    created_at: str
    updated_at: str

    commercial_invoice_document_id: str
    packing_list_document_id: str | None
    additional_document_ids: list[str]
    shipment_date: str | None
    letter_of_credit_date: str | None
    destination_country: str | None
    additional_uploaded_document_types: list[str]
    supporting_documents: list[dict[str, Any]]

    extraction_result: dict[str, Any] | None
    page_reviews: list[dict[str, Any]]
    ocr_results: list[dict[str, Any]]
    matched_items: list[dict[str, Any]]
    cross_document_checks: list[dict[str, Any]]
    shipment_inputs: list[dict[str, Any]]
    deterministic_compliance_result: dict[str, Any] | None
    deterministic_result_history: list[dict[str, Any]]

    broker_report: dict[str, Any] | None
    auditor_report: dict[str, Any] | None
    consensus_result: dict[str, Any] | None
    retrieved_evidence: list[dict[str, Any]]
    explanation_results: list[dict[str, Any]]

    critical_anomalies: list[str]
    manual_review_reasons: list[str]
    human_review_request: dict[str, Any] | None
    human_review_decision: dict[str, Any] | None
    human_correction_history: list[dict[str, Any]]

    final_report: dict[str, Any] | None
    rule_data_version: str | None
    vector_index_version: str | None
    errors: list[dict[str, Any]]
    audit_events: list[dict[str, Any]]
    retry_count: int


def utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


def to_json_scalar(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
