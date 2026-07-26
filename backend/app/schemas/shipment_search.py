from pydantic import BaseModel, Field


class ShipmentSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class ShipmentSearchResult(BaseModel):
    workflow_id: str
    score: float
    summary: str


class ShipmentSearchResponse(BaseModel):
    status: str  # "ok" | "no_shipments_indexed"
    retrieval_mode: str  # "semantic" | "semantic_degraded" | "recency_fallback"
    query: str
    result_count: int
    results: list[ShipmentSearchResult]
