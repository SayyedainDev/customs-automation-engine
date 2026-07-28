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


class CorrectionBasis(str, Enum):
    """*Why* a disputed value is eligible (or not) for correct_extracted_value.

    A human corrects an *extraction* problem - the software's own reading of
    a document - never a *document* problem, where two documents plainly and
    confidently disagree with each other. The first four are extraction
    problems, correctable in place. The last three are not: the document
    itself is the source of the disagreement, and only a corrected document
    (not implemented in this prototype - see ``correct_extracted_value``
    validation in nodes.py) can resolve them.
    """

    LOW_CONFIDENCE_EXTRACTION = "low_confidence_extraction"
    AMBIGUOUS_OCR = "ambiguous_ocr"
    PARSER_ERROR = "parser_error"
    HUMAN_CONFIRMATION_REQUIRED = "human_confirmation_required"
    CONFIRMED_DOCUMENT_MISMATCH = "confirmed_document_mismatch"
    MISSING_REQUIRED_DOCUMENT = "missing_required_document"
    REGULATORY_VIOLATION = "regulatory_violation"


#: The only bases correct_extracted_value/confirm_extracted_value may act on -
#: a reviewer resolves the software's uncertainty, never a document's own
#: unambiguous content.
CORRECTABLE_BASES = frozenset(
    {
        CorrectionBasis.LOW_CONFIDENCE_EXTRACTION.value,
        CorrectionBasis.AMBIGUOUS_OCR.value,
        CorrectionBasis.PARSER_ERROR.value,
        CorrectionBasis.HUMAN_CONFIRMATION_REQUIRED.value,
    }
)


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


class DisputedFieldDetail(BaseModel):
    """One specific extracted value behind a review task - where it came
    from, on which page, and how confident the extractor was. This is what
    lets a reviewer (and the frontend) see *which* value is in question
    without reading raw field paths, and what a correction is later
    validated against (see ``field_path`` in ``HumanCorrection``)."""

    field_path: str
    document_type: str
    document_id: str | None = None
    page: int | None = None
    value: Any = None
    confidence: float | None = None
    extraction_method: str | None = None
    #: Why this specific value is (or is not) eligible for
    #: correct_extracted_value/confirm_extracted_value - see CorrectionBasis.
    correction_basis: str = CorrectionBasis.CONFIRMED_DOCUMENT_MISMATCH.value


class HumanReviewRequest(BaseModel):
    reason: str
    disputed_fields: list[str] = Field(default_factory=list)
    source_document_pages: list[dict[str, Any]] = Field(default_factory=list)
    deterministic_status: str | None = None
    evidence_passages: list[dict[str, Any]] = Field(default_factory=list)
    allowed_actions: list[HumanAction] = Field(default_factory=list)
    created_at: str | None = None
    review_status: str = "open"

    # -- Structured review-task fields (targeted correction workflow) ----- #
    #: Stable id for this specific review task, distinct from the workflow
    #: id - a workflow can have more than one review task across rounds.
    review_task_id: str | None = None
    #: Which audit revision this task is reviewing (1 for the first pass).
    revision_number: int = 1
    #: Short machine-stable code for *why* (e.g. "quantity_mismatch"),
    #: independent of the human-readable ``reason``/``plain_language_question``.
    reason_code: str | None = None
    title: str | None = None
    plain_language_question: str | None = None
    #: The precise values in dispute, each traceable to a document and page -
    #: the answer to "does the interrupt payload identify the exact disputed
    #: field" (see field-to-check dependency map for how these map onward).
    disputed_field_details: list[DisputedFieldDetail] = Field(default_factory=list)
    #: check_ids a correction to any of the above fields would rerun - shown
    #: to the reviewer so they know what "submit" actually does.
    affected_check_ids: list[str] = Field(default_factory=list)


class HumanCorrection(BaseModel):
    field_path: str
    original_value: Any = None
    corrected_value: Any = None
    reviewer_reference: str
    reason: str
    source: str | None = None
    timestamp: str

    # -- Structured correction-record fields ------------------------------ #
    correction_id: str | None = None
    review_task_id: str | None = None
    #: The revision this correction was applied to (it produces the next one).
    revision_from: int | None = None
    source_document_id: str | None = None
    source_page: int | None = None
    #: check_ids this specific field is known to affect - computed by the
    #: dependency map at apply-time, stored here so the audit record is
    #: self-explanatory without recomputing it later.
    affected_check_ids: list[str] = Field(default_factory=list)


