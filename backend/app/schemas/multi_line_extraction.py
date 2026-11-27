"""Phase 2C schemas: commercial invoices and packing lists with many lines.

Phase 2A modelled exactly one product line per document.  Phase 2C keeps every
Phase 1/2A guarantee but lets a commercial invoice carry invoice-level header
data plus a list of line items, and lets a packing list carry a list of items.
Each value keeps field-level provenance (an ``ExtractedField``); each line item
additionally carries an item-level provenance roll-up.
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.compliance import (
    ComplianceCheckResponse,
    ComplianceCheckStatus,
    ShipmentComplianceInput,
)
from app.schemas.shipment_extraction import (
    CandidateField,
    CrossDocumentCheck,
    ExtractedField,
    FieldValidationStatus,
    SourcePageReview,
)
from app.schemas.supporting_documents import (
    SupportingDocumentRef,
    SupportingDocumentResult,
)


# --------------------------------------------------------------------------- #
# Provider candidate models (untrusted LLM output, before provenance).
# --------------------------------------------------------------------------- #
class InvoiceLineItemCandidate(BaseModel):
    """One commercial-invoice line as returned by the model."""

    model_config = ConfigDict(extra="forbid")

    line_number: CandidateField[int]
    product_name: CandidateField[str]
    pct_code: CandidateField[str]
    quantity: CandidateField[Decimal]
    unit: CandidateField[str]
    unit_price: CandidateField[Decimal]
    line_total: CandidateField[Decimal]
    net_weight: CandidateField[Decimal]
    gross_weight: CandidateField[Decimal]


class MultiLineInvoiceCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exporter_name: CandidateField[str]
    buyer_name: CandidateField[str]
    invoice_number: CandidateField[str]
    invoice_date: CandidateField[date]
    currency: CandidateField[str]
    destination_country: CandidateField[str]
    invoice_total: CandidateField[Decimal]
    declared_net_weight_total: CandidateField[Decimal]
    declared_gross_weight_total: CandidateField[Decimal]
    line_items: list[InvoiceLineItemCandidate]


class PackingListItemCandidate(BaseModel):
    """One packing-list item as returned by the model."""

    model_config = ConfigDict(extra="forbid")

    line_number: CandidateField[int]
    product_name: CandidateField[str]
    pct_code: CandidateField[str]
    quantity: CandidateField[Decimal]
    unit: CandidateField[str]
    net_weight: CandidateField[Decimal]
    gross_weight: CandidateField[Decimal]
    package_count: CandidateField[int]


class MultiLinePackingListCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    declared_net_weight_total: CandidateField[Decimal]
    declared_gross_weight_total: CandidateField[Decimal]
    #: The document's own "Total Packages" line, when printed. Lets a
    #: single-line packing list derive its one item's package count the same
    #: principled way it already derives net/gross weight.
    declared_package_count_total: CandidateField[int]
    items: list[PackingListItemCandidate]


# --------------------------------------------------------------------------- #
# Materialized extraction models (field-level + item-level provenance).
# --------------------------------------------------------------------------- #
class InvoiceLineItem(BaseModel):
    # Stable 1-based position of this line within the document as extracted.
    item_index: int = Field(ge=1)
    line_number: ExtractedField[int]
    product_name: ExtractedField[str]
    pct_code: ExtractedField[str]
    quantity: ExtractedField[Decimal]
    unit: ExtractedField[str]
    unit_price: ExtractedField[Decimal]
    line_total: ExtractedField[Decimal]
    net_weight: ExtractedField[Decimal]
    gross_weight: ExtractedField[Decimal]
    # Item-level provenance roll-up derived from the fields above.
    item_source_page: int | None = None
    item_confidence: Decimal = Field(ge=0, le=1)
    item_validation_status: FieldValidationStatus
    item_note: str


class MultiLineCommercialInvoiceExtraction(BaseModel):
    exporter_name: ExtractedField[str]
    buyer_name: ExtractedField[str]
    invoice_number: ExtractedField[str]
    invoice_date: ExtractedField[date]
    currency: ExtractedField[str]
    destination_country: ExtractedField[str]
    invoice_total: ExtractedField[Decimal]
    declared_net_weight_total: ExtractedField[Decimal]
    declared_gross_weight_total: ExtractedField[Decimal]
    line_items: list[InvoiceLineItem]


class PackingListItem(BaseModel):
    item_index: int = Field(ge=1)
    line_number: ExtractedField[int]
    product_name: ExtractedField[str]
    pct_code: ExtractedField[str]
    quantity: ExtractedField[Decimal]
    unit: ExtractedField[str]
    net_weight: ExtractedField[Decimal]
    gross_weight: ExtractedField[Decimal]
    package_count: ExtractedField[int]
    item_source_page: int | None = None
    item_confidence: Decimal = Field(ge=0, le=1)
    item_validation_status: FieldValidationStatus
    item_note: str


class MultiLinePackingListExtraction(BaseModel):
    declared_net_weight_total: ExtractedField[Decimal]
    declared_gross_weight_total: ExtractedField[Decimal]
    declared_package_count_total: ExtractedField[int]
    items: list[PackingListItem]


# --------------------------------------------------------------------------- #
# Matching and per-item result models.
# --------------------------------------------------------------------------- #
class ItemMatchStatus(str, Enum):
    MATCHED = "matched"
    INVOICE_ONLY = "invoice_only"
    PACKING_ONLY = "packing_only"


class ItemMatchStrategy(str, Enum):
    LINE_REFERENCE = "line_reference"
    SKU_CODE = "sku_code"
    PCT_CODE = "pct_code"
    PRODUCT_NAME = "product_name"


class LineDutyEstimate(BaseModel):
    """Statutory FY2026-27 Pakistan Customs cascading duty estimate for one line."""

    hs_code: str
    currency: str | None = None
    rate_source: str
    tariff_year: str | None = None
    source_page: int | None = None
    breakdown: dict[str, str]


class DutyEstimationSummary(BaseModel):
    """Aggregated shipment-level statutory duty & landed-cost breakdown."""

    total_assessed_value: str
    total_customs_duty: str
    total_additional_customs_duty: str
    total_regulatory_duty: str
    total_sales_tax: str
    total_value_addition_tax: str
    total_advance_income_tax: str
    total_duties_and_taxes: str
    total_landed_cost: str
    effective_rate_pct: str
    lines_estimated: int
    total_lines: int
    currency: str | None = None
    basis_note: str


class PipelineExecutionTelemetry(BaseModel):
    """Hybrid extraction speed and token-savings telemetry for one review."""

    extraction_mode: str
    processing_time_ms: float
    documents_processed: int
    cache_hits: int = 0
    fields_from_deterministic: int = 0
    fields_from_llm: int = 0
    llm_calls: int = 0
    tokens_used: int = 0
    estimated_tokens_saved: int = 0


ReadinessBand = Literal[
    "ready_for_filing",
    "awaiting_external_documents",
    "needs_human_confirmation",
    "blocked_document_defect",
]
PortHoldRisk = Literal["low", "medium", "high"]
ActionOwner = Literal[
    "exporter_in_house",
    "bank_or_psw",
    "issuing_authority",
    "compliance_reviewer",
]
BlockerCategory = Literal[
    "document_discrepancy",
    "arithmetic_error",
    "regulatory_blocker",
    "external_document",
    "manual_confirmation",
]


class RemediationStep(BaseModel):
    """One prioritized, owner-assigned action to reach customs filing readiness."""

    priority: int = Field(ge=1, le=3)
    owner: ActionOwner
    category: BlockerCategory
    title: str
    action: str
    score_impact: int = Field(ge=0, le=100)


class ClearanceReadinessSummary(BaseModel):
    """Exporter-facing pre-clearance readiness scorecard and fix plan."""

    readiness_score: int = Field(ge=0, le=100)
    readiness_band: ReadinessBand
    port_hold_risk: PortHoldRisk
    headline: str
    in_house_fixes_count: int = Field(ge=0)
    external_documents_count: int = Field(ge=0)
    manual_confirmations_count: int = Field(ge=0)
    remediation_steps: list[RemediationStep] = Field(default_factory=list)


class ShipmentItemResult(BaseModel):
    item_reference: str
    invoice_item_index: int | None
    packing_item_index: int | None
    invoice_line_number: int | None
    packing_line_number: int | None
    product_name: str | None
    pct_code: str | None
    match_status: ItemMatchStatus
    match_strategy: ItemMatchStrategy | None
    match_note: str
    item_checks: list[CrossDocumentCheck]
    shipment_input: ShipmentComplianceInput | None
    compliance: ComplianceCheckResponse | None
    status: ComplianceCheckStatus
    fields_requiring_manual_review: list[str]
    duty_estimate: LineDutyEstimate | None = None


# --------------------------------------------------------------------------- #
# Request / response envelopes.
# --------------------------------------------------------------------------- #
class MultiLineShipmentRequest(BaseModel):
    # Binds one selected-document snapshot to its response. Compliance rules
    # never read this identifier.
    review_revision_id: UUID = Field(default_factory=uuid4)
    commercial_invoice_document_id: UUID
    packing_list_document_id: UUID | None
    shipment_date: date | None = None
    letter_of_credit_date: date | None = None
    # Legacy: a *claim* that a document exists. Kept for backward compatibility,
    # but a claim alone can no longer satisfy a required-document rule - see
    # `supporting_documents`.
    additional_uploaded_document_types: list[str] = Field(default_factory=list)
    # Actual uploaded supporting documents, which are read, classified and
    # cross-checked against the shipment before they can count as present.
    supporting_documents: list[SupportingDocumentRef] = Field(default_factory=list)


class OutstandingDocument(BaseModel):
    """A customs document a rule requires that is not in hand yet.

    This is a checklist entry, not a defect in the uploaded files: the document
    is issued by an outside body and cannot be derived from the invoice or the
    packing list. Every rule that named it keeps its citation here.
    """

    document_type: str
    display_name: str
    #: "required" when at least one rule firmly requires it; "conditional" when
    #: the rules could not decide from the shipment fields whether it applies.
    requirement: str
    reasons: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class MultiLineShipmentResponse(BaseModel):
    # Echoed from the request so the UI cannot combine this result with a newer
    # supporting-document selection.
    review_revision_id: UUID = Field(default_factory=uuid4)
    #: The strict customs verdict: a shipment with outstanding paperwork is
    #: still not ready for submission. Unchanged meaning.
    overall_status: ComplianceCheckStatus
    is_compliant: bool
    rule_data_version: str
    commercial_invoice_document_id: UUID
    packing_list_document_id: UUID
    invoice: MultiLineCommercialInvoiceExtraction
    packing_list: MultiLinePackingListExtraction
    page_reviews: list[SourcePageReview]
    shipment_level_checks: list[CrossDocumentCheck]
    items: list[ShipmentItemResult]
    fields_requiring_manual_review: list[str]
    supporting_documents: list[SupportingDocumentResult] = Field(default_factory=list)
    #: The verdict on the two documents that were actually uploaded, judged
    #: without the rules that merely require further paperwork. Never used to
    #: clear a shipment on its own - `overall_status` remains the decision.
    document_review_status: ComplianceCheckStatus = ComplianceCheckStatus.PASSED
    #: Customs documents the rules require that were not provided.
    outstanding_documents: list[OutstandingDocument] = Field(default_factory=list)
    #: Deterministic 0-100 pre-clearance readiness score, hold risk, and fix plan.
    clearance_readiness: ClearanceReadinessSummary | None = None
    #: Exact Decimal FY2026-27 cascading duty & landed-cost summary across lines.
    duty_estimation_summary: DutyEstimationSummary | None = None
    #: Hybrid deterministic-vs-LLM execution speed and token-savings telemetry.
    pipeline_telemetry: PipelineExecutionTelemetry | None = None
