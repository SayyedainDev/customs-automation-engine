"""Adapter from the retrieval engine to the API response schema."""

from sqlalchemy.orm import Session

from app.schemas.regulatory_evidence import (
    EvidenceResult,
    EvidenceSearchRequest,
    EvidenceSearchResponse,
)
from app.services.regulatory.retrieval import (
    ScoredEvidence,
    search_regulatory_evidence,
)


def _to_result(evidence: ScoredEvidence) -> EvidenceResult:
    child = evidence.chunk
    parent = evidence.parent
    return EvidenceResult(
        child_evidence_text=child.text,
        parent_evidence_text=parent.text,
        bm25_score=evidence.bm25_score,
        bm25_rank=evidence.bm25_rank,
        dense_score=evidence.dense_score,
        dense_rank=evidence.dense_rank,
        rrf_score=evidence.rrf_score,
        rrf_rank=evidence.rrf_rank,
        cross_encoder_score=evidence.cross_encoder_score,
        cross_encoder_rank=evidence.cross_encoder_rank,
        source_document=child.source_document,
        source_path=child.source_path,
        source_url=child.source_url,
        sro_number=child.sro_number,
        page_number=child.page_number,
        section=child.section,
        issuing_authority=child.issuing_authority,
        effective_date=child.effective_date,
        legal_cutoff_date=child.legal_cutoff_date,
        validation_status=child.validation_status,
        rule_data_version=child.rule_data_version,
    )


def run_evidence_search(
    db: Session,
    request: EvidenceSearchRequest,
) -> EvidenceSearchResponse:
    output = search_regulatory_evidence(
        db,
        query=request.query,
        pct_code=request.pct_code,
        sro_number=request.sro_number,
        issuing_authority=request.issuing_authority,
        destination_country=request.destination_country,
        validation_status=request.validation_status,
        document_type=request.document_type,
        legal_cutoff_date=request.legal_cutoff_date,
        top_k=request.top_k,
        verified_only=request.verified_only,
    )
    results = [_to_result(evidence) for evidence in output.results]
    return EvidenceSearchResponse(
        status=output.status,
        query=request.query,
        normalized_query=output.normalized_query,
        result_count=len(results),
        embedding_model=output.embedding_model,
        reranker_model=output.reranker_model,
        vector_index_version=output.vector_index_version,
        retrieval_mode=output.retrieval_mode,
        degraded_mode=output.degraded_mode,
        retrieval_ms=output.retrieval_ms,
        results=results,
    )
