"""A cold four-document review must not need the provider at all.

A first-time review of invoice, packing list, Form-E and COO was rate limited
by Groq's 8,000 tokens-per-minute tier. Two extraction requests reserved about
4,075 tokens each, so the pair exceeded the limit and the review failed; the
same documents succeeded on a second attempt only because the extraction cache
served them.

The cause was not the token ceiling and not the supporting documents. Every
compliance-important header field on both documents already resolved
deterministically. What failed was line-item table reconstruction: these
documents flatten their table to one cell per line, which carries no column
coordinates, so the hybrid extractor conceded the whole document to the single
full-document call it is allowed to fall back on - purely to read one line item
it could have parsed for nothing.

These tests pin that a standard cold review makes zero provider calls, and that
the difficult-document path stays bounded when one is genuinely needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.services.structured_extraction_service as extraction_service
from app.services import multi_line_shipment_service as mls
from app.services.extraction.document_bundle import DocumentTextBundle, StoredPage
from app.services.extraction.regex_extractor import (
    extract_document,
    reconstruct_line_items_from_text,
)


# The layout CACE's own synthetic documents produce: a header block naming the
# columns, then one line per cell. Kept verbatim - the shape is the point.
STANDARD_INVOICE = """SYNTHETIC TEST DOCUMENT
COMMERCIAL INVOICE
CACE TEST - Raw cotton, other - PCT 5201.0090
Exporter
Multan Raw Cotton Traders (Pvt.) Ltd.
Exporter address
Multan Industrial Estate, Punjab, Pakistan
Buyer / Consignee
Al Ain Fibre Trading LLC
Invoice number
MRC-INV-2026-101
Invoice date
2026-08-02
Destination country
United Arab Emirates
Currency
USD
Goods and values
Line
Product description
PCT code
Quantity
Unit
Unit price
Line total
1
Raw cotton, other
5201.0090
1000
KG
2.00
2000.00
Invoice total
USD 2000.00
Declared net weight
1000.00 KG
Declared gross weight
1025.00 KG
Packages
20 BALES
Country of origin
Pakistan
"""

STANDARD_PACKING = """SYNTHETIC TEST DOCUMENT
PACKING LIST
CACE TEST - Raw cotton, other - PCT 5201.0090
Exporter
Multan Raw Cotton Traders (Pvt.) Ltd.
Buyer / Consignee
Al Ain Fibre Trading LLC
Related invoice
MRC-INV-2026-101
Goods and packing
Line
Product description
PCT code
Quantity
Unit
1
Raw cotton, other
5201.0090
1000
KG
Declared net weight
1000.00 KG
Declared gross weight
1025.00 KG
Packages
20 BALES
"""


def _bundle(text: str, document_type: str) -> DocumentTextBundle:
    return DocumentTextBundle(
        document_id=uuid4(),
        document_type=document_type,
        pages=[
            StoredPage(
                page_number=1,
                text=text,
                original_embedded_text=text,
                extraction_method="pdf_embedded_text",
                ocr_confidence=None,
                ocr_validation_status=None,
            )
        ],
        reviews=[],
    )


@pytest.fixture(autouse=True)
def hybrid_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production runs hybrid; the suite pins legacy by default.

    These tests are about the hybrid extractor's fallback behaviour, so they
    opt in explicitly - the same way the other hybrid tests do.
    """
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "extraction_mode", "hybrid")


