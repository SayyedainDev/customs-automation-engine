"""Unit tests for the document/regulatory evidence split.

These are pure-function tests: no LangGraph, no database, no retrieval. They
pin down exactly which checks get which kind of evidence, and that a check
missing evidence is reported honestly rather than papered over.
"""

from __future__ import annotations

from app.services.customs_audit.evidence import (
    document_evidence_for_check,
    evidence_status_for_regulatory,
    is_regulatory_check,
    normalize_regulatory_evidence,
)


def _field(value, *, page=1, method="pdf_text_llm_structured_output", conf="0.95"):
    return {"value": value, "source_page": page, "extraction_method": method, "confidence": conf}


EXTRACTION = {
    "invoice": {
        "invoice_total": _field("550.00"),
        "declared_net_weight_total": _field("75.00"),
        "line_items": [
            {
                "item_index": 1,
                "quantity": _field("100"),
                "net_weight": _field("75"),
                "gross_weight": _field("80"),
                "pct_code": _field("6109.1000"),
            }
        ],
    },
    "packing_list": {
        "declared_net_weight_total": _field("75.00"),
        "items": [
            {
                "item_index": 1,
                "quantity": _field("99", page=1),
            }
        ],
    },
    "items": [
        {"item_reference": "invoice_line_1", "invoice_item_index": 1, "packing_item_index": 1}
    ],
}


def test_a_regulatory_check_is_identified_by_source_document_or_sro():
    assert is_regulatory_check({"source_document": "TIPP"}) is True
    assert is_regulatory_check({"sro_number": "123(I)/2026"}) is True
    assert is_regulatory_check({"check_id": "item_quantity_match"}) is False


def test_b_item_level_check_pulls_values_from_both_documents():
    check = {"check_id": "item_quantity_match", "status": "failed"}
    evidence = document_evidence_for_check(
        check, EXTRACTION, item_reference="invoice_line_1"
    )
    by_doc = {item["document_type"]: item for item in evidence}
    assert by_doc["Commercial invoice"]["extracted_value"] == "100"
    assert by_doc["Packing list"]["extracted_value"] == "99"
    assert by_doc["Commercial invoice"]["field_name"] == "quantity"


def test_c_header_level_check_pulls_the_invoice_total():
    check = {"check_id": "sum_line_totals_match_invoice_total", "status": "passed"}
    evidence = document_evidence_for_check(check, EXTRACTION)
    assert len(evidence) == 1
    assert evidence[0]["extracted_value"] == "550.00"
    assert evidence[0]["document_type"] == "Commercial invoice"


def test_d_unmapped_check_falls_back_to_recorded_page_references():
    check = {
        "check_id": "some_future_check_id",
        "invoice_source_page": 2,
        "packing_list_source_page": 3,
    }
    evidence = document_evidence_for_check(check, EXTRACTION)
    pages = {item["document_type"]: item["page_number"] for item in evidence}
    assert pages == {"Commercial invoice": 2, "Packing list": 3}


def test_e_a_check_with_no_field_mapping_and_no_pages_yields_no_evidence():
    """Never invent a value: absence in, absence out."""
    assert document_evidence_for_check({"check_id": "unknown_check"}, EXTRACTION) == []


def test_f_empty_retrieval_result_is_unavailable_not_fabricated():
    assert evidence_status_for_regulatory([]) == "unavailable"


def test_g_conflicting_validation_status_is_uncertain():
    assert (
        evidence_status_for_regulatory(
            [{"validation_status": "conflicting", "evidence_text": "x"}]
        )
        == "uncertain"
    )


def test_h_normal_result_is_supported():
    assert (
        evidence_status_for_regulatory(
            [{"validation_status": "verified", "evidence_text": "x"}]
        )
        == "supported"
    )


def test_i_normalize_regulatory_evidence_shapes_citation_fields():
    raw = [
        {
            "source_document": "TIPP Customs Clearance Procedure",
            "source_document_id": "sha256:abcd",
            "sro_number": None,
            "page_number": 4,
            "section": "Export documentation",
            "validation_status": "verified",
            "evidence_text": "A Form-E declaration is required for every export shipment.",
            "retrieval_score": 0.81,
            "rerank_score": 0.93,
        }
    ]
    normalized = normalize_regulatory_evidence(raw)
    assert normalized == [
        {
            "source_title": "TIPP Customs Clearance Procedure",
            "source_document_id": "sha256:abcd",
            "sro_number": None,
            "page_number": 4,
            "section": "Export documentation",
            "snippet": "A Form-E declaration is required for every export shipment.",
            "retrieval_score": 0.81,
            "rerank_score": 0.93,
            "validation_status": "verified",
        }
    ]


def test_j_long_snippet_is_truncated_not_silently_dropped():
    long_text = "x" * 500
    normalized = normalize_regulatory_evidence([{"evidence_text": long_text}])
    assert len(normalized[0]["snippet"]) <= 320
    assert normalized[0]["snippet"].endswith("…")


# --------------------------------------------------------------------------- #
# Found live: pure arithmetic checks were routed through RAG and cited an
# unrelated regulatory passage, because any non-empty source_document counted
# as "this needs a government citation" - including the internal label
# arithmetic_checks.py uses to mark a check as "this is just math".
# --------------------------------------------------------------------------- #
def test_k_arithmetic_checks_are_never_treated_as_regulatory():
    for check_id in (
        "positive_quantity",
        "positive_unit_price",
        "invoice_line_calculation",
        "invoice_total_consistency",
    ):
        check = {
            "check_id": check_id,
            "status": "passed",
            "source_document": "Shipment invoice arithmetic",
        }
        assert is_regulatory_check(check) is False, check_id


def test_l_a_real_government_source_is_still_treated_as_regulatory():
    """The arithmetic-label exclusion must not swallow genuine citations."""
    check = {
        "check_id": "required_document_form_e",
        "status": "passed",
        "source_document": "TIPP Customs Clearance Procedure",
    }
    assert is_regulatory_check(check) is True


def test_m_item_line_calculation_shows_the_three_values_it_depends_on():
    """Found live: this check passed but its evidence read "Not extracted" -
    it depends on three fields at once (quantity, unit price, line total),
    and the field map only knew single-field comparisons."""
    extraction = {
        "invoice": {
            "line_items": [
                {
                    "item_index": 1,
                    "quantity": _field("100"),
                    "unit_price": _field("5.50"),
                    "line_total": _field("550.00"),
                }
            ]
        },
        "packing_list": {"items": []},
        "items": [
            {"item_reference": "invoice_line_1", "invoice_item_index": 1, "packing_item_index": None}
        ],
    }
    check = {"check_id": "item_line_calculation", "status": "passed"}
    evidence = document_evidence_for_check(check, extraction, item_reference="invoice_line_1")
    values = {item["field_name"]: item["extracted_value"] for item in evidence}
    assert values == {"quantity": "100", "unit_price": "5.50", "line_total": "550.00"}
