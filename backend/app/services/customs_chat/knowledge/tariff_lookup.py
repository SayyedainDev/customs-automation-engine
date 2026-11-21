"""Exact/prefix HS-code lookup against ``tariff_lines`` - never RAG.

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 7.5. A duty-rate question is
answered by looking up a row, not by retrieving and summarising a passage:
table structure a chunk-and-retrieve pipeline destroys is exactly the
structure that carries the meaning here.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tariff import TariffLine
from app.services.compliance.duty_calculator import calculate_cascading_customs_taxes
from app.services.customs_chat.contracts import TariffLookupResult

_EIGHT_DIGIT = re.compile(r"^\d{8}$")
_PCT_SHAPED = re.compile(r"\b(\d{4})[.\s-]?(\d{4})\b")

#: How many candidates to surface when only a chapter/heading prefix (4 or 6
#: digits) is given - enough to let the user narrow down, not a data dump.
_MAX_PREFIX_CANDIDATES = 8


def extract_hs_code(text: str) -> str | None:
    """The first tariff-code-shaped number in free text, normalized to eight
    digits with no separator - or ``None`` if the text names no code."""
    match = _PCT_SHAPED.search(text or "")
    if not match:
        return None
    return f"{match.group(1)}{match.group(2)}"


def lookup_tariff(db: Session, code_or_prefix: str) -> TariffLookupResult:
    """Exact 8-digit match, else a 4/6-digit prefix search offering
    candidates, else no match. Never guesses a rate."""
    digits = re.sub(r"\D", "", code_or_prefix or "")

    if _EIGHT_DIGIT.match(digits):
        row = db.execute(select(TariffLine).where(TariffLine.hs_code == digits)).scalar_one_or_none()
        if row is None:
            return TariffLookupResult(query_code=digits, matched=False, exact=True)
        cascading_breakdown: dict[str, str] | None = None
        if not row.needs_review and row.customs_duty_pct is not None:
            cascading_breakdown = calculate_cascading_customs_taxes(
                customs_duty_pct=row.customs_duty_pct,
                additional_customs_duty_pct=row.additional_duty_pct,
                regulatory_duty_pct=row.regulatory_duty_pct,
                sales_tax_pct=row.sales_tax_pct,
                advance_income_tax_pct=row.income_tax_pct,
            ).to_dict()
        return TariffLookupResult(
            query_code=digits,
            matched=True,
            exact=True,
            hs_code=row.hs_code,
            description=row.description,
            customs_duty_pct=row.customs_duty_pct,
            chapter=row.chapter,
            source_page=row.source_page,
            tariff_year=row.tariff_year,
            needs_review=row.needs_review,
            cascading_tax_breakdown=cascading_breakdown,
        )

    if len(digits) in (4, 6):
        rows = (
            db.execute(
                select(TariffLine)
                .where(TariffLine.hs_code.like(f"{digits}%"))
                .order_by(TariffLine.hs_code)
                .limit(_MAX_PREFIX_CANDIDATES)
            )
            .scalars()
            .all()
        )
        if not rows:
            return TariffLookupResult(query_code=digits, matched=False, exact=False)
        return TariffLookupResult(
            query_code=digits,
            matched=True,
            exact=False,
            candidates=tuple(f"{r.hs_code} — {r.description}" for r in rows),
        )

    return TariffLookupResult(query_code=digits or code_or_prefix, matched=False, exact=False)


def describe_for_prompt(result: TariffLookupResult) -> str:
    """A short factual block for the answer generator's SOURCES section -
    never prose the model wrote, only the looked-up row rendered as text."""
    if not result.matched:
        return f"No tariff entry was found for {result.query_code}."
    if result.exact:
        if result.needs_review or result.customs_duty_pct is None:
            return (
                f"HS code {result.hs_code}: {result.description} "
                f"(Pakistan Customs Tariff {result.tariff_year}, page {result.source_page}). "
                "The duty rate for this exact line could not be confidently read from the "
                "source document and needs manual confirmation."
            )
        return (
            f"HS code {result.hs_code}: {result.description}. Customs duty: "
            f"{result.customs_duty_pct:g}% (Pakistan Customs Tariff {result.tariff_year}, "
            f"page {result.source_page})."
        )
    listed = "; ".join(result.candidates)
    return f"No exact 8-digit match for {result.query_code}. Related entries: {listed}."
