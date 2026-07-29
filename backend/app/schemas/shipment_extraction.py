from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.compliance import (
    ComplianceCheckResponse,
    ComplianceCheckStatus,
    ShipmentComplianceInput,
)
from app.schemas.ocr import OcrValidationStatus


class FieldValidationStatus(str, Enum):
    VERIFIED = "verified"
    MANUAL_REVIEW = "manual_review"


class ExtractionMethod(str, Enum):
    REGEX_LABEL = "regex_label"
    OCR_REGEX = "ocr_regex"
    LLM_GAPFILL = "llm_gapfill"
    UNRESOLVED = "unresolved"
    PDF_TEXT_LLM_STRUCTURED_OUTPUT = "pdf_text_llm_structured_output"
    TESSERACT_OCR_LLM_STRUCTURED_OUTPUT = (
        "tesseract_ocr_llm_structured_output"
    )
    NOT_EXTRACTED_OCR_REQUIRED = "not_extracted_ocr_required"
    #: A reviewer confirmed or corrected this value during human review - it
    #: was never read off the PDF by this value. See HumanCorrection for the
    #: reviewer, reason and original value this replaced.
    HUMAN_REVIEW = "human_review"


class Phase2AStatus(str, Enum):
    READY = "ready"
    MANUAL_REVIEW = "manual_review"
    FAILED = "failed"


_ValueT = TypeVar("_ValueT")


class ExtractedField(BaseModel, Generic[_ValueT]):
    model_config = ConfigDict(str_strip_whitespace=True)

    value: _ValueT | None = None
    source_document_id: UUID
    source_page: int | None = Field(default=None, ge=1)
    extraction_method: ExtractionMethod
    confidence: Decimal = Field(ge=0, le=1)
    ocr_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    validation_status: FieldValidationStatus
    validation_note: str
    # Set only when a value was deterministically derived from another verified
    # field (never an LLM guess), e.g. a single-line invoice taking its weight
    # from the document-level declared total.
    derivation_method: str | None = None
    original_field_location: str | None = None

    @model_validator(mode="after")
    def uncertain_values_are_null(self) -> "ExtractedField[_ValueT]":
        if (
            self.value is None
            or self.confidence < Decimal("0.80")
            or self.validation_status == FieldValidationStatus.MANUAL_REVIEW
        ):
            self.value = None
            self.validation_status = FieldValidationStatus.MANUAL_REVIEW
        return self


class CandidateField(BaseModel, Generic[_ValueT]):
    """Provider output before trusted document provenance is attached."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    value: _ValueT | None = None
    source_page: int | None = Field(default=None, ge=1)
    confidence: Decimal = Field(ge=0, le=1)
    validation_status: FieldValidationStatus
    validation_note: str


class CommercialInvoiceCandidates(BaseModel):
    exporter_name: CandidateField[str]
    buyer_name: CandidateField[str]
    invoice_number: CandidateField[str]
    invoice_date: CandidateField[date]
    product_name: CandidateField[str]
    pct_code: CandidateField[str]
    quantity: CandidateField[Decimal]
    unit: CandidateField[str]
    unit_price: CandidateField[Decimal]
    line_total: CandidateField[Decimal]
    invoice_total: CandidateField[Decimal]
    currency: CandidateField[str]
    destination_country: CandidateField[str]
    net_weight: CandidateField[Decimal]
    gross_weight: CandidateField[Decimal]


class PackingListCandidates(BaseModel):
    product_name: CandidateField[str]
    pct_code: CandidateField[str]
    quantity: CandidateField[Decimal]
    unit: CandidateField[str]
    net_weight: CandidateField[Decimal]
    gross_weight: CandidateField[Decimal]
    package_count: CandidateField[int]


class CommercialInvoiceExtraction(BaseModel):
    exporter_name: ExtractedField[str]
    buyer_name: ExtractedField[str]
    invoice_number: ExtractedField[str]
    invoice_date: ExtractedField[date]
    product_name: ExtractedField[str]
    pct_code: ExtractedField[str]
    quantity: ExtractedField[Decimal]
    unit: ExtractedField[str]
    unit_price: ExtractedField[Decimal]
    line_total: ExtractedField[Decimal]
    invoice_total: ExtractedField[Decimal]
    currency: ExtractedField[str]
    destination_country: ExtractedField[str]
    net_weight: ExtractedField[Decimal]
    gross_weight: ExtractedField[Decimal]


class PackingListExtraction(BaseModel):
    product_name: ExtractedField[str]
    pct_code: ExtractedField[str]
    quantity: ExtractedField[Decimal]
    unit: ExtractedField[str]
    net_weight: ExtractedField[Decimal]
    gross_weight: ExtractedField[Decimal]
    package_count: ExtractedField[int]


class SourcePageReview(BaseModel):
    document_id: UUID
    document_type: str
    page_number: int = Field(ge=1)
    character_count: int = Field(ge=0)
    requires_ocr: bool
    review_note: str
    ocr_attempted: bool = False
    ocr_engine: str | None = None
    ocr_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    ocr_validation_status: OcrValidationStatus | None = None
    ocr_note: str | None = None


class CrossDocumentCheck(BaseModel):
    check_id: str
    check_name: str
    status: ComplianceCheckStatus
    message: str
    invoice_source_page: int | None
    packing_list_source_page: int | None


class ShipmentExtractionRequest(BaseModel):
    commercial_invoice_document_id: UUID
    packing_list_document_id: UUID | None
    shipment_date: date | None = None
    letter_of_credit_date: date | None = None
    additional_uploaded_document_types: list[str] = Field(default_factory=list)


class ShipmentExtractionResponse(BaseModel):
    status: Phase2AStatus
    commercial_invoice_document_id: UUID
    packing_list_document_id: UUID
    invoice: CommercialInvoiceExtraction
    packing_list: PackingListExtraction
    page_reviews: list[SourcePageReview]
    cross_document_checks: list[CrossDocumentCheck]
    shipment_input: ShipmentComplianceInput
    compliance: ComplianceCheckResponse
    fields_requiring_manual_review: list[str]
