"""Typed supporting-document schemas.

Closes the gap where a caller could satisfy a required-document check simply by
putting its name in ``additional_uploaded_document_types``. A document now has
to be *uploaded*, *read*, *classified* and *cross-checked* against the shipment
before it can contribute to a positive compliance outcome.

Every extracted value keeps the same provenance contract as the invoice and
packing list (``CandidateField`` from the provider, ``ExtractedField`` after
document provenance is attached), so nothing here can invent a value.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.shipment_extraction import (
    CandidateField,
    CrossDocumentCheck,
    ExtractedField,
    FieldValidationStatus,
)


class SupportingDocumentType(str, Enum):
    """Document types the deterministic rules can reason about."""

    FORM_E_OR_PSW_EXPORT_DECLARATION = "form_e_or_psw_export_declaration"
    CERTIFICATE_OF_ORIGIN = "certificate_of_origin"
    SBP_DEPOSIT_PROOF = "sbp_deposit_proof"
    SBP_CONFIRMATION = "sbp_confirmation"
    IRREVOCABLE_LETTER_OF_CREDIT = "irrevocable_letter_of_credit"
    PHYTOSANITARY_CERTIFICATE = "phytosanitary_certificate"
    IMPORTING_COUNTRY_PERMIT = "importing_country_permit"
    GOODS_DECLARATION = "goods_declaration"
    BILL_OF_LADING = "bill_of_lading"
    EXPORT_CONTRACT = "export_contract"
    UNKNOWN = "unknown"


# Aliases accepted from callers / existing string-based requests, mapped onto the
# canonical type. Keeps backward compatibility with
# ``additional_uploaded_document_types`` without treating a string as proof.
SUPPORTING_TYPE_ALIASES: dict[str, SupportingDocumentType] = {
    "form_e": SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION,
    "forme": SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION,
    "psw_export_declaration": SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION,
    "form_e_or_psw_export_declaration": (
        SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION
    ),
    # Titles a real Form-E/PSW declaration is printed under. Detection reads the
    # document's own heading, so every heading that names the same instrument has
    # to resolve to the same canonical type - otherwise a correct document would
    # be rejected as the wrong type.
    "form_e_psw_export_declaration": (
        SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION
    ),
    "form_e_export_declaration": (
        SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION
    ),
    "export_declaration": SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION,
    "psw_declaration": SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION,
    "certificate_of_origin": SupportingDocumentType.CERTIFICATE_OF_ORIGIN,
    "coo": SupportingDocumentType.CERTIFICATE_OF_ORIGIN,
    "cpfta_certificate_of_origin": SupportingDocumentType.CERTIFICATE_OF_ORIGIN,
    "certificate_of_origin_cpfta": SupportingDocumentType.CERTIFICATE_OF_ORIGIN,
    "preferential_certificate_of_origin": (
        SupportingDocumentType.CERTIFICATE_OF_ORIGIN
    ),
    "non_preferential_certificate_of_origin": (
        SupportingDocumentType.CERTIFICATE_OF_ORIGIN
    ),
    "sbp_deposit_proof": SupportingDocumentType.SBP_DEPOSIT_PROOF,
    "proof_of_sbp_deposit": SupportingDocumentType.SBP_DEPOSIT_PROOF,
    "sbp_deposit_receipt": SupportingDocumentType.SBP_DEPOSIT_PROOF,
    "state_bank_of_pakistan_deposit_proof": SupportingDocumentType.SBP_DEPOSIT_PROOF,
    "sbp_confirmation": SupportingDocumentType.SBP_CONFIRMATION,
    "state_bank_of_pakistan_confirmation": SupportingDocumentType.SBP_CONFIRMATION,
    "irrevocable_letter_of_credit": SupportingDocumentType.IRREVOCABLE_LETTER_OF_CREDIT,
    "letter_of_credit": SupportingDocumentType.IRREVOCABLE_LETTER_OF_CREDIT,
    "lc": SupportingDocumentType.IRREVOCABLE_LETTER_OF_CREDIT,
    "irrevocable_lc": SupportingDocumentType.IRREVOCABLE_LETTER_OF_CREDIT,
    "documentary_credit": SupportingDocumentType.IRREVOCABLE_LETTER_OF_CREDIT,
    "phytosanitary_certificate": SupportingDocumentType.PHYTOSANITARY_CERTIFICATE,
    "phyto_certificate": SupportingDocumentType.PHYTOSANITARY_CERTIFICATE,
    "plant_health_certificate": SupportingDocumentType.PHYTOSANITARY_CERTIFICATE,
    "import_permit": SupportingDocumentType.IMPORTING_COUNTRY_PERMIT,
    "importing_country_permit": SupportingDocumentType.IMPORTING_COUNTRY_PERMIT,
    "importing_country_import_permit": (
        SupportingDocumentType.IMPORTING_COUNTRY_PERMIT
    ),
    "goods_declaration": SupportingDocumentType.GOODS_DECLARATION,
    "export_goods_declaration": SupportingDocumentType.GOODS_DECLARATION,
    "bill_of_lading": SupportingDocumentType.BILL_OF_LADING,
    "ocean_bill_of_lading": SupportingDocumentType.BILL_OF_LADING,
    "export_contract": SupportingDocumentType.EXPORT_CONTRACT,
    "export_sales_contract": SupportingDocumentType.EXPORT_CONTRACT,
    "sales_contract": SupportingDocumentType.EXPORT_CONTRACT,
}


# Aliases shorter than this are not used for the containment stage below: a
# two-letter token like "lc" appearing inside a longer description is too weak a
# signal to classify a legal document on.
_MIN_CONTAINMENT_ALIAS_LENGTH = 3


def _normalise_type_key(value: str) -> str:
    key = "".join(
        character if character.isalnum() else "_" for character in value.casefold()
    ).strip("_")
    while "__" in key:
        key = key.replace("__", "_")
    return key


def canonical_supporting_type(value: str) -> SupportingDocumentType:
    """Resolve a printed or claimed document description to a canonical type.

    Two stages, both deterministic:

    1. exact match on the normalised alias;
    2. failing that, a *unique* whole-token containment match.

    Stage 2 exists because a document names itself in prose. A real Form-E is
    headed "Pakistan Single Window Export Declaration", and a certificate of
    origin may carry a "(CPFTA)" qualifier - neither is an exact alias, yet both
    are unambiguous. Found live: a correct Form-E was rejected as the wrong type
    because the page described itself more fully than the alias table did.

    Ambiguity is never resolved by guessing. If a description contains aliases
    for two different types, or none, the result is UNKNOWN, which the verifier
    turns into a manual review rather than a pass or a mismatch.
    """
    key = _normalise_type_key(value)
    direct = SUPPORTING_TYPE_ALIASES.get(key)
    if direct is not None:
        return direct

    padded = f"_{key}_"
    matched = {
        canonical
        for alias, canonical in SUPPORTING_TYPE_ALIASES.items()
        if len(alias) >= _MIN_CONTAINMENT_ALIAS_LENGTH and f"_{alias}_" in padded
    }
    if len(matched) == 1:
        return matched.pop()
    return SupportingDocumentType.UNKNOWN


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #
class SupportingDocumentRef(BaseModel):
    """A supporting document the caller actually uploaded."""

    model_config = ConfigDict(extra="forbid")

    document_type: str
    document_id: UUID

    @property
    def canonical_type(self) -> SupportingDocumentType:
        return canonical_supporting_type(self.document_type)


# --------------------------------------------------------------------------- #
# Provider candidate model (untrusted output, before provenance)
# --------------------------------------------------------------------------- #
class SupportingDocumentCandidates(BaseModel):
    """One flat shape covering every supported document type.

    A single flat model keeps the provider request small and reliable (the
    lesson from DEF-001); type-specific *requirements* are enforced
    deterministically afterwards, not by asking the model for a different shape
    per document.
    """

    model_config = ConfigDict(extra="forbid")

    detected_document_type: CandidateField[str]
    document_number: CandidateField[str]
    issue_date: CandidateField[str]
    expiry_date: CandidateField[str]
    exporter_or_applicant: CandidateField[str]
    buyer_or_beneficiary: CandidateField[str]
    invoice_reference: CandidateField[str]
    contract_reference: CandidateField[str]
    pct_code: CandidateField[str]
    product_or_commodity: CandidateField[str]
    destination_country: CandidateField[str]
    issuing_authority: CandidateField[str]
    bank_name: CandidateField[str]
    amount: CandidateField[Decimal]
    currency: CandidateField[str]
    percentage: CandidateField[Decimal]
    quantity: CandidateField[Decimal]
    shipment_deadline: CandidateField[str]
    related_reference: CandidateField[str]
    treatment_or_inspection: CandidateField[str]


# --------------------------------------------------------------------------- #
# Materialized extraction (provenance attached)
# --------------------------------------------------------------------------- #
class SupportingDocumentExtraction(BaseModel):
    detected_document_type: ExtractedField[str]
    document_number: ExtractedField[str]
    issue_date: ExtractedField[date]
    expiry_date: ExtractedField[date]
    exporter_or_applicant: ExtractedField[str]
    buyer_or_beneficiary: ExtractedField[str]
    invoice_reference: ExtractedField[str]
    contract_reference: ExtractedField[str]
    pct_code: ExtractedField[str]
    product_or_commodity: ExtractedField[str]
    destination_country: ExtractedField[str]
    issuing_authority: ExtractedField[str]
    bank_name: ExtractedField[str]
    amount: ExtractedField[Decimal]
    currency: ExtractedField[str]
    percentage: ExtractedField[Decimal]
    quantity: ExtractedField[Decimal]
    shipment_deadline: ExtractedField[date]
    related_reference: ExtractedField[str]
    treatment_or_inspection: ExtractedField[str]

    document_source_page: int | None = None
    document_confidence: Decimal = Field(ge=0, le=1)
    document_validation_status: FieldValidationStatus
    document_note: str


# --------------------------------------------------------------------------- #
# Verification result
# --------------------------------------------------------------------------- #
class SupportingDocumentState(str, Enum):
    """How far a document got through verification."""

    CLAIMED_ONLY = "claimed_only"
    UPLOADED = "uploaded"
    UNREADABLE = "unreadable"
    TYPE_MISMATCH = "type_mismatch"
    TYPE_VERIFIED = "type_verified"
    FIELDS_VERIFIED = "fields_verified"
    SHIPMENT_MATCHED = "shipment_matched"


class AuthenticityStatus(str, Enum):
    """Deliberately explicit: content checks are not authenticity checks."""

    NOT_EXTERNALLY_VERIFIED = "not_externally_verified"


class SupportingDocumentResult(BaseModel):
    """Deterministic verification outcome for one supporting document."""

    claimed_document_type: str
    canonical_document_type: SupportingDocumentType
    document_id: UUID | None
    uploaded: bool
    state: SupportingDocumentState
    detected_document_type: str | None = None
    document_number: str | None = None
    source_page: int | None = None
    extraction_confidence: Decimal | None = None
    ocr_confidence: Decimal | None = None
    checks: list[CrossDocumentCheck] = Field(default_factory=list)
    # The deterministic engine owns this; agents may never change it.
    content_status: str
    authenticity_status: AuthenticityStatus = (
        AuthenticityStatus.NOT_EXTERNALLY_VERIFIED
    )
    required_action: str | None = None
    notes: list[str] = Field(default_factory=list)
    extraction: SupportingDocumentExtraction | None = None
    extraction_summary: str | None = None
    # Presence, parsing, shipment matching and acceptance are distinct states.
    presence_status: str | None = None

    @model_validator(mode="after")
    def set_user_facing_extraction_summary(self) -> "SupportingDocumentResult":
        if self.extraction_summary is None and self.extraction is not None:
            recovered = [
                getattr(self.extraction, name)
                for name in SupportingDocumentCandidates.model_fields
                if getattr(self.extraction, name).value is not None
            ]
            methods = {field.extraction_method.value for field in recovered}
            if "llm_gapfill" in methods:
                self.extraction_summary = "Extracted with AI assistance"
            elif methods & {"regex_label", "ocr_regex"}:
                self.extraction_summary = "Extracted deterministically"
            elif recovered:
                self.extraction_summary = "Extracted with AI assistance"
            else:
                self.extraction_summary = "Partially extracted — retry available"
            if self.content_status == "manual_review" and not recovered:
                self.extraction_summary = "Manual review required"

        if self.presence_status is None:
            failed_matches = any(
                check.status.value == "failed" and check.check_id.endswith("_match")
                for check in self.checks
            )
            if not self.uploaded or self.state in {
                SupportingDocumentState.CLAIMED_ONLY,
                SupportingDocumentState.UNREADABLE,
            }:
                self.presence_status = "unresolved"
            elif self.state is SupportingDocumentState.TYPE_MISMATCH:
                self.presence_status = "invalid"
            elif failed_matches:
                self.presence_status = "shipment_mismatched"
            elif self.content_status == "failed":
                self.presence_status = "invalid"
            elif self.state is SupportingDocumentState.SHIPMENT_MATCHED:
                self.presence_status = "shipment_matched"
            else:
                self.presence_status = "unresolved"
        return self
