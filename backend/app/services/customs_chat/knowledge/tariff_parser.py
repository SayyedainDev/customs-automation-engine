"""Pure text parser for one page of the Pakistan Customs Tariff PDF.

Deliberately takes already-extracted page text, not a PDF file, so it is
unit-testable with plain strings and has no dependency on PyMuPDF or a live
database. ``scripts/parse_tariff_pdf.py`` is the thin I/O wrapper that reads
the real PDF and calls this.

Layout this is built against (verified by inspecting the source PDF at
``regulatory_data/raw/fbr/pct/pakistan_customs_tariff_fy_2025_26.pdf``,
FY2025-26 edition): each page is a sequence of lines where an 8-digit code
like ``6203.4200`` starts a tariff line item, the following one or more
lines are its description, and the first subsequent line that is a bare
number (optionally with ``%``) is its customs duty rate - e.g.::

    6203.4200
    - - Of cotton
    20

A 4-digit heading like ``62.03`` (no line item of its own) introduces a
group of codes below it and is skipped rather than emitted as a row.

Known limitation, found by scanning the full 327-page document before
trusting this parser: on ~58 of 327 pages, at least one code's rate could
not be found within a few lines (a footnote reference, an unusual layout, or
similar). Rather than guess, those rows are still emitted with
``needs_review=True`` and ``customs_duty_pct=None`` - the mechanical
enforcement of the same "never invent a number" rule the rest of this
package applies to model output applies here too, to the data pipeline
itself. This has been spot-checked against a sample of pages, not
exhaustively audited against the published tariff; treat ``needs_review``
rows, and ideally a further sample of clean rows too, as needing a human
pass before this is relied on for a real duty-rate answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^\d{2}\.\d{2}[A-Za-z]?$")
_PCT_RE = re.compile(r"^(\d{4})\.(\d{4})$")
_RATE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*%?$")
#: How many lines ahead to look for a rate before giving up on this code.
_MAX_LOOKAHEAD = 24


@dataclass(frozen=True)
class ParsedTariffRow:
    hs_code: str
    description: str
    chapter: str
    heading: str
    customs_duty_pct: float | None
    needs_review: bool
    source_page: int | None = None


def _clean_description(parts: list[str]) -> str:
    text = " ".join(p for p in parts if p)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^-+\s*", "", text)
    return text


def parse_tariff_page(text: str, *, page_number: int | None = None) -> list[ParsedTariffRow]:
    """Every tariff line item found on one page of extracted text."""
    lines = [line.strip() for line in (text or "").split("\n")]
    rows: list[ParsedTariffRow] = []

    index = 0
    while index < len(lines):
        line = lines[index]
        match = _PCT_RE.match(line)
        if not match:
            index += 1
            continue

        hs_code = f"{match.group(1)}{match.group(2)}"
        chapter = match.group(1)[:2]
        heading = match.group(1)

        description_parts: list[str] = []
        rate: float | None = None
        needs_review = True
        lookahead = 0
        cursor = index + 1
        while cursor < len(lines) and lookahead < _MAX_LOOKAHEAD:
            candidate = lines[cursor].strip()
            if not candidate:
                cursor += 1
                continue
            lookahead += 1
            if _PCT_RE.match(candidate) or _HEADING_RE.match(candidate):
                # The next item started before a rate line was found.
                break
            rate_match = _RATE_RE.match(candidate)
            if rate_match:
                rate = float(rate_match.group(1))
                needs_review = False
                cursor += 1
                break
            description_parts.append(candidate)
            cursor += 1

        rows.append(
            ParsedTariffRow(
                hs_code=hs_code,
                description=_clean_description(description_parts),
                chapter=chapter,
                heading=heading,
                customs_duty_pct=rate,
                needs_review=needs_review,
                source_page=page_number,
            )
        )
        index = cursor if cursor > index else index + 1

    return rows


def parse_tariff_document(
    pages: list[str], *, first_page_number: int = 1
) -> list[ParsedTariffRow]:
    """``pages[i]`` is the extracted text of page ``first_page_number + i``."""
    rows: list[ParsedTariffRow] = []
    for offset, page_text in enumerate(pages):
        rows.extend(parse_tariff_page(page_text, page_number=first_page_number + offset))
    return rows
