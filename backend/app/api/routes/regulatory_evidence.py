from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.schemas.regulatory_evidence import (
    EvidenceSearchRequest,
    EvidenceSearchResponse,
)
from app.services.regulatory.evidence_api import run_evidence_search


router = APIRouter(
    prefix="/api/v1/regulatory-evidence",
    tags=["Regulatory evidence"],
)


@router.post("/search", response_model=EvidenceSearchResponse)
async def search_evidence(
    payload: EvidenceSearchRequest,
    db: Session = Depends(get_db_session),
) -> EvidenceSearchResponse:
    """Hybrid (BM25 + vector + RRF + rerank) retrieval over ingested evidence.

    Returns ``status: evidence_not_found`` with no results when no verified
    evidence matches, rather than a confident answer without evidence.
    """

    return run_evidence_search(db=db, request=payload)
