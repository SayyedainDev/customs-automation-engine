"""Hybrid regulatory evidence retrieval (Phase 3B production RAG).

Flow:
    query -> normalization -> metadata filters
      -> BM25 keyword retrieval
      +  dense vector retrieval (persistent store, on-the-fly for un-indexed)
      -> Reciprocal Rank Fusion
      -> cross-encoder reranking (small candidate set)
      -> parent-section expansion
      -> verified evidence results (with per-stage scores)

The dense stage uses a real sentence-transformer when available, else an
explicitly labelled degraded hashing embedder (``degraded_mode=true``). The
reranker is a real cross-encoder when available, else a labelled lexical
fallback. The evidence_not_found decision uses a deterministic lexical relevance
gate, independent of which reranker model is active, so swapping the reranker
never changes whether evidence is deemed to exist.
"""

from __future__ import annotations

import math
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.regulatory import RegulatoryChunk
from app.services.regulatory.embeddings import (
    EmbeddingProvider,
    get_embedding_provider,
)
from app.services.regulatory.lexical_index import (
    postgres_fts_available,
    search_lexical_candidates,
)
from app.services.regulatory.reranker import Reranker, get_reranker
from app.services.regulatory.vector_cache import get_vector_matrix
from app.services.regulatory.vector_store import (
    VECTOR_INDEX_VERSION,
    get_vectors_for,
)

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9]+")
_PCT = re.compile(r"\b(\d{4})\.?(\d{4})\b")
_SRO = re.compile(r"(\d{2,4})\s*\(?\s*i\s*\)?\s*[/\-]?\s*(\d{4})", re.IGNORECASE)

VERIFIED_STATUSES = {"verified", "partially_verified"}
RRF_K = 60
RERANK_TOP_N_DEFAULT = 25
# Deterministic relevance floor (reranker-independent) for evidence_not_found.
MIN_LEXICAL_RELEVANCE = 0.5
# A chunk's declared pct_codes/sro_number/destination metadata is trusted to
# BOOST an already-relevant candidate, never to manufacture relevance out of
# a passage with almost no real lexical overlap with the query. Found live: a
# page listing Schedule-III negative-list items (tobacco, medical devices,
# seeds, plant material - a completely unrelated part of the same source
# document) was tagged at ingestion with every one of the five supported PCT
# codes, including 61091000. Its real overlap with a cotton-T-shirt query was
# 0.083 (only the word "pct" in common) - noise - but the flat +0.5 metadata
# bonus alone cleared MIN_LEXICAL_RELEVANCE. A genuinely relevant chunk about
# the same product measured 0.75-0.80 overlap on the same queries, so 0.20 is
# a wide, general margin between "actually about this" and "shares a stray
# metadata tag with this" - it is not tuned to this one document.
MIN_BASE_LEXICAL_OVERLAP = 0.20
RETRIEVAL_MODE = "hybrid_dense_bm25_rrf_cross_encoder"
#: Reported instead when the stored PostgreSQL full-text index serves the
#: lexical stage, so a response says which path actually ran.
RETRIEVAL_MODE_PERSISTENT = "hybrid_dense_pgfts_rrf_cross_encoder"
#: How many candidates each stage contributes before fusion. Large enough
#: that RRF still has something to fuse, small enough that the per-request
#: work stops scaling with the corpus.
LEXICAL_CANDIDATE_LIMIT = 200
DENSE_CANDIDATE_LIMIT = 200


def normalize_pct_token(value: str | None) -> str | None:
    if not value:
        return None
    match = _PCT.search(value)
    if match:
        return match.group(1) + match.group(2)
    digits = re.sub(r"\D", "", value)
    return digits if len(digits) == 8 else None


def _special_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _PCT.finditer(text):
        tokens.append("pct" + match.group(1) + match.group(2))
    for match in _SRO.finditer(text):
        tokens.append(f"sro{match.group(1)}i{match.group(2)}")
    return tokens


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    return _WORD.findall(lowered) + _special_tokens(lowered)


