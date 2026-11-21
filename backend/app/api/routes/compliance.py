from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.schemas.compliance import (
    ComplianceCheckResponse,
    ShipmentComplianceInput,
)
from app.services.compliance.duty_calculator import (
    DutyCalculationRequest,
    DutyCalculationResponse,
    calculate_cascading_customs_taxes,
    format_cascading_steps,
    resolve_tariff_rates_for_hs_code,
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
    db: Session = Depends(get_db_session),
) -> DutyCalculationResponse:
    """Compute exact 2026-27 Pakistan Customs cascading duties and taxes using Decimal."""
    cd_pct = payload.customs_duty_pct
    acd_pct = payload.additional_customs_duty_pct
    rd_pct = payload.regulatory_duty_pct
    st_pct = payload.sales_tax_pct
    wht_pct = payload.advance_income_tax_pct
    rate_source = "caller_supplied"
    tariff_year: str | None = None
    source_page: int | None = None

    if cd_pct is None:
        resolved = resolve_tariff_rates_for_hs_code(db, payload.hs_code)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "No confirmed statutory customs duty rate was found for the supplied HS code; "
                    "provide customs_duty_pct explicitly."
                ),
            )
        cd_pct = resolved.customs_duty_pct
        acd_pct = acd_pct if acd_pct is not None else resolved.additional_customs_duty_pct
        rd_pct = rd_pct if rd_pct is not None else resolved.regulatory_duty_pct
        st_pct = st_pct if st_pct is not None else resolved.sales_tax_pct
        wht_pct = wht_pct if wht_pct is not None else resolved.advance_income_tax_pct
        rate_source = resolved.rate_source
        tariff_year = resolved.tariff_year
        source_page = resolved.source_page

    breakdown = calculate_cascading_customs_taxes(
        assessed_value=payload.assessed_value,
        customs_duty_pct=cd_pct,
        additional_customs_duty_pct=acd_pct,
        regulatory_duty_pct=rd_pct,
        federal_excise_duty_pct=payload.federal_excise_duty_pct,
        sales_tax_pct=st_pct,
        value_addition_tax_pct=payload.value_addition_tax_pct,
        advance_income_tax_pct=wht_pct,
    )
    return DutyCalculationResponse(
        hs_code=payload.hs_code,
        breakdown=breakdown.to_dict(),
        formula_steps=format_cascading_steps(breakdown),
        rate_source=rate_source,
        tariff_year=tariff_year,
        source_page=source_page,
    )
