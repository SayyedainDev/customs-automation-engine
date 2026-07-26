"""Per-field regex coverage across the corpus, at zero token cost.

Answers "which patterns need work?" before any quota is spent. Two coverage
numbers are reported and the distinction matters:

* **schema coverage** - resolved / all schema fields. Always low on documents
  that legitimately do not print every field, so it is a poor tuning signal.
* **present-field coverage** - resolved / fields whose label actually appears
  in the document. This is the number to tune against: a gap here is a pattern
  that failed on text that was really there.

Run:
    python -m scripts.extraction_coverage_report
    python -m scripts.extraction_coverage_report --json
    python -m scripts.extraction_coverage_report --scanned
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.extraction.regex_extractor import (  # noqa: E402
    RegexExtraction,
    extract_document,
)
from app.services.extraction.telemetry import (  # noqa: E402
    BatchTelemetry,
    DocumentTelemetry,
)

FACTORY_ROOT = PROJECT_ROOT / "synthetic_factory"

#: Label probes used to decide whether a field is even printed on a document.
#: Deliberately looser than the extraction patterns: this asks "is the field
#: here at all", not "can we read it".
_PRESENCE_PROBES: dict[str, str] = {
    "invoice_number": r"invoice\s*(?:no|num|#)",
    "invoice_date": r"date",
    "exporter_name": r"exporter|shipper|seller|consignor",
    "exporter_address": r"exporter\s+address|shipper\s+address",
    "exporter_ntn": r"\bntn\b|national\s+tax",
    "consignee_name": r"consignee|buyer|importer",
    "consignee_address": r"consignee\s+address|buyer\s+address",
    "country_of_origin": r"country\s+of\s+origin|origin",
    "country_of_destination": r"destination",
    "port_of_loading": r"port\s+of\s+(?:loading|lading|shipment)",
    "port_of_discharge": r"port\s+of\s+(?:discharge|delivery)",
    "incoterm": r"incoterm|delivery\s+terms|\b(?:FOB|CIF|CFR|EXW|DDP)\b",
    "currency": r"currency|\b(?:USD|PKR|EUR)\b",
    "total_invoice_value": r"total|grand\s+total",
    "gross_weight": r"gross\s+w",
    "net_weight": r"net\s+w",
    "number_of_packages": r"packages|cartons|bales",
    "bl_awb_number": r"bill\s+of\s+lading|b/l|awb",
    "gd_number": r"goods\s+declaration|gd\s*no",
    "form_e_number": r"form\s*-?\s*e",
}


def _read_pdf(path: Path) -> tuple[str, list[list[tuple]]]:
    """Recover page text, falling back to OCR exactly as the app does.

    A rasterized document has no text layer, so ``get_text()`` returns nothing
    and every pattern trivially fails. Running the same Tesseract path the
    application uses is what makes the scanned-variant number meaningful rather
    than a measurement of "this PDF contains no text". OCR is local and free,
    so this costs no quota.
    """
    document = pymupdf.open(path)
    try:
        text = "\n".join(
            document[index].get_text() for index in range(document.page_count)
        )
        words = [
            list(document[index].get_text("words"))
            for index in range(document.page_count)
        ]
        page_count = document.page_count
    finally:
        document.close()

    if text.strip():
        return text, words

    from uuid import uuid5, NAMESPACE_URL

    from app.services.extraction.ocr_extractor import ocr_pdf_page

    document_id = uuid5(NAMESPACE_URL, str(path))
    recovered: list[str] = []
    for page_number in range(1, page_count + 1):
        try:
            result = ocr_pdf_page(
                pdf_path=path,
                document_id=document_id,
                page_number=page_number,
                original_embedded_text="",
            )
        except Exception:  # noqa: BLE001 - a failed OCR page is reported as empty
            continue
        recovered.append(result.ocr_text)
    # Word coordinates do not survive rasterization, so table reconstruction is
    # unavailable for scanned documents and line items fall to the LLM path.
    return "\n".join(recovered), []


def _field_is_present(field_name: str, text: str) -> bool:
    probe = _PRESENCE_PROBES.get(field_name)
    if probe is None:
        return True
    return re.search(probe, text, re.IGNORECASE) is not None


def analyse(path: Path) -> tuple[RegexExtraction, str, dict[str, Any]]:
    text, words = _read_pdf(path)
    extraction = extract_document(text, page_words=words)
    present = {
        name: _field_is_present(name, text) for name in extraction.fields
    }
    return extraction, text, present


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scanned",
        action="store_true",
        help="Analyse the rasterized variants instead of the text ones.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--pattern",
        default="synthetic_commercial_invoice_{variant}.pdf",
        help="Filename template within each scenario folder.",
    )
    args = parser.parse_args()

    variant = "scanned" if args.scanned else "text"
    filename = args.pattern.format(variant=variant)

    documents = sorted(
        path
        for path in FACTORY_ROOT.glob(f"*/{filename}")
        if path.is_file()
    )
    if not documents:
        print(f"No documents matched {filename} under {FACTORY_ROOT}")
        return 1

    batch = BatchTelemetry()
    resolved_counts: dict[str, int] = {}
    present_counts: dict[str, int] = {}
    field_names: list[str] = []
    per_document: list[dict[str, Any]] = []

    for path in documents:
        extraction, _text, present = analyse(path)
        if not field_names:
            field_names = list(extraction.fields)
            resolved_counts = dict.fromkeys(field_names, 0)
            present_counts = dict.fromkeys(field_names, 0)

        resolved_here = 0
        for name, result in extraction.fields.items():
            if present.get(name):
                present_counts[name] += 1
            if result.resolved:
                resolved_counts[name] += 1
                resolved_here += 1

        batch.add(
            DocumentTelemetry(
                document_ref=path.parent.name,
                fields_total=len(extraction.fields),
                fields_from_regex=resolved_here,
                fields_missing=len(extraction.fields) - resolved_here,
                line_items_from_table=len(extraction.line_items),
            )
        )
        per_document.append(
            {
                "scenario": path.parent.name,
                "variant": variant,
                "schema_coverage": round(extraction.coverage(), 4),
                "line_items": len(extraction.line_items),
                "unresolved": extraction.unresolved_fields(),
            }
        )

    total_present = sum(present_counts.values())
    total_resolved_present = sum(
        min(resolved_counts[name], present_counts[name]) for name in field_names
    )

    if args.json:
        print(
            json.dumps(
                {
                    "variant": variant,
                    "documents": per_document,
                    "per_field": {
                        name: {
                            "resolved": resolved_counts[name],
                            "present": present_counts[name],
                            "documents": len(documents),
                        }
                        for name in field_names
                    },
                    "totals": batch.to_json()["totals"],
                    "present_field_coverage": (
                        round(total_resolved_present / total_present, 4)
                        if total_present
                        else None
                    ),
                },
                indent=2,
            )
        )
        return 0

    print(f"Regex-only coverage report ({variant} variant, {len(documents)} documents)")
    print("=" * 72)
    print(f"{'field':28s} {'resolved':>9s} {'present':>8s}  status")
    print("-" * 72)
    for name in field_names:
        resolved = resolved_counts[name]
        present_count = present_counts[name]
        if present_count == 0:
            status = "not printed on any document"
        elif resolved >= present_count:
            status = "OK"
        else:
            status = f"** PATTERN GAP: {present_count - resolved} missed **"
        print(f"{name:28s} {resolved:9d} {present_count:8d}  {status}")
    print("-" * 72)
    print(batch.render())
    if total_present:
        print(
            f"Present-field coverage: {total_resolved_present}/{total_present} "
            f"({total_resolved_present / total_present * 100:.1f}%)"
        )
    print(
        "\nSchema coverage counts fields the documents may not print at all; "
        "tune against present-field coverage."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
