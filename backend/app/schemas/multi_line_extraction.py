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
    PCT_CODE = "pct_code"
    PRODUCT_NAME = "product_name"


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