class BM25:
    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_len = [len(doc) for doc in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.doc_freqs: list[Counter] = [Counter(doc) for doc in corpus_tokens]
        df: Counter = Counter()
        for doc in self.doc_freqs:
            df.update(doc.keys())
        self.n = len(corpus_tokens)
        self.idf = {
            term: math.log(1 + (self.n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def scores(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * self.n
        for term in query_tokens:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for index, freqs in enumerate(self.doc_freqs):
                tf = freqs.get(term, 0)
                if tf == 0:
                    continue
                denom = tf + self.k1 * (
                    1 - self.b + self.b * (self.doc_len[index] / (self.avgdl or 1))
                )
                scores[index] += idf * (tf * (self.k1 + 1)) / denom
        return scores


@dataclass
class ScoredEvidence:
    chunk: RegulatoryChunk
    parent: RegulatoryChunk
    bm25_score: float
    bm25_rank: int
    dense_score: float
    dense_rank: int
    rrf_score: float
    rrf_rank: int
    cross_encoder_score: float
    cross_encoder_rank: int
    # Backwards-compatible alias used by older callers/tests.
    @property
    def vector_rank(self) -> int:
        return self.dense_rank

    @property
    def reranker_score(self) -> float:
        return self.cross_encoder_score


@dataclass
class RetrievalOutput:
    status: str
    normalized_query: str
    results: list[ScoredEvidence] = field(default_factory=list)
    embedding_model: str = ""
    reranker_model: str = ""
    vector_index_version: str = VECTOR_INDEX_VERSION
    retrieval_mode: str = RETRIEVAL_MODE
    degraded_mode: bool = False
    retrieval_ms: float = 0.0


def _rank_map(scores: list[float]) -> dict[int, int]:
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return {index: rank + 1 for rank, index in enumerate(order)}


def _lexical_relevance(
    query_tokens: set[str],
    chunk: RegulatoryChunk,
    pct_code: str | None,
    destination_country: str | None,
) -> float:
    """Deterministic relevance floor, independent of the active reranker.

    Metadata bonuses (PCT/SRO/destination match) only ever apply on top of a
    real baseline of lexical overlap - see MIN_BASE_LEXICAL_OVERLAP. Below
    that baseline, the chunk's own declared metadata is not trusted enough to
    manufacture relevance by itself, because that metadata can be wrong or
    too coarse (a whole source document tagged with every PCT code it covers
    anywhere, not the specific code each page is actually about).
    """
    doc_tokens = set(tokenize(chunk.text))
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & doc_tokens) / len(query_tokens)
    if overlap < MIN_BASE_LEXICAL_OVERLAP:
        return overlap
    score = overlap
    if pct_code and chunk.pct_codes and pct_code in chunk.pct_codes:
        score += 0.5
    if chunk.sro_number and any(
        token.startswith("sro") and token in doc_tokens for token in query_tokens
    ):
        score += 0.5
    if destination_country and destination_country.lower() in chunk.text.lower():
        score += 0.25
    return score


# Backwards-compatible export name.
_reranker_score = _lexical_relevance


def _apply_metadata_filters(
    children: list[RegulatoryChunk],
    *,
    pct_code: str | None,
    sro_number: str | None,
    issuing_authority: str | None,
    validation_status: str | None,
    document_type: str | None,
    legal_cutoff_date: date | None,
    verified_only: bool,
) -> list[RegulatoryChunk]:
    result = children
    # A filter is applied only when the caller supplied it.
    if validation_status:
        result = [c for c in result if c.validation_status == validation_status]
    elif verified_only:
        result = [c for c in result if c.validation_status in VERIFIED_STATUSES]
    if pct_code:
        result = [c for c in result if c.pct_codes and pct_code in c.pct_codes]
    if sro_number:
        target = sro_number.strip().casefold()
        result = [c for c in result if (c.sro_number or "").strip().casefold() == target]
    if issuing_authority:
        needle = issuing_authority.casefold()
        result = [c for c in result if needle in (c.issuing_authority or "").casefold()]
    if document_type:
        result = [c for c in result if c.document_type == document_type]
    if legal_cutoff_date:
        result = [
            c
            for c in result
            if c.legal_cutoff_date is not None and c.legal_cutoff_date <= legal_cutoff_date
        ]
    return result


def _dense_scores(
    db: Session,
    children: list[RegulatoryChunk],
    query_vector: np.ndarray,
    embedder: EmbeddingProvider,
) -> list[float]:
    chunk_ids = [c.chunk_id for c in children]
    persisted = get_vectors_for(
        db,
        chunk_ids,
        embedding_model=embedder.model_name,
        embedding_dim=embedder.dimension,
    )
    missing = [
        child
        for child in children
        if (
            child.chunk_id not in persisted
            or persisted[child.chunk_id].ndim != 1
            or persisted[child.chunk_id].size != query_vector.size
        )
    ]
    if missing:
        # On-the-fly embedding for chunks not yet in the persistent index, for
        # rows built by another model, and for malformed vector payloads. This
        # is intentionally read-only: rebuilding the stored index is an
        # explicit maintenance action.
        vectors = embedder.embed([c.text for c in missing])
        for child, vector in zip(missing, vectors):
            persisted[child.chunk_id] = vector
    query_norm = query_vector / (np.linalg.norm(query_vector) or 1.0)
    scores: list[float] = []
    for child in children:
        doc = persisted.get(child.chunk_id)
        if doc is None or doc.size == 0:
            scores.append(0.0)
            continue
        if doc.ndim != 1 or doc.size != query_norm.size:
            logger.error(
                "Skipping incompatible regulatory vector for chunk %s: "
                "query_dim=%d, vector_shape=%s, embedding_model=%s.",
                child.chunk_id,
                query_norm.size,
                doc.shape,
                embedder.model_name,
            )
            scores.append(0.0)
            continue
        doc_norm = doc / (np.linalg.norm(doc) or 1.0)
        scores.append(float(np.dot(query_norm, doc_norm)))
    return scores


def _persistent_candidates(
    db: Session,
    *,
    query: str,
    query_vector: np.ndarray,
    embedder: EmbeddingProvider,
    pct_code: str | None,
    sro_number: str | None,
    issuing_authority: str | None,
    validation_status: str | None,
    document_type: str | None,
    legal_cutoff_date: date | None,
    verified_only: bool,
) -> tuple[list[str], dict[str, float], dict[str, float]]:
    """Candidate chunk ids from the stored FTS index and the cached matrix.

    Returns the union of both stages plus their raw scores. Neither stage
    rebuilds anything per request: the lexical side is a GIN index lookup with
    the metadata filters applied in the same statement, and the dense side is a
    matrix multiply against a matrix loaded once per index generation.
    """
    lexical = search_lexical_candidates(
        db,
        query=query,
        limit=LEXICAL_CANDIDATE_LIMIT,
        pct_code=pct_code,
        sro_number=sro_number,
        issuing_authority=issuing_authority,
        document_type=document_type,
        validation_status=validation_status,
        verified_statuses=tuple(sorted(VERIFIED_STATUSES)) if verified_only else None,
        legal_cutoff_date=legal_cutoff_date,
    )
    lexical_scores = {hit.chunk_id: hit.rank for hit in lexical}

    matrix = get_vector_matrix(
        db, embedding_model=embedder.model_name, dimension=embedder.dimension
    )
    dense_scores: dict[str, float] = {}
    if matrix.chunk_ids:
        similarities = matrix.similarities(query_vector)
        top = np.argsort(similarities)[::-1][:DENSE_CANDIDATE_LIMIT]
        dense_scores = {matrix.chunk_ids[i]: float(similarities[i]) for i in top}

    ordered = list(lexical_scores) + [c for c in dense_scores if c not in lexical_scores]
    return ordered, lexical_scores, dense_scores


def search_regulatory_evidence(
    db: Session,
    *,
    query: str,
    pct_code: str | None = None,
    sro_number: str | None = None,
    issuing_authority: str | None = None,
    destination_country: str | None = None,
    validation_status: str | None = None,
    document_type: str | None = None,
    legal_cutoff_date: date | None = None,
    top_k: int = 5,
    verified_only: bool = True,
    rerank_top_n: int = RERANK_TOP_N_DEFAULT,
    embedder: EmbeddingProvider | None = None,
    reranker: Reranker | None = None,
) -> RetrievalOutput:
    started = time.perf_counter()
    embedder = embedder or get_embedding_provider()
    reranker = reranker or get_reranker()
    normalized_pct = normalize_pct_token(pct_code)

    augmented_query = query
    if normalized_pct:
        augmented_query += f" {normalized_pct}"
    if sro_number:
        augmented_query += f" {sro_number}"
    if destination_country:
        augmented_query += f" {destination_country}"

    degraded = bool(embedder.degraded or reranker.degraded)
    retrieval_mode = RETRIEVAL_MODE

    def output(status: str, results: list[ScoredEvidence]) -> RetrievalOutput:
        return RetrievalOutput(
            status=status,
            normalized_query=augmented_query,
            results=results,
            embedding_model=embedder.model_name,
            reranker_model=reranker.model_name,
            retrieval_mode=retrieval_mode,
            degraded_mode=degraded,
            retrieval_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    query_vector = embedder.embed_query(augmented_query)
    persistent = postgres_fts_available(db)
    if persistent:
        retrieval_mode = RETRIEVAL_MODE_PERSISTENT

    if persistent:
        # Candidate generation happens in the database and against a matrix
        # that was loaded once, so nothing here scales with the corpus except a
        # single matrix multiply.
        candidate_ids, lexical_by_id, dense_by_id = _persistent_candidates(
            db,
            query=augmented_query,
            query_vector=query_vector,
            embedder=embedder,
            pct_code=normalized_pct,
            sro_number=sro_number,
            issuing_authority=issuing_authority,
            validation_status=validation_status,
            document_type=document_type,
            legal_cutoff_date=legal_cutoff_date,
            verified_only=verified_only,
        )
        if not candidate_ids:
            return output("evidence_not_found", [])
        by_id = {
            chunk.chunk_id: chunk
            for chunk in db.execute(
                select(RegulatoryChunk).where(
                    RegulatoryChunk.chunk_id.in_(candidate_ids)
                )
            ).scalars()
        }
        children = [by_id[cid] for cid in candidate_ids if cid in by_id]
        # The dense stage does not apply metadata filters in SQL, so anything it
        # contributed still has to satisfy them.
        children = _apply_metadata_filters(
            children,
            pct_code=normalized_pct,
            sro_number=sro_number,
            issuing_authority=issuing_authority,
            validation_status=validation_status,
            document_type=document_type,
            legal_cutoff_date=legal_cutoff_date,
            verified_only=verified_only,
        )
        if not children:
            return output("evidence_not_found", [])
        parent_ids = {c.parent_chunk_id for c in children if c.parent_chunk_id}
        parents = {
            chunk.chunk_id: chunk
            for chunk in db.execute(
                select(RegulatoryChunk).where(RegulatoryChunk.chunk_id.in_(parent_ids))
            ).scalars()
        } if parent_ids else {}
        bm25_scores = [lexical_by_id.get(c.chunk_id, 0.0) for c in children]
        dense_scores = [dense_by_id.get(c.chunk_id, 0.0) for c in children]
    else:
        # In-memory fallback: no stored full-text index on this dialect.
        parents = {
            chunk.chunk_id: chunk
            for chunk in db.execute(
                select(RegulatoryChunk).where(RegulatoryChunk.is_parent.is_(True))
            ).scalars()
        }
        children = list(
            db.execute(
                select(RegulatoryChunk).where(RegulatoryChunk.is_parent.is_(False))
            ).scalars()
        )
        children = _apply_metadata_filters(
            children,
            pct_code=normalized_pct,
            sro_number=sro_number,
            issuing_authority=issuing_authority,
            validation_status=validation_status,
            document_type=document_type,
            legal_cutoff_date=legal_cutoff_date,
            verified_only=verified_only,
        )
        if not children:
            return output("evidence_not_found", [])
        bm25 = BM25([tokenize(c.text) for c in children])
        bm25_scores = bm25.scores(tokenize(augmented_query))
        dense_scores = _dense_scores(db, children, query_vector, embedder)

    query_tokens = tokenize(augmented_query)
    bm25_ranks = _rank_map(bm25_scores)
    dense_ranks = _rank_map(dense_scores)
    rrf_values = [
        1.0 / (RRF_K + bm25_ranks[i]) + 1.0 / (RRF_K + dense_ranks[i])
        for i in range(len(children))
    ]
    rrf_ranks = _rank_map(rrf_values)

    candidate_indexes = sorted(
        range(len(children)), key=lambda i: rrf_values[i], reverse=True
    )[:rerank_top_n]

    # Cross-encoder reranking (real model or labelled fallback) for ordering.
    candidate_texts = [children[i].text for i in candidate_indexes]
    reranker_scores = reranker.score(augmented_query, candidate_texts)
    order = sorted(
        range(len(candidate_indexes)),
        key=lambda pos: reranker_scores[pos],
        reverse=True,
    )
    cross_rank_by_index = {
        candidate_indexes[pos]: rank + 1 for rank, pos in enumerate(order)
    }

    query_token_set = set(query_tokens)
    results: list[ScoredEvidence] = []
    seen_parents: set[str] = set()
    for pos in order:
        index = candidate_indexes[pos]
        child = children[index]
        # Deterministic relevance gate (independent of reranker model).
        if _lexical_relevance(
            query_token_set, child, normalized_pct, destination_country
        ) < MIN_LEXICAL_RELEVANCE:
            continue
        parent = parents.get(child.parent_chunk_id or "", child)
        if parent.chunk_id in seen_parents:
            continue
        seen_parents.add(parent.chunk_id)
        results.append(
            ScoredEvidence(
                chunk=child,
                parent=parent,
                bm25_score=round(bm25_scores[index], 6),
                bm25_rank=bm25_ranks[index],
                dense_score=round(dense_scores[index], 6),
                dense_rank=dense_ranks[index],
                rrf_score=round(rrf_values[index], 6),
                rrf_rank=rrf_ranks[index],
                cross_encoder_score=round(float(reranker_scores[pos]), 6),
                cross_encoder_rank=cross_rank_by_index[index],
            )
        )
        if len(results) >= top_k:
            break

    if not results:
        return output("evidence_not_found", [])
    return output("ok", results)