@pytest.fixture()
def forbid_provider(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Any provider call at all fails the test, loudly."""
    attempts: list[int] = []

    def _refuse() -> SimpleNamespace:
        attempts.append(1)
        raise AssertionError("the standard cold path must not call the provider")

    monkeypatch.setattr(extraction_service, "_get_groq_client", _refuse)
    return attempts


# --------------------------------------------------------------------------- #
# Standard cold review: zero provider calls
# --------------------------------------------------------------------------- #
def test_standard_invoice_extracts_with_no_provider_call(
    forbid_provider: list[int],
) -> None:
    candidates, telemetry = mls._extract_invoice_candidates(
        _bundle(STANDARD_INVOICE, "commercial_invoice")
    )

    assert forbid_provider == []
    assert telemetry is not None and telemetry.llm_calls == 0
    assert len(candidates.line_items) == 1
    # The fields compliance and matching actually consume.
    assert candidates.exporter_name.value == "Multan Raw Cotton Traders (Pvt.) Ltd"
    assert candidates.invoice_number.value == "MRC-INV-2026-101"
    assert candidates.currency.value == "USD"
    assert str(candidates.invoice_total.value) == "2000.00"


def test_standard_packing_list_extracts_with_no_provider_call(
    forbid_provider: list[int],
) -> None:
    candidates, telemetry = mls._extract_packing_candidates(
        _bundle(STANDARD_PACKING, "packing_list")
    )

    assert forbid_provider == []
    assert telemetry is not None and telemetry.llm_calls == 0
    assert str(candidates.declared_net_weight_total.value) == "1000.00"
    assert str(candidates.declared_gross_weight_total.value) == "1025.00"


def test_both_documents_together_cost_nothing(forbid_provider: list[int]) -> None:
    """The pair is what exceeded the tier, so the pair is what is asserted."""
    mls._extract_invoice_candidates(_bundle(STANDARD_INVOICE, "commercial_invoice"))
    mls._extract_packing_candidates(_bundle(STANDARD_PACKING, "packing_list"))

    assert forbid_provider == [], "a cold review must reserve zero provider tokens"


# --------------------------------------------------------------------------- #
# The parser change itself
# --------------------------------------------------------------------------- #
def test_a_table_flattened_to_one_cell_per_line_is_reconstructed() -> None:
    """Coordinate reconstruction cannot see this shape.

    Every cell has its own y, so row clustering yields one word per row and no
    row ever looks like a header.
    """
    items = reconstruct_line_items_from_text(STANDARD_INVOICE)

    assert len(items) == 1
    item = {name: field.value for name, field in items[0].items()}
    assert item["description"] == "Raw cotton, other"
    assert item["pct_code"] == "52010090"
    assert item["quantity"] == "1000"
    assert item["unit_price"] == "2.00"
    assert item["line_total"] == "2000.00"


def test_the_totals_block_below_the_table_is_not_read_as_line_items() -> None:
    """"Invoice total / Declared net weight / Packages" sits right beneath it.

    The coordinate path already had this bug once and fixed it with an
    end-marker vocabulary; the stacked path reuses that same vocabulary rather
    than inventing a second rule.
    """
    items = reconstruct_line_items_from_text(STANDARD_INVOICE)
    descriptions = [item["description"].value for item in items]

    assert descriptions == ["Raw cotton, other"]


def test_a_document_with_no_table_still_reconstructs_nothing() -> None:
    """The fallback must stay a fallback, not invent items from prose."""
    prose = (
        "CERTIFICATE OF ORIGIN\nExporter\nAcme Ltd\nCountry of origin\nPakistan\n"
        "Issuing authority\nTrade Development Authority of Pakistan\n"
    )
    assert reconstruct_line_items_from_text(prose) == []


def test_a_header_run_needs_three_mapped_columns_including_description() -> None:
    """Two stray vocabulary words in a row are not a table header."""
    near_miss = "Quantity\n1000\nUnit\nKG\nCurrency\nUSD\n"
    assert reconstruct_line_items_from_text(near_miss) == []


def test_multiple_line_items_are_all_read() -> None:
    """One row is the fixture; the parser must not be written for one row."""
    two_lines = """Goods
Line
Product description
PCT code
Quantity
Unit
Unit price
Line total
1
Cotton knitted T-shirts
6109.1000
100
PCS
5.50
550.00
2
Cotton bed sheets, mill-made
6302.3110
40
PCS
12.00
480.00
Invoice total
USD 1030.00
"""
    items = reconstruct_line_items_from_text(two_lines)

    assert [item["description"].value for item in items] == [
        "Cotton knitted T-shirts",
        "Cotton bed sheets, mill-made",
    ]
    assert [item["line_total"].value for item in items] == ["550.00", "480.00"]


def test_coordinate_reconstruction_still_wins_when_it_works() -> None:
    """The stacked reader is only consulted when coordinates found nothing."""
    extraction = extract_document(STANDARD_INVOICE, page_words=None)

    assert len(extraction.line_items) == 1
    assert extraction.line_items[0]["description"].method == "regex_stacked_table"


# --------------------------------------------------------------------------- #
# Difficult documents still reach the provider, still bounded
# --------------------------------------------------------------------------- #
def test_a_document_with_no_recognisable_table_still_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing here removes the existing fallback for genuinely hard layouts.

    A scanned document whose table cannot be reconstructed either way still
    gets its one full-document call - the behaviour that was already correct.
    """
    calls: list[dict] = []

    def _fake_single_shot(marked_text: str):
        calls.append({"text": marked_text})
        return mls._empty_invoice_candidates("stubbed")

    monkeypatch.setattr(mls, "_single_shot_invoice_call", _fake_single_shot)
    unreadable = "SCANNED IMAGE\nno labels, no table, nothing parseable here\n"

    mls._extract_invoice_candidates(_bundle(unreadable, "commercial_invoice"))

    assert len(calls) == 1, "exactly one full-document call, as before"


def test_optional_fields_never_trigger_a_provider_call(
    forbid_provider: list[int],
) -> None:
    """Ports, incoterm, NTN and addresses are absent from these documents.

    They are unresolved on every standard fixture and must stay outside
    gap-fill eligibility, or every review would pay for fields nothing reads.
    """
    from app.services.extraction.hybrid_orchestrator import (
        invoice_gapfill_fields,
        packing_gapfill_fields,
    )

    extraction = extract_document(STANDARD_INVOICE, page_words=None)
    unresolved = set(extraction.unresolved_fields())

    # These really are unresolved on the standard invoice...
    assert {"port_of_loading", "incoterm", "exporter_ntn"} <= unresolved
    # ...and none of them can reach the provider.
    assert unresolved.isdisjoint(invoice_gapfill_fields())
    assert unresolved.isdisjoint(packing_gapfill_fields())
    assert forbid_provider == []
