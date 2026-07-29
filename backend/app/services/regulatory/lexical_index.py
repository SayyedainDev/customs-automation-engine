"""Persistent lexical candidate search over the regulatory corpus.

The hybrid retriever used to build a BM25 index in Python on every request,
tokenizing every child chunk in the corpus each time. Measured on the live
corpus that was cheap in isolation (4 ms to build at 374 chunks), but it is
O(corpus) per question and it forces the whole corpus to be loaded into the
process before anything can be ranked. Once the Customs Act, the Customs Rules
and the textile tariff chapters are indexed, that is thousands of chunks per
question.

This module moves the lexical stage into PostgreSQL: a stored ``tsvector``
generated column with a GIN index (migration 013), ranked with ``ts_rank_cd``,
with metadata filters applied in the same statement so the candidate set is
narrowed *before* ranking rather than after.

The in-memory path is kept as a fallback, not as dead code: the test suite runs
on SQLite, which has no ``tsvector``. ``postgres_fts_available`` decides which
path a given session gets, so the same retriever works against both and the
behaviour difference is explicit rather than accidental.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Ranking normalization: 32 divides the rank by itself + 1, giving a bounded
#: 0-1 score, which is what the RRF stage downstream expects to compare.
_RANK_NORMALIZATION = 32

_TSQUERY_STRIP = re.compile(r"[^\w\s]")


@dataclass(frozen=True)
class LexicalHit:
    chunk_id: str
    rank: float


def postgres_fts_available(db: Session) -> bool:
    """Whether this session can serve the stored full-text index."""
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return False
    try:
        found = db.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'regulatory_chunks' "
                "AND column_name = 'search_vector'"
            )
        ).first()
        return found is not None
    except Exception:  # pragma: no cover - defensive; falls back to in-memory
        logger.warning("Could not probe for the regulatory full-text index.", exc_info=True)
        return False


def _to_tsquery_input(query: str) -> str:
    """Reduce a normalized query to whitespace-separated words.

    ``websearch_to_tsquery`` is used downstream, which treats the input as a
    web search box: bare words are OR-ed and quotes make phrases. Punctuation is
    stripped so a PCT code written as ``5205.2100`` does not become a phrase
    operator by accident.
    """
    cleaned = _TSQUERY_STRIP.sub(" ", query or "")
    return " ".join(cleaned.split())


def search_lexical_candidates(
    db: Session,
    *,
    query: str,
    limit: int,
    pct_code: str | None = None,
    sro_number: str | None = None,
    issuing_authority: str | None = None,
    document_type: str | None = None,
    validation_status: str | None = None,
    verified_statuses: tuple[str, ...] | None = None,
    legal_cutoff_date: date | None = None,
    exclude_superseded: bool = True,
) -> list[LexicalHit]:
    """Top-ranked child chunks for a query, filtered and ranked in PostgreSQL."""
    terms = _to_tsquery_input(query)
    if not terms:
        return []

    where = ["c.is_parent = false", "c.search_vector @@ q.query"]
    params: dict[str, object] = {"terms": terms, "limit": limit}

    if validation_status:
        where.append("c.validation_status = :validation_status")
        params["validation_status"] = validation_status
    elif verified_statuses:
        where.append("c.validation_status = ANY(:verified_statuses)")
        params["verified_statuses"] = list(verified_statuses)
    if pct_code:
        # pct_codes is JSON; the containment test keeps the filter in SQL.
        where.append("c.pct_codes::jsonb ? :pct_code")
        params["pct_code"] = pct_code
    if sro_number:
        where.append("lower(btrim(coalesce(c.sro_number, ''))) = lower(btrim(:sro_number))")
        params["sro_number"] = sro_number
    if issuing_authority:
        where.append("lower(coalesce(c.issuing_authority, '')) LIKE lower(:issuing_authority)")
        params["issuing_authority"] = f"%{issuing_authority}%"
    if document_type:
        where.append("c.document_type = :document_type")
        params["document_type"] = document_type
    if legal_cutoff_date:
        where.append("c.legal_cutoff_date IS NOT NULL AND c.legal_cutoff_date <= :cutoff")
        params["cutoff"] = legal_cutoff_date
    if exclude_superseded:
        # A superseded document must never be ranked as current law. Historical
        # sources stay searchable but are labelled, so they are not excluded
        # here - only outright superseded ones are.
        where.append("coalesce(c.currency_status, 'current') <> 'superseded'")

    statement = text(
        f"""
        SELECT c.chunk_id,
               ts_rank_cd(c.search_vector, q.query, {_RANK_NORMALIZATION}) AS rank
        FROM regulatory_chunks AS c,
             websearch_to_tsquery('english', :terms) AS q(query)
        WHERE {' AND '.join(where)}
        ORDER BY rank DESC, c.chunk_id
        LIMIT :limit
        """
    )
    rows = db.execute(statement, params).all()
    return [LexicalHit(chunk_id=row[0], rank=float(row[1])) for row in rows]
