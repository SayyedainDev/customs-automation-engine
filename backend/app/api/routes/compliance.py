from fastapi import APIRouter, HTTPException, status

from app.schemas.compliance import (
    ComplianceCheckResponse,
    ShipmentComplianceInput,
)
from app.services.compliance.rule_engine import get_compliance_rule_engine
from app.services.compliance.rule_loader import RuleSourceError


router = APIRouter(
    prefix="/api/v1/compliance",
    tags=["Compliance"],
)


@router.post("/check", response_model=ComplianceCheckResponse)
async def check_shipment_compliance(
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