class HumanReviewDecision(BaseModel):
    action: HumanAction
    corrections: list[HumanCorrection] = Field(default_factory=list)
    provided_document_ids: list[str] = Field(default_factory=list)
    reviewer_reference: str
    reason: str | None = None
    review_note: str | None = None
    timestamp: str


#: A human correction changes *input data*; Python recalculates the status
#: from that data exactly as it did the first time - this bundle is what
#: "recalculated, not reassigned" looks like on the wire. One entry per
#: revision, append-only: revision 1 is never edited or replaced, a
#: correction only ever adds a new entry.
class AuditRevision(BaseModel):
    revision_number: int
    frozen: bool = True
    frozen_at: str
    #: "initial" for the first pass, "human_correction" for every revision a
    #: correction produced.
    triggered_by: str
    correction_id: str | None = None
    deterministic_result: dict[str, Any]
    broker_report: dict[str, Any] | None = None
    auditor_report: dict[str, Any] | None = None
    consensus_result: dict[str, Any] | None = None


#: After this many human-review rounds still leave the shipment uncertain,
#: the graph stops looping and leaves it in manual review rather than
#: interrupting a person indefinitely.
MAX_HUMAN_REVIEW_ROUNDS = 2


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
    #: Per check_id, normalized RAG citations - populated for every regulatory
    #: check regardless of pass/fail/manual_review status.
    regulatory_evidence_by_check: dict[str, list[dict[str, Any]]]
    #: Per check_id, "evidence_verified" | "evidence_partial" |
    #: "evidence_unavailable" | "evidence_conflicting" - honest, graded label
    #: for what retrieval found; never invented when nothing was found.
    regulatory_evidence_status_by_check: dict[str, str]
    #: Per check_id, evidence read directly from the uploaded invoice/packing
    #: list for a document-comparison check (no retrieval involved).
    document_evidence_by_check: dict[str, list[dict[str, Any]]]
    #: Per check_id, a plain "this prototype supports this input" statement
    #: for a system-scope check (e.g. mvp_pct_support) - never a regulatory
    #: citation, because it isn't a government requirement.
    system_scope_statements_by_check: dict[str, str]
    explanation_results: list[dict[str, Any]]

    critical_anomalies: list[str]
    manual_review_reasons: list[str]
    human_review_request: dict[str, Any] | None
    human_review_decision: dict[str, Any] | None
    human_correction_history: list[dict[str, Any]]
    #: One AuditRevision dict per frozen revision, oldest first. Revision 1 is
    #: appended once, right after the first deterministic result exists; a
    #: correction never edits it, only appends revision 2, 3, ...
    audit_revisions: list[dict[str, Any]]
    #: How many times interrupt_for_human_review has actually paused the
    #: workflow (first pass counts as round 1) - compared against
    #: MAX_HUMAN_REVIEW_ROUNDS to stop an uncertain correction loop.
    human_review_round: int
    #: Set only when correction validation rejects a submitted decision
    #: (bad field path, wrong type, protected field, ...) - never silently
    #: dropped, always visible on the report. Accumulates across every round
    #: (the full audit trail); routing after a correction uses the
    #: round-local ``correction_applied`` flag instead, so a stale failure
    #: from an earlier round can never mis-route a later, valid one.
    correction_validation_errors: list[str]
    #: Whether *this* round's correction was successfully applied and
    #: rechecked - overwritten every round, unlike the accumulating error
    #: list above. Drives routing straight after apply_human_correction.
    correction_applied: bool
    #: check_ids the most recent correction is known to affect (from the
    #: dependency map) - read by auditor_recheck_revision to decide which
    #: regulatory checks must requery RAG versus reuse their prior citation.
    affected_check_ids: list[str]
    #: "rejected_current_submission" when the reviewer rejected the
    #: submission - kept separate from deterministic_compliance_result's own
    #: overall_status, which a human disposition never overwrites.
    human_disposition: str | None

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
