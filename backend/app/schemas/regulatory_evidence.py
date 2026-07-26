from datetime import date

from pydantic import BaseModel, Field


class EvidenceSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    pct_code: str | None = None
    destination_country: str | None = None
    # Optional metadata filters (applied only when provided).
    sro_number: str | None = None
    issuing_authority: str | None = None
    validation_status: str | None = None
    document_type: str | None = None
    legal_cutoff_date: date | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    verified_only: bool = True


class EvidenceResult(BaseModel):
    child_evidence_text: str
    parent_evidence_text: str
    # Per-stage retrieval scores and ranks.
    bm25_score: float
    bm25_rank: int
    dense_score: float
    dense_rank: int
    rrf_score: float
    rrf_rank: int
    cross_encoder_score: float
    cross_encoder_rank: int
    # Provenance.
    source_document: str
    source_path: str
    source_url: str | None
    sro_number: str | None
    page_number: int | None
    section: str | None
    issuing_authority: str | None
    effective_date: date | None
    legal_cutoff_date: date | None
    validation_status: str
    rule_data_version: str


class EvidenceSearchResponse(BaseModel):
    status: str  # "ok" | "evidence_not_found"
    query: str
    normalized_query: str
    result_count: int
    # Model / index / mode metadata.
    embedding_model: str
    reranker_model: str
    vector_index_version: str
    retrieval_mode: str
    degraded_mode: bool
    retrieval_ms: float
    results: list[EvidenceResult]
