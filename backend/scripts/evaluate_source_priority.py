"""Evaluate query-aware source priority against the persistent PostgreSQL corpus.

The report records raw PostgreSQL lexical candidates, cached-matrix dense
candidates and the accepted RRF/reranker results for a fixed query set. It is a
diagnostic only: it does not ingest, rebuild BM25, download models or call
Groq.

Run:
    python -m scripts.evaluate_source_priority
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.models.regulatory import RegulatoryChunk
from app.services.regulatory.embeddings import get_embedding_provider
from app.services.regulatory.lexical_index import search_lexical_candidates
from app.services.regulatory.reranker import get_reranker
from app.services.regulatory.retrieval import (
    search_regulatory_evidence,
)
from app.services.regulatory.source_kinds import resolve_source_kind
from app.services.regulatory.vector_cache import get_vector_matrix

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = BACKEND_ROOT / "tests" / "fixtures" / "query_aware_source_priority_eval.json"
REPORT_PATH = BACKEND_ROOT / "reports" / "query_aware_source_priority_evaluation.json"


def _row(chunk: RegulatoryChunk, *, score: float, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "score": round(float(score), 6),
        "source_document": chunk.source_document,
        "page_number": chunk.page_number,
        "source_kind": resolve_source_kind(chunk),
        "currency_status": chunk.currency_status,
        "validation_status": chunk.validation_status,
        "chunk_id": chunk.chunk_id,
    }


def _load_chunks(db, chunk_ids: list[str]) -> dict[str, RegulatoryChunk]:
    if not chunk_ids:
        return {}
    return {
        chunk.chunk_id: chunk
        for chunk in db.execute(
            select(RegulatoryChunk).where(RegulatoryChunk.chunk_id.in_(chunk_ids))
        ).scalars()
    }


def evaluate() -> dict[str, Any]:
    loaded = cast(
        dict[str, Any],
        json.loads(CASES_PATH.read_text(encoding="utf-8")),
    )
    cases = cast(list[dict[str, Any]], loaded["cases"])
    db = Session(get_engine())
    embedder = get_embedding_provider()
    reranker = get_reranker()
    rows: list[dict[str, Any]] = []
    try:
        matrix = get_vector_matrix(
            db,
            embedding_model=embedder.model_name,
            dimension=embedder.dimension,
        )
        for case in cases:
            query = case["query"]
            pct_code = case.get("pct_code")
            destination = case.get("destination_country")
            augmented = query
            if pct_code:
                augmented += f" {pct_code} {pct_code[:4]}.{pct_code[4:]}"
            if destination:
                augmented += f" {destination}"

            lexical_hits = search_lexical_candidates(
                db,
                query=augmented,
                limit=5,
                pct_code=pct_code,
                verified_statuses=("partially_verified", "verified"),
            )
            lexical_chunks = _load_chunks(
                db, [hit.chunk_id for hit in lexical_hits]
            )
            lexical = [
                _row(lexical_chunks[hit.chunk_id], score=hit.rank, rank=index)
                for index, hit in enumerate(lexical_hits, start=1)
                if hit.chunk_id in lexical_chunks
            ]

            query_vector = embedder.embed_query(augmented)
            similarities = matrix.similarities(query_vector)
            dense_indexes: list[int] = (
                [
                    int(index)
                    for index in np.argsort(similarities)[::-1][:5].tolist()
                ]
                if matrix.chunk_ids
                else []
            )
            dense_ids = [matrix.chunk_ids[index] for index in dense_indexes]
            dense_chunks = _load_chunks(db, dense_ids)
            dense = [
                _row(
                    dense_chunks[chunk_id],
                    score=float(similarities[index]),
                    rank=rank,
                )
                for rank, (index, chunk_id) in enumerate(
                    zip(dense_indexes, dense_ids), start=1
                )
                if chunk_id in dense_chunks
            ]

            output = search_regulatory_evidence(
                db,
                query=query,
                pct_code=pct_code,
                destination_country=destination,
                top_k=5,
                verified_only=True,
                embedder=embedder,
                reranker=reranker,
            )
            accepted: list[dict[str, Any]] = [
                {
                    "rank": rank,
                    "source_document": item.chunk.source_document,
                    "page_number": item.chunk.page_number,
                    "source_kind": resolve_source_kind(item.chunk),
                    "currency_status": item.chunk.currency_status,
                    "bm25_score": round(item.bm25_score, 6),
                    "bm25_rank": item.bm25_rank,
                    "dense_score": round(item.dense_score, 6),
                    "dense_rank": item.dense_rank,
                    "rrf_score": round(item.rrf_score, 6),
                    "rrf_rank": item.rrf_rank,
                    "reranker_score": round(item.cross_encoder_score, 6),
                    "reranker_rank": item.cross_encoder_rank,
                    "accepted_passage": item.chunk.text,
                }
                for rank, item in enumerate(output.results, start=1)
            ]
            primary = (
                cast(str, accepted[0]["source_document"]) if accepted else None
            )
            expected_primary = cast(str, case["expected_primary_contains"])
            rows.append(
                {
                    **case,
                    "status": output.status,
                    "retrieval_mode": output.retrieval_mode,
                    "latency_ms": output.retrieval_ms,
                    "lexical_candidates": lexical,
                    "dense_candidates": dense,
                    "final_accepted_passages": accepted,
                    "primary_source": primary,
                    "supplemental_sources": [
                        item["source_document"] for item in accepted[1:]
                    ],
                    "expected_primary_matched": bool(
                        primary
                        and expected_primary.casefold()
                        in primary.casefold()
                    ),
                }
            )
    finally:
        db.close()
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": {
            "regulatory_sources": 9,
            "child_chunks": 6756,
            "persistent_lexical_search": True,
            "cached_dense_matrix": True,
        },
        "embedding_model": embedder.model_name,
        "reranker_model": reranker.model_name,
        "real_model_diagnostic": (
            "not available locally; sentence-transformers is not installed"
        ),
        "real_external_calls": False,
        "all_expected_primary_matched": all(
            row["expected_primary_matched"] for row in rows
        ),
        "queries": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")
    for row in report["queries"]:
        print(
            f"{row['id']}: {row['status']} | {row['primary_source']} | "
            f"{row['latency_ms']} ms | expected={row['expected_primary_matched']}"
        )
    if not report["all_expected_primary_matched"]:
        raise SystemExit("One or more source-priority expectations failed.")


if __name__ == "__main__":
    main()
