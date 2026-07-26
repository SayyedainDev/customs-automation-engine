from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator

from app.services.compliance.rule_loader import normalize_pct_code


class ComplianceCheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"
    NOT_APPLICABLE = "not_applicable"


class ShipmentComplianceInput(BaseModel):
    """Structured input for one product line in a Phase 1 shipment check."""

    model_config = ConfigDict(str_strip_whitespace=True)

    product_name: str | None = None
    pct_code: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    invoice_line_total: Decimal | None = None
    invoice_total: Decimal | None = None
    net_weight: Decimal | None = None
    gross_weight: Decimal | None = None
    destination_country: str | None = None
    shipment_date: date | None = None
    letter_of_credit_date: date | None = None
    uploaded_document_types: list[str] | None = None

    @field_validator("pct_code")
    @classmethod
    def normalize_input_pct_code(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        return normalize_pct_code(value)


class ComplianceCheckResult(BaseModel):
    check_id: str
    check_name: str
    status: ComplianceCheckStatus
    message: str
    pct_code: str | None
    required_document: str | None
    source_document: str | None
    sro_number: str | None
    source_url: str | None
    source_page: int | None
    source_locator: str | None
    issuing_authority: str | None
    effective_date: date | None
    legal_cutoff_date: date | None
    rule_data_version: str | None
    validation_status: str | None


class ComplianceCheckResponse(BaseModel):
    pct_code: str | None
    supported_product: bool
    rule_data_version: str
    overall_status: ComplianceCheckStatus
    is_compliant: bool
    checks: list[ComplianceCheckResult]
    # Data-driven executable-rule evaluation (Stage 2). These carry full legal
    # provenance and mirror the authoritative checks above; they are surfaced so
    # future products can be expressed as validated data rather than Python.
    executable_rule_checks: list[ComplianceCheckResult] = []
    executable_overall_status: ComplianceCheckStatus | None = None
