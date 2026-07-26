from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.schemas.compliance_explanation import (
    ExplanationRequest,
    ExplanationResponse,
)
from app.services.regulatory.explanation import explain_compliance_check


router = APIRouter(
    prefix="/api/v1/compliance",
    tags=["Compliance explanation"],
)


@router.post("/explain", response_model=ExplanationResponse)
async def explain_compliance_decision(
    payload: ExplanationRequest,
    db: Session = Depends(get_db_session),
) -> ExplanationResponse:
    """Explain a final deterministic decision using grounded regulatory evidence.

    The deterministic status is echoed unchanged. The explanation is grounded
    only in retrieved verified evidence and never alters the decision.
    """

    return explain_compliance_check(db=db, request=payload)
