"""Load a ``scripts/parse_tariff_pdf.py`` JSON artifact into ``tariff_lines``.

Usage::

    python -m scripts.load_tariff_lines
    python -m scripts.load_tariff_lines --file /path/to/tariff_lines_fy2025_26.json

Upserts by ``hs_code`` (delete-then-insert per row), so re-running after a
corrected parse or a newer tariff year is safe. Requires ``CUSTOMS_DATABASE_URL``
- this is the step deliberately kept separate from parsing (see
``parse_tariff_pdf.py``), so a review pass can sit between them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import delete  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import get_engine  # noqa: E402
from app.models.tariff import TariffLine  # noqa: E402
from app.services.compliance.duty_calculator import statutory_tax_defaults_for_cd  # noqa: E402

DEFAULT_JSON = (
    BACKEND_ROOT.parent / "regulatory_data" / "processed" / "fbr" / "pct" / "tariff_lines_fy2025_26.json"
)


def load_rows(session: Session, rows: list[dict]) -> int:
    # Deduplicate by hs_code (preferring confirmed rows over needs_review rows)
    # so duplicate headings in parsed tariff artifacts never violate the UNIQUE
    # hs_code constraint.
    deduped: dict[str, dict] = {}
    for row in rows:
        code = row["hs_code"]
        existing = deduped.get(code)
        if existing is None or (
            bool(existing.get("needs_review", False))
            and not bool(row.get("needs_review", False))
        ):
            deduped[code] = row
    unique_rows = list(deduped.values())
    codes = list(deduped.keys())
    for start in range(0, len(codes), 500):
        batch = codes[start : start + 500]
        session.execute(delete(TariffLine).where(TariffLine.hs_code.in_(batch)))
    for row in unique_rows:
        needs_review = bool(row.get("needs_review", False))
        customs_duty_pct = row.get("customs_duty_pct")
        if not needs_review and customs_duty_pct is not None:
            defaults = statutory_tax_defaults_for_cd(customs_duty_pct)
            acd_pct = (
                row.get("additional_duty_pct")
                if row.get("additional_duty_pct") is not None
                else defaults["additional_duty_pct"]
            )
            rd_pct = (
                row.get("regulatory_duty_pct")
                if row.get("regulatory_duty_pct") is not None
                else defaults["regulatory_duty_pct"]
            )
            st_pct = (
                row.get("sales_tax_pct")
                if row.get("sales_tax_pct") is not None
                else defaults["sales_tax_pct"]
            )
            wht_pct = (
                row.get("income_tax_pct")
                if row.get("income_tax_pct") is not None
                else defaults["income_tax_pct"]
            )
        else:
            acd_pct = None
            rd_pct = None
            st_pct = None
            wht_pct = None

        session.add(
            TariffLine(
                id=uuid4(),
                hs_code=row["hs_code"],
                description=row["description"],
                chapter=row.get("chapter"),
                heading=row.get("heading"),
                customs_duty_pct=customs_duty_pct,
                additional_duty_pct=acd_pct,
                regulatory_duty_pct=rd_pct,
                sales_tax_pct=st_pct,
                income_tax_pct=wht_pct,
                unit=row.get("unit"),
                tariff_year=row["tariff_year"],
                source_page=row.get("source_page"),
                needs_review=needs_review,
            )
        )
    session.commit()
    return len(unique_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()

    if not args.file.is_file():
        print(f"JSON artifact not found: {args.file}", file=sys.stderr)
        print("Run scripts/parse_tariff_pdf.py first.", file=sys.stderr)
        return 1

    rows = json.loads(args.file.read_text(encoding="utf-8"))
    with Session(get_engine()) as session:
        count = load_rows(session, rows)
    print(f"Loaded {count} tariff lines from {args.file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
