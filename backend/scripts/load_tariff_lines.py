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

DEFAULT_JSON = (
    BACKEND_ROOT.parent / "regulatory_data" / "processed" / "fbr" / "pct" / "tariff_lines_fy2025_26.json"
)


def load_rows(session: Session, rows: list[dict]) -> int:
    codes = [row["hs_code"] for row in rows]
    session.execute(delete(TariffLine).where(TariffLine.hs_code.in_(codes)))
    for row in rows:
        session.add(
            TariffLine(
                id=uuid4(),
                hs_code=row["hs_code"],
                description=row["description"],
                chapter=row.get("chapter"),
                heading=row.get("heading"),
                customs_duty_pct=row.get("customs_duty_pct"),
                unit=row.get("unit"),
                tariff_year=row["tariff_year"],
                source_page=row.get("source_page"),
                needs_review=bool(row.get("needs_review", False)),
            )
        )
    session.commit()
    return len(rows)


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
