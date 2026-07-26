"""Tests for the hybrid regex-first extraction layer.

The emphasis is on the failure mode that matters: a *wrong* value is far worse
than a missing one, because a missing field escalates to the LLM and a wrong
one silently corrupts a compliance verdict. Several tests below pin bugs that
were found by running the extractor against the real fixture corpus.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.schemas.multi_line_extraction import (
    MultiLineInvoiceCandidates,
    MultiLinePackingListCandidates,
)
from app.services.extraction import llm_gapfill
from app.services.extraction.hybrid_orchestrator import (
    extract_invoice_hybrid,
    extract_packing_hybrid,
    to_invoice_candidates,
    to_packing_candidates,
)
from app.services.extraction.patterns import (
    normalise_count,
    normalise_identifier,
    normalise_money,
    normalise_pct_code,
    normalise_text,
    parse_date,
)
from app.services.extraction.regex_extractor import (
    extract_document,
    extract_field,
    reconstruct_line_items,
)
from app.services.extraction.telemetry import BatchTelemetry, DocumentTelemetry

# A two-column invoice as PyMuPDF extracts it: label line, then value line.
TEXT_LAYER_INVOICE = """COMMERCIAL INVOICE
Exporter
Lahore Cotton Garments (Pvt.) Ltd.
Buyer / Consignee
Shanghai Sample Trading Co., Ltd.
Invoice Number
LCG-INV-2026-002
Invoice Date
2026-06-25
Destination Country
China
Currency
USD
Invoice Total
USD 550.00
Declared Net Weight Total
75.00 KG
Declared Gross Weight Total
80.00 KG
"""

# The same document as Tesseract emits it: the two columns collapse onto one
# line with only whitespace between label and value.
OCR_SINGLE_LINE_INVOICE = """COMMERCIAL INVOICE
Exporter Lahore Cotton Garments (Pvt.) Ltd.
Buyer / Consignee Shanghai Sample Trading Co., Ltd.
Invoice Number LCG-INV-2026-002
Invoice Date 2026-06-25
Destination Country China
Currency USD
Invoice Total USD 550.00
Declared Net Weight Total 75.00 KG
Declared Gross Weight Total 80.00 KG
"""


# --------------------------------------------------------------------------- #
# The false-value regressions found against the real corpus
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("document", [TEXT_LAYER_INVOICE, OCR_SINGLE_LINE_INVOICE])
def test_compound_label_is_never_captured_as_its_own_value(document: str) -> None:
    """A label prefix must not match inside a compound label.

    Found live: an unanchored pattern matched "Buyer" inside "Buyer /
    Consignee" and captured the remainder of the *label* as the consignee name,
    producing ``consignee_name = "/ Consignee"``. Confidently wrong.
    """
    result = extract_field("consignee_name", normalise_text(document))
    assert result.value == "Shanghai Sample Trading Co., Ltd"
    assert "consignee" not in (result.value or "").lower()


@pytest.mark.parametrize("document", [TEXT_LAYER_INVOICE, OCR_SINGLE_LINE_INVOICE])
def test_label_word_is_never_captured_as_a_country(document: str) -> None:
    """Found live: "Destination Country" yielded ``country = "Country"``."""
    result = extract_field("country_of_destination", normalise_text(document))
    assert result.value == "China"


def test_atomic_label_prevents_backtracking_into_a_partial_label() -> None:
    """The specific mechanism behind both false values above.

    Without an atomic group the engine consumes the whole compound label, fails
    the same-line form at the newline, then backtracks to the shorter label and
    captures the label's own remainder. Both layouts must stay clean.
    """
    for document in (TEXT_LAYER_INVOICE, OCR_SINGLE_LINE_INVOICE):
        result = extract_field("consignee_name", normalise_text(document))
        assert result.confidence == "high", result.candidates
        assert result.candidates == ["Shanghai Sample Trading Co., Ltd"]


def test_prose_word_is_not_accepted_as_an_invoice_number() -> None:
    """Found live: "COMMERCIAL INVOICE\\nExporter" gave invoice_number="Exporter".

    Patterns are compiled IGNORECASE, so ``[A-Z0-9]`` also matches lowercase and
    an ordinary word satisfied the identifier shape. A real reference always
    contains a digit.
    """
    result = extract_field("invoice_number", normalise_text(TEXT_LAYER_INVOICE))
    assert result.value == "LCG-INV-2026-002"


# --------------------------------------------------------------------------- #
# Both document layouts extract identically
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("document", [TEXT_LAYER_INVOICE, OCR_SINGLE_LINE_INVOICE])
def test_text_layer_and_ocr_layouts_agree(document: str) -> None:
    extraction = extract_document(document)
    resolved = {
        name: result.value
        for name, result in extraction.fields.items()
        if result.resolved
    }
    assert resolved["exporter_name"] == "Lahore Cotton Garments (Pvt.) Ltd"
    assert resolved["consignee_name"] == "Shanghai Sample Trading Co., Ltd"
    assert resolved["invoice_number"] == "LCG-INV-2026-002"
    assert resolved["invoice_date"] == "2026-06-25"
    assert resolved["country_of_destination"] == "China"
    assert resolved["currency"] == "USD"
    assert resolved["total_invoice_value"] == "550.00"
    assert resolved["net_weight"] == "75.00"
    assert resolved["gross_weight"] == "80.00"


def test_absent_fields_are_missing_not_invented() -> None:
    extraction = extract_document(TEXT_LAYER_INVOICE)
    for name in ("exporter_ntn", "bl_awb_number", "gd_number", "form_e_number"):
        result = extraction.fields[name]
        assert result.value is None
        assert result.confidence == "missing"


# --------------------------------------------------------------------------- #
# Dates: never guess DD/MM vs MM/DD
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-06-25", "2026-06-25"),
        ("25/06/2026", "2026-06-25"),   # day > 12 -> unambiguously DD/MM
        ("25-06-2026", "2026-06-25"),
        ("25.06.2026", "2026-06-25"),
        ("14 Mar 2026", "2026-03-14"),  # spelled month is never ambiguous
        ("14-Mar-2026", "2026-03-14"),
        ("Mar 14, 2026", "2026-03-14"),
    ],
)
def test_unambiguous_dates_normalise_to_iso(raw: str, expected: str) -> None:
    parsed = parse_date(raw)
    assert parsed.value is not None
    assert parsed.value.isoformat() == expected
    assert parsed.ambiguous is False


@pytest.mark.parametrize("raw", ["05/03/2026", "01/02/2026", "12/11/2026"])
def test_ambiguous_dates_refuse_to_pick_an_interpretation(raw: str) -> None:
    """Assuming DD/MM could shift a shipment date by months.

    That directly changes 180-day SRO deadline arithmetic, so an ambiguous date
    escalates to the LLM instead of being silently resolved.
    """
    parsed = parse_date(raw)
    assert parsed.ambiguous is True
    assert parsed.value is None
    assert "DD/MM" in parsed.reason and "MM/DD" in parsed.reason


def test_ambiguous_date_downgrades_the_field_rather_than_guessing() -> None:
    document = "Invoice Date\n05/03/2026\n"
    result = extract_field("invoice_date", normalise_text(document))
    assert result.value is None
    assert result.confidence == "low"


# --------------------------------------------------------------------------- #
# OCR tolerance: match, but never silently repair
# --------------------------------------------------------------------------- #
def test_ocr_confusable_digits_escalate_instead_of_being_corrected() -> None:
    """An NTN read as containing 'O' must not be rewritten to '0'.

    Rewriting would invent a legal identifier the page does not show. The value
    is reported as needing confirmation instead.
    """
    document = "NTN\n123456O-7\n"
    result = extract_field("exporter_ntn", normalise_text(document))
    assert result.value is None
    assert result.confidence == "low"
    assert "confuses" in result.reason


def test_clean_ntn_is_accepted() -> None:
    result = extract_field("exporter_ntn", normalise_text("NTN\n1234567-8\n"))
    assert result.value == "1234567-8"
    assert result.confidence == "high"


@pytest.mark.parametrize(
    "noisy",
    [
        "Invoice   Number\nLCG-INV-2026-002\n",   # doubled whitespace
        "Invoice Number:\nLCG-INV-2026-002\n",    # extra separator
        "Invoice Number​\nLCG-INV-2026-002\n",  # zero-width character
    ],
)
def test_patterns_tolerate_ocr_whitespace_and_invisible_characters(noisy: str) -> None:
    result = extract_field("invoice_number", normalise_text(noisy))
    assert result.value == "LCG-INV-2026-002"


# --------------------------------------------------------------------------- #
# Normalisers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("550.00", "550.00"),
        ("USD 550.00", "550.00"),      # currency prefix as printed
        ("Rs. 1,250.50", "1250.50"),
        ("75.00 KG", "75.00"),          # weight unit as printed
        ("1,025.00 KGS", "1025.00"),
    ],
)
def test_money_normaliser_strips_affixes_without_altering_digits(
    raw: str, expected: str
) -> None:
    assert normalise_money(raw) == expected


@pytest.mark.parametrize("raw", ["not a number", "", "12.345", "USD"])
def test_money_normaliser_rejects_non_amounts(raw: str) -> None:
    assert normalise_money(raw) is None


def test_count_normaliser_handles_a_count_printed_with_its_noun() -> None:
    assert normalise_count("5 CARTONS") == "5"
    assert normalise_count("1,200 bales") == "1200"
    assert normalise_count("no digits") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("5208.52.00", "52085200"), ("5208.5200", "52085200"), ("6109.10.00", "61091000")],
)
def test_pct_normaliser_preserves_all_digits(raw: str, expected: str) -> None:
    assert normalise_pct_code(raw) == expected


def test_identifier_requires_a_digit() -> None:
    assert normalise_identifier("LCG-INV-2026-002") == "LCG-INV-2026-002"
    assert normalise_identifier("Exporter") is None


# --------------------------------------------------------------------------- #
# Line-item table reconstruction (free, no LLM)
# --------------------------------------------------------------------------- #
def _word(x0: float, x1: float, y: float, text: str) -> tuple:
    return (x0, y, x1, y + 8.0, text, 0, 0, 0)


def _invoice_table_words() -> list[list[tuple]]:
    header = [
        _word(50, 63, 100, "Line"),
        _word(74, 102, 100, "Product"),
        _word(105, 139, 100, "Description"),
        _word(216, 240, 100, "PCT"),
        _word(242, 251, 100, "Code"),
        _word(285, 313, 100, "Quantity"),
        _word(316, 329, 100, "Unit"),
        _word(364, 380, 100, "Unit"),
        _word(382, 397, 100, "Price"),
        _word(422, 437, 100, "Line"),
        _word(440, 455, 100, "Total"),
        _word(467, 500, 100, "Net"),
        _word(502, 512, 100, "Wt"),
        _word(530, 560, 100, "Gross"),
        _word(562, 566, 100, "Wt"),
    ]
    row = [
        _word(50, 56, 120, "1"),
        _word(74, 139, 120, "Cotton knitted T-shirts"),
        _word(216, 251, 120, "6109.1000"),
        _word(285, 313, 120, "100"),
        _word(316, 329, 120, "PCS"),
        _word(364, 397, 120, "5.50"),
        _word(422, 455, 120, "550.00"),
        _word(467, 512, 120, "75.00"),
        _word(530, 566, 120, "80.00"),
    ]
    return [header + row]


def test_line_items_reconstruct_from_coordinates_without_an_llm() -> None:
    items = reconstruct_line_items(_invoice_table_words())
    assert len(items) == 1
    item = items[0]
    assert item["description"].value == "Cotton knitted T-shirts"
    assert item["pct_code"].value == "61091000"
    assert item["quantity"].value == "100"
    assert item["unit"].value == "PCS"
    assert item["unit_price"].value == "5.50"
    assert item["line_total"].value == "550.00"


def test_unmodelled_columns_do_not_leak_into_modelled_ones() -> None:
    """Found live: Net/Gross Wt values were reassigned into "Line Total".

    Every value under an unmodelled column was previously pushed to whichever
    modelled column was nearest - the rightmost - overwriting a financial
    figure with a weight.
    """
    items = reconstruct_line_items(_invoice_table_words())
    assert items[0]["line_total"].value == "550.00"
    assert "75.00" not in str(items[0]["line_total"].value)
    assert "80.00" not in str(items[0]["line_total"].value)


def test_wide_cell_is_not_split_into_the_previous_column() -> None:
    """Found live: the first word of "Raw cotton, other" fell into "Line".

    A heading is often narrower than the data beneath it, so nearest-centre
    assignment stole the leading word. Boundaries are taken in the header gaps.
    """
    words = _invoice_table_words()
    words[0] = [
        w if w[4] != "Cotton knitted T-shirts" else _word(74, 139, 120, "Raw cotton, other")
        for w in words[0]
    ]
    items = reconstruct_line_items(words)
    assert items[0]["description"].value == "Raw cotton, other"


def test_a_document_with_no_table_yields_no_invented_items() -> None:
    assert reconstruct_line_items([[_word(50, 100, 10, "Just prose text here")]]) == []


# --------------------------------------------------------------------------- #
# Gap-fill: context is bounded, and model output is re-validated
# --------------------------------------------------------------------------- #
def test_gapfill_context_is_capped_and_never_the_whole_document() -> None:
    extraction = extract_document("Invoice Number\nINV-1\n" + ("filler text " * 5000))
    unresolved = extraction.unresolved_fields()
    context, _choices = llm_gapfill.build_context(extraction, unresolved)
    assert len(context) <= llm_gapfill.MAX_CONTEXT_CHARACTERS
    assert len(context) < len(extraction.normalised_text)


def test_fields_with_candidates_cost_no_document_text() -> None:
    """The cheapest gap-fill path: send three strings, not a page."""
    extraction = extract_document(TEXT_LAYER_INVOICE)
    extraction.fields["incoterm"].candidates = ["FOB", "CIF"]
    extraction.fields["incoterm"].confidence = "low"
    _context, choices = llm_gapfill.build_context(extraction, ["incoterm"])
    assert choices["incoterm"] == ["FOB", "CIF"]


def test_system_prompt_is_static_so_groq_prefix_caching_applies() -> None:
    assert "{" not in llm_gapfill.GAPFILL_SYSTEM_PROMPT
    assert llm_gapfill.GAPFILL_SYSTEM_PROMPT is llm_gapfill.GAPFILL_SYSTEM_PROMPT


@pytest.mark.parametrize(
    ("field_name", "returned"),
    [
        ("total_invoice_value", "not a number"),
        ("invoice_number", "Exporter"),   # no digit
        ("pct_code", "12"),               # wrong length
        ("total_invoice_value", None),
        ("currency", "unknown"),
    ],
)
def test_unusable_model_values_are_discarded_not_passed_through(
    field_name: str, returned: Any
) -> None:
    assert llm_gapfill.validate_returned_value(field_name, returned) is None


def test_valid_model_values_are_accepted() -> None:
    assert llm_gapfill.validate_returned_value("total_invoice_value", "550.00") == "550.00"
    assert llm_gapfill.validate_returned_value("currency", "USD") == "USD"


def test_discarded_gapfill_leaves_the_field_missing_rather_than_wrong() -> None:
    extraction = extract_document(TEXT_LAYER_INVOICE)
    updated = llm_gapfill.apply_gapfill_response(
        extraction, ["exporter_ntn"], {"exporter_ntn": "not-an-ntn"}
    )
    assert updated["exporter_ntn"].value is None
    assert updated["exporter_ntn"].confidence == "missing"


# --------------------------------------------------------------------------- #
# Drop-in schema conformance
# --------------------------------------------------------------------------- #
def test_orchestrator_output_validates_against_the_existing_schema() -> None:
    """The compliance engine downstream must see no difference in shape."""
    extraction = extract_document(TEXT_LAYER_INVOICE, page_words=_invoice_table_words())
    candidates = to_invoice_candidates(extraction)
    assert isinstance(candidates, MultiLineInvoiceCandidates)
    assert candidates.invoice_number.value == "LCG-INV-2026-002"
    assert candidates.exporter_name.value == "Lahore Cotton Garments (Pvt.) Ltd"
    assert candidates.buyer_name.value == "Shanghai Sample Trading Co., Ltd"
    assert candidates.invoice_total.value == Decimal("550.00")
    assert len(candidates.line_items) == 1
    assert candidates.line_items[0].pct_code.value == "61091000"


def test_unresolved_fields_are_rendered_as_manual_review_not_fabricated() -> None:
    candidates = to_invoice_candidates(extract_document("nothing useful here"))
    assert candidates.invoice_number.value is None
    assert candidates.invoice_number.confidence == Decimal("0")
    assert candidates.invoice_number.validation_status.value == "manual_review"


def test_hybrid_entrypoint_reports_zero_llm_calls() -> None:
    _candidates, telemetry = extract_invoice_hybrid(
        TEXT_LAYER_INVOICE, page_words=_invoice_table_words(), document_ref="fixture"
    )
    assert telemetry.llm_calls == 0
    assert telemetry.total_tokens == 0
    assert telemetry.line_items_from_table == 1
    assert telemetry.fields_from_regex >= 9


# --------------------------------------------------------------------------- #
# Packing-list mapper: structural mirror of the invoice mapper above.
# --------------------------------------------------------------------------- #
TEXT_LAYER_PACKING_LIST = """PACKING LIST
Declared Net Weight Total
75.00 KG
Declared Gross Weight Total
80.00 KG
"""


def _packing_table_words() -> list[list[tuple]]:
    header = [
        _word(50, 63, 100, "Line"),
        _word(74, 102, 100, "Product"),
        _word(105, 139, 100, "Description"),
        _word(216, 240, 100, "PCT"),
        _word(242, 251, 100, "Code"),
        _word(285, 313, 100, "Quantity"),
        _word(316, 329, 100, "Unit"),
    ]
    row = [
        _word(50, 56, 120, "1"),
        _word(74, 139, 120, "Cotton knitted T-shirts"),
        _word(216, 251, 120, "6109.1000"),
        _word(285, 313, 120, "100"),
        _word(316, 329, 120, "PCS"),
    ]
    return [header + row]


def test_packing_orchestrator_output_validates_against_the_existing_schema() -> None:
    """The compliance engine downstream must see no difference in shape."""
    extraction = extract_document(
        TEXT_LAYER_PACKING_LIST, page_words=_packing_table_words()
    )
    candidates = to_packing_candidates(extraction)
    assert isinstance(candidates, MultiLinePackingListCandidates)
    assert candidates.declared_net_weight_total.value == Decimal("75.00")
    assert candidates.declared_gross_weight_total.value == Decimal("80.00")
    assert len(candidates.items) == 1
    assert candidates.items[0].pct_code.value == "61091000"
    assert candidates.items[0].product_name.value == "Cotton knitted T-shirts"
    # No reconstructable column for per-item weights/package count: honest
    # "not found", never a guess.
    assert candidates.items[0].net_weight.value is None
    assert candidates.items[0].package_count.value is None


def test_packing_hybrid_entrypoint_reports_zero_llm_calls() -> None:
    _candidates, telemetry = extract_packing_hybrid(
        TEXT_LAYER_PACKING_LIST,
        page_words=_packing_table_words(),
        document_ref="fixture",
    )
    assert telemetry.llm_calls == 0
    assert telemetry.total_tokens == 0
    assert telemetry.line_items_from_table == 1
    assert telemetry.fields_from_regex >= 2


def test_a_packing_list_with_no_table_yields_no_invented_items() -> None:
    extraction = extract_document("nothing useful here")
    candidates = to_packing_candidates(extraction)
    assert candidates.items == []
    assert candidates.declared_net_weight_total.value is None


# --------------------------------------------------------------------------- #
# Telemetry
# --------------------------------------------------------------------------- #
def test_more_than_one_gapfill_call_is_flagged_as_a_regression() -> None:
    """Any second call per document means the retry ladder has returned."""
    entry = DocumentTelemetry(document_ref="d", llm_calls=2)
    assert entry.exceeded_call_budget is True
    batch = BatchTelemetry()
    batch.add(entry)
    assert batch.budget_exceeded_documents() == ["d"]
    assert "WARNING" in batch.render()


def test_single_gapfill_call_is_within_budget() -> None:
    assert DocumentTelemetry(document_ref="d", llm_calls=1).exceeded_call_budget is False
