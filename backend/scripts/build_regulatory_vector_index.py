"""Phase 3B: build (or rebuild) the persistent regulatory dense vector index.

Idempotent. Embeds only new / changed / stale child chunks. Uses the real
sentence-transformer when available, otherwise the explicitly labelled degraded
hashing embedder.

Run:
    python -m scripts.build_regulatory_vector_index          # incremental
    python -m scripts.build_regulatory_vector_index --rebuild
"""

from __future__ import annotations

import argparse

from app.services.regulatory.embeddings import get_embedding_provider
from app.services.regulatory.index_db import index_session
from app.services.regulatory.ingestion import ingest_all
from app.services.regulatory.vector_store import build_vector_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the regulatory vector index.")
    parser.add_argument("--rebuild", action="store_true", help="re-embed every chunk")
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="do not run text ingestion first",
    )
    args = parser.parse_args()

    db = index_session()
    try:
        if not args.skip_ingest:
            ingest_all(db)  # ensure chunks exist (idempotent)
        embedder = get_embedding_provider()
        report = build_vector_index(db, embedder, rebuild=args.rebuild)
    finally:
        db.close()

    print("Regulatory vector index build")
    print(f"  embedding model      : {report.embedding_model}")
    print(f"  degraded mode        : {report.degraded}")
    print(f"  embedding dimension  : {report.embedding_dim}")
    print(f"  index version        : {report.index_version}")
    print(f"  collection           : {report.collection}")
    print(f"  chunks discovered    : {report.chunks_discovered}")
    print(f"  chunks embedded (new): {report.chunks_embedded}")
    print(f"  chunks updated       : {report.chunks_updated}")
    print(f"  chunks skipped       : {report.chunks_skipped}")
    print(f"  stale chunks removed : {report.stale_removed}")
    print(f"  failures             : {report.failures}")
    print(f"  execution seconds    : {report.execution_seconds}")


if __name__ == "__main__":
    main()
