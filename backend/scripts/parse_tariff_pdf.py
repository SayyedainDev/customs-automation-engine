"""Parse the Pakistan Customs Tariff PDF into structured JSON rows.

Usage::

    python -m scripts.parse_tariff_pdf
    python -m scripts.parse_tariff_pdf --pages 131-164   # textile chapters only
    python -m scripts.parse_tariff_pdf --out /tmp/tariff.json

Writes one JSON array of rows (see ``TariffParser.tariff_parser.ParsedTariffRow``)
to ``regulatory_data/processed/fbr/pct/tariff_lines_<year>.json`` by default.
Deliberately does not touch the database - ``scripts/load_tariff_lines.py``
loads the JSON artifact this produces, so a review pass can sit between
"parsed" and "queryable" rather than the parse and the load being one
unreviewable step. See ``knowledge/tariff_parser.py`` for the parsing
approach and its known limitations.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.customs_chat.knowledge.tariff_parser import parse_tariff_page  # noqa: E402

DEFAULT_PDF = (
    BACKEND_ROOT.parent / "regulatory_data" / "raw" / "fbr" / "pct" / "pakistan_customs_tariff_fy_2025_26.pdf"
)
DEFAULT_TARIFF_YEAR = "FY2025-26"


def _parse_page_range(spec: str | None, page_count: int) -> tuple[int, int]:
    if not spec:
        return 1, page_count
    start_str, _, end_str = spec.partition("-")
    start = max(1, int(start_str))
    end = min(page_count, int(end_str)) if end_str else start
    return start, end


def parse_pdf(pdf_path: Path, *, page_range: tuple[int, int] | None, tariff_year: str) -> list[dict]:
    import fitz  # PyMuPDF - imported lazily so --help works without it installed

    doc = fitz.open(str(pdf_path))
    start, end = page_range or (1, doc.page_count)
    rows: list[dict] = []
    for page_number in range(start, end + 1):
        text = doc[page_number - 1].get_text()
        for row in parse_tariff_page(text, page_number=page_number):
            payload = asdict(row)
            payload["tariff_year"] = tariff_year
            rows.append(payload)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--pages", type=str, default=None, help="e.g. 131-164; default: whole document")
    parser.add_argument("--tariff-year", type=str, default=DEFAULT_TARIFF_YEAR)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if not args.pdf.is_file():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 1

    import fitz

    doc = fitz.open(str(args.pdf))
    page_range = _parse_page_range(args.pages, doc.page_count)

    rows = parse_pdf(args.pdf, page_range=page_range, tariff_year=args.tariff_year)

    out_path = args.out or (
        BACKEND_ROOT.parent
        / "regulatory_data"
        / "processed"
        / "fbr"
        / "pct"
        / f"tariff_lines_{args.tariff_year.lower().replace('-', '_')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    needs_review = sum(1 for r in rows if r["needs_review"])
    print(f"Parsed {len(rows)} tariff lines from pages {page_range[0]}-{page_range[1]}.")
    print(f"  {needs_review} rows ({needs_review / len(rows):.1%}) need review - no rate confidently found.")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
