from fastapi import APIRouter, HTTPException, status

from app.schemas.compliance import (
    ComplianceCheckResponse,
    ShipmentComplianceInput,
)
from app.services.compliance.duty_calculator import (
    DutyCalculationRequest,
    DutyCalculationResponse,
    calculate_cascading_customs_taxes,
    format_cascading_steps,
)
from app.services.compliance.rule_engine import get_compliance_rule_engine
from app.services.compliance.rule_loader import RuleSourceError


router = APIRouter(
    prefix="/api/v1/compliance",
    tags=["Compliance"],
)


@router.post("/check", response_model=ComplianceCheckResponse)
def check_shipment_compliance(
    payload: ShipmentComplianceInput,
) -> ComplianceCheckResponse:
    """Run deterministic Phase 1 checks against structured regulatory rules."""

    try:
        engine = get_compliance_rule_engine()
        return engine.check(payload)
    except RuleSourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post("/calculate-duties", response_model=DutyCalculationResponse)
def calculate_shipment_duties(
    payload: DutyCalculationRequest,
) -> DutyCalculationResponse:
    """Compute exact 2026-27 Pakistan Customs cascading duties and taxes using Decimal."""
    breakdown = calculate_cascading_customs_taxes(
        assessed_value=payload.assessed_value,
        customs_duty_pct=payload.customs_duty_pct,
        additional_customs_duty_pct=payload.additional_customs_duty_pct,
        regulatory_duty_pct=payload.regulatory_duty_pct,
        federal_excise_duty_pct=payload.federal_excise_duty_pct,
        sales_tax_pct=payload.sales_tax_pct,
        value_addition_tax_pct=payload.value_addition_tax_pct,
        advance_income_tax_pct=payload.advance_income_tax_pct,
    )
    return DutyCalculationResponse(
        hs_code=payload.hs_code,
        breakdown=breakdown.to_dict(),
        formula_steps=format_cascading_steps(breakdown),
    )
