import numpy as np
from uuid import UUID
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.shipment_chunks import ShipmentDocumentChunk
from app.services.regulatory.embeddings import get_embedding_provider
from app.services.regulatory.retrieval import BM25, tokenize, _rank_map
from app.services.regulatory.reranker import get_reranker

RRF_K = 60

@dataclass
class RetrievedChunk:
    chunk_id: UUID
    document_type: str
    document_name: str
    page_number: int
    section: str
    text: str
    parent_text: str
    rrf_score: float
    evidence_status: str

class ShipmentDocumentRetriever:
    def __init__(self, db: Session):
        self.db = db
        self.embedder = get_embedding_provider()
        self.reranker = get_reranker()

    def _evaluate_evidence_gate(self, query: str, chunk: ShipmentDocumentChunk, reranker_score: float) -> str:
        # 8. lexical or semantic relevance clears a calibrated threshold
        if reranker_score < -2.0: # using typical cross-encoder threshold
            return "shipment_evidence_unavailable"
            
        # Simplified deterministic gate for prototype
        query_lower = query.lower()
        
        # 3. expected document type matches when requested
        if "invoice" in query_lower and chunk.document_type != "commercial_invoice":
            return "shipment_evidence_conflicting"
        if "packing list" in query_lower and chunk.document_type != "packing_list":
            return "shipment_evidence_conflicting"
        if "form e" in query_lower and chunk.document_type not in ("form_e", "psw_declaration"):
            return "shipment_evidence_conflicting"
        if "certificate of origin" in query_lower and chunk.document_type != "certificate_of_origin":
            return "shipment_evidence_conflicting"
            
        # 4. requested section or concept is relevant
        if "weight" in query_lower and chunk.section not in ("shipment_weights", "weight_totals", "package_information", "general_content", "product_line"):
            return "shipment_evidence_partial"
            
        return "shipment_evidence_verified"

    def retrieve(self, shipment_id: UUID, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        # 1. Mandatory filters: shipment_id AND active=True
        # Only retrieve child chunks for ranking
        all_active = list(
            self.db.execute(
                select(ShipmentDocumentChunk)
                .where(
                    ShipmentDocumentChunk.shipment_id == shipment_id,
                    ShipmentDocumentChunk.active == True
                )
            ).scalars()
        )
        if not all_active:
            return []
            
        chunks = [c for c in all_active if not c.is_parent]
        parents = {c.id: c for c in all_active if c.is_parent}
        
        if not chunks:
            # Fallback if no child chunks exist
            chunks = list(parents.values())

        # 2. BM25 Search
        query_tokens = tokenize(query)
        bm25 = BM25([tokenize(c.search_text or c.text) for c in chunks])
        bm25_scores = bm25.scores(query_tokens)

        # 3. Dense Embeddings
        query_vector = self.embedder.embed_query(query)
        query_norm = query_vector / (np.linalg.norm(query_vector) or 1.0)
        dense_scores = []
        for c in chunks:
            if not c.embedding:
                dense_scores.append(0.0)
                continue
            doc = np.array(c.embedding)
            if doc.size == 0 or doc.size != query_norm.size:
                dense_scores.append(0.0)
                continue
            doc_norm = doc / (np.linalg.norm(doc) or 1.0)
            dense_scores.append(float(np.dot(query_norm, doc_norm)))

        # 4. Reciprocal Rank Fusion (RRF)
        bm25_ranks = _rank_map(bm25_scores)
        dense_ranks = _rank_map(dense_scores)
        rrf_values = [
            1.0 / (RRF_K + bm25_ranks[i]) + 1.0 / (RRF_K + dense_ranks[i])
            for i in range(len(chunks))
        ]
        
        # 5. Reranking using cross_encoder
        candidate_indexes = sorted(
            range(len(chunks)), key=lambda i: rrf_values[i], reverse=True
        )[:max(top_k * 2, 10)]

        if not candidate_indexes:
            return []

        candidate_texts = [chunks[i].text for i in candidate_indexes]
        try:
            reranker_scores = self.reranker.score(query, candidate_texts)
        except Exception:
            # Degraded mode
            reranker_scores = [rrf_values[i] for i in candidate_indexes]

        order = sorted(
            range(len(candidate_indexes)),
            key=lambda pos: reranker_scores[pos],
            reverse=True,
        )

        results = []
        for pos in order[:top_k]:
            index = candidate_indexes[pos]
            chunk = chunks[index]
            score = float(reranker_scores[pos])
            
            evidence_status = self._evaluate_evidence_gate(query, chunk, score)
            
            if evidence_status in ("shipment_evidence_unavailable", "shipment_evidence_conflicting"):
                continue
                
            parent_text = chunk.text
            if chunk.parent_chunk_id and chunk.parent_chunk_id in parents:
                parent_text = parents[chunk.parent_chunk_id].text
                
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    document_type=chunk.document_type,
                    document_name=chunk.document_name,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    text=chunk.text,
                    parent_text=parent_text,
                    rrf_score=round(score, 6),
                    evidence_status=evidence_status
                )
            )

        return results
