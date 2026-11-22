"""Idempotent cold-start bootstrap for tariff lines, regulatory chunks, and vectors.

Run automatically at Docker container startup after ``init_db`` and
``apply_migrations`` so a fresh ``docker compose up --build`` boots with a
populated FY2025-26 Pakistan Customs Tariff table, indexed regulatory corpus,
and dense vector index instead of empty tables.

Safe to re-run on every container restart:
- Tariff lines are loaded only when ``tariff_lines`` is empty (unless ``--force-tariff``).
- Regulatory ingestion skips unchanged sources by SHA-256 checksum.
- Vector indexing skips fresh vectors matching checksum, model, and dimension.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import get_engine  # noqa: E402
from app.models.tariff import TariffLine  # noqa: E402
from app.services.regulatory.embeddings import get_embedding_provider  # noqa: E402
from app.services.regulatory.ingestion import ingest_all  # noqa: E402
from app.services.regulatory.vector_store import build_vector_index  # noqa: E402
from scripts.load_tariff_lines import DEFAULT_JSON, load_rows  # noqa: E402


@dataclass(frozen=True)
class BootstrapSummary:
    tariff_lines_loaded: int
    tariff_lines_skipped: bool
    regulatory_documents_ingested: int
    regulatory_documents_skipped: int
    vectors_embedded: int
    vectors_skipped: int


def bootstrap_database(
    db: Session,
    *,
    tariff_json_path: Path = DEFAULT_JSON,
    force_tariff: bool = False,
    skip_regulatory: bool = False,
) -> BootstrapSummary:
    """Seed tariff lines and regulatory RAG indexes idempotently."""
    existing_tariffs = int(
        db.execute(select(func.count()).select_from(TariffLine)).scalar() or 0
    )
    tariff_loaded = 0
    tariff_skipped = False
    if (existing_tariffs == 0 or force_tariff) and tariff_json_path.is_file():
        rows = json.loads(tariff_json_path.read_text(encoding="utf-8"))
        tariff_loaded = load_rows(db, rows)
    else:
        tariff_skipped = True

    reg_ingested = 0
    reg_skipped = 0
    vec_embedded = 0
    vec_skipped = 0
    if not skip_regulatory:
        ingest_report = ingest_all(db)
        reg_ingested = ingest_report.documents_ingested + ingest_report.documents_updated
        reg_skipped = ingest_report.documents_skipped_idempotent
        embedder = get_embedding_provider()
        vec_report = build_vector_index(db, embedder)
        vec_embedded = vec_report.chunks_embedded + vec_report.chunks_updated
        vec_skipped = vec_report.chunks_skipped

    return BootstrapSummary(
        tariff_lines_loaded=tariff_loaded,
        tariff_lines_skipped=tariff_skipped,
        regulatory_documents_ingested=reg_ingested,
        regulatory_documents_skipped=reg_skipped,
        vectors_embedded=vec_embedded,
        vectors_skipped=vec_skipped,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tariff-file", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--force-tariff", action="store_true")
    parser.add_argument("--skip-regulatory", action="store_true")
    args = parser.parse_args()

    with Session(get_engine()) as db:
        summary = bootstrap_database(
            db,
            tariff_json_path=args.tariff_file,
            force_tariff=args.force_tariff,
            skip_regulatory=args.skip_regulatory,
        )

    print(
        "CACE runtime bootstrap complete: "
        f"tariff_loaded={summary.tariff_lines_loaded} (skipped={summary.tariff_lines_skipped}), "
        f"regulatory_ingested={summary.regulatory_documents_ingested} "
        f"(skipped={summary.regulatory_documents_skipped}), "
        f"vectors_embedded={summary.vectors_embedded} (skipped={summary.vectors_skipped})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
