from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.schemas.shipment_search import ShipmentSearchRequest, ShipmentSearchResponse, ShipmentSearchResult
from app.services.shipment_search.search import search_shipments

router = APIRouter(
    prefix="/api/v1/shipment-search",
    tags=["Shipment history search"],
)


@router.post("/search", response_model=ShipmentSearchResponse)
async def search(
    payload: ShipmentSearchRequest,
    db: Session = Depends(get_db_session),
) -> ShipmentSearchResponse:
    """Deterministic semantic search over finalized shipment summaries.

    Returns ``status: no_shipments_indexed`` with no results when the index
    is empty, rather than a confident-looking empty match.
    """
    output = search_shipments(db, payload.query, top_k=payload.top_k)
    results = [
        ShipmentSearchResult(workflow_id=r.workflow_id, score=r.score, summary=r.summary)
        for r in output.results
    ]
    return ShipmentSearchResponse(
        status=output.status,
        retrieval_mode=output.retrieval_mode,
        query=payload.query,
        result_count=len(results),
        results=results,
    )
