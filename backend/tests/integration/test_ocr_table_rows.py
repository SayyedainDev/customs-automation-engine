"""Integration regression tests for DEF-002: OCR dropped invoice table rows.

Root cause: the configured Tesseract page-segmentation mode was PSM 6 ("assume
a single uniform block of text"). An invoice page is not a uniform block - it is
a header block, a wide sparse table, then a footer block. On rows whose columns
are separated by large horizontal gaps, PSM 6 discarded the entire product row,
so the model received a table header with no data beneath it and returned zero
line items even though OCR confidence was 0.94.

These tests require the real Tesseract binary and are skipped when it is not
installed, so the unit suite stays hermetic.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest

from app.core.config import get_settings
from app.services.extraction.ocr_extractor import ocr_pdf_page

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FACTORY_ROOT = PROJECT_ROOT / "synthetic_factory"
MANIFEST = FACTORY_ROOT / "scenario_manifest.json"


def _tesseract_path() -> str | None:
    configured = get_settings().ocr_executable
    resolved = shutil.which(configured)
    if resolved:
        return resolved
    backend_relative = Path(__file__).resolve().parents[2] / configured
    return str(backend_relative) if backend_relative.exists() else None


requires_tesseract = pytest.mark.skipif(
    _tesseract_path() is None, reason="Tesseract is not installed"
)
requires_fixtures = pytest.mark.skipif(
    not MANIFEST.exists(), reason="synthetic factory manifest not generated"
)


def _scanned_entries() -> list[dict]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [e for e in manifest["scenarios"] if e["variant"] == "scanned"]


@requires_tesseract
@requires_fixtures
def test_ocr_recovers_the_product_row_of_a_single_line_invoice() -> None:
    """The row that PSM 6 silently dropped must be present in the OCR text."""
    entry = next(
        e for e in _scanned_entries() if e["scenario_id"] == "clean_raw_cotton"
    )
    pdf = FACTORY_ROOT / entry["scenario_id"] / "synthetic_commercial_invoice_scanned.pdf"
    result = ocr_pdf_page(
        pdf_path=pdf, document_id=uuid4(), page_number=1, original_embedded_text=""
    )
    compact = result.ocr_text.replace(" ", "")
    item = entry["expected_items"][0]
    assert item["pct_code"].replace(" ", "") in compact, (
        "OCR lost the product row; PCT code missing from:\n" + result.ocr_text
    )
    assert item["quantity"].replace(" ", "") in compact
    assert item["line_total"].replace(" ", "") in compact


@requires_tesseract
@requires_fixtures
def test_ocr_recovers_every_row_of_a_multi_line_invoice() -> None:
    entry = next(
        e for e in _scanned_entries() if e["scenario_id"] == "multi_line_shipment"
    )
    pdf = FACTORY_ROOT / entry["scenario_id"] / "synthetic_commercial_invoice_scanned.pdf"
    result = ocr_pdf_page(
        pdf_path=pdf, document_id=uuid4(), page_number=1, original_embedded_text=""
    )
    compact = result.ocr_text.replace(" ", "")
    for item in entry["expected_items"]:
        assert item["pct_code"].replace(" ", "") in compact, (
            f"OCR lost row {item['line_number']} ({item['pct_code']})"
        )


@requires_tesseract
@requires_fixtures
def test_every_scanned_fixture_yields_its_expected_tokens() -> None:
    """Guards against tuning OCR for one layout at another layout's expense."""
    missing: list[str] = []
    for entry in _scanned_entries():
        pdf = (
            FACTORY_ROOT / entry["scenario_id"] / "synthetic_commercial_invoice_scanned.pdf"
        )
        result = ocr_pdf_page(
            pdf_path=pdf, document_id=uuid4(), page_number=1, original_embedded_text=""
        )
        compact = result.ocr_text.replace(" ", "")
        tokens = [entry["expected_invoice_number"]]
        for item in entry["expected_items"]:
            tokens += [item["pct_code"], item["quantity"], item["line_total"]]
        for token in tokens:
            if str(token).replace(" ", "") not in compact:
                missing.append(f"{entry['scenario_id']}:{token}")
    assert missing == [], f"OCR did not recover these expected tokens: {missing}"


@requires_tesseract
@requires_fixtures
def test_configured_segmentation_mode_beats_single_uniform_block() -> None:
    """Document the measured reason PSM 6 was replaced, not just the choice."""
    executable = _tesseract_path()
    assert executable is not None
    entry = next(
        e for e in _scanned_entries() if e["scenario_id"] == "clean_cotton_tshirts"
    )
    pdf = FACTORY_ROOT / entry["scenario_id"] / "synthetic_commercial_invoice_scanned.pdf"
    document = pymupdf.open(pdf)
    png = document[0].get_pixmap(dpi=get_settings().ocr_dpi).tobytes("png")
    document.close()

    def tokens_found(psm: int) -> int:
        completed = subprocess.run(
            [executable, "stdin", "stdout", "--psm", str(psm), "-l", "eng"],
            input=png,
            capture_output=True,
            timeout=120,
        )
        text = completed.stdout.decode("utf-8", "replace").replace(" ", "")
        item = entry["expected_items"][0]
        return sum(
            1
            for token in (item["pct_code"], item["quantity"], item["line_total"])
            if str(token).replace(" ", "") in text
        )

    configured = get_settings().ocr_page_segmentation_mode
    assert configured != 6, "PSM 6 drops sparse table rows on invoice layouts"
    assert tokens_found(configured) > tokens_found(6)
