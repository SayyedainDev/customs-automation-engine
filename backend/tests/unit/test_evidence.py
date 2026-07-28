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
    is_system_scope_check,
    normalize_regulatory_evidence,
    system_scope_statement,
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
    assert evidence_status_for_regulatory([]) == "evidence_unavailable"


def test_g_conflicting_validation_status_is_uncertain():
    assert (
        evidence_status_for_regulatory(
            [{"validation_status": "conflicting", "evidence_text": "x"}]
        )
        == "evidence_conflicting"
    )


def test_h_normal_result_is_supported():
    assert (
        evidence_status_for_regulatory(
            [{"validation_status": "verified", "evidence_text": "x"}]
        )
        == "evidence_verified"
    )


def test_h2_mixed_verified_and_partial_is_evidence_partial():
    assert (
        evidence_status_for_regulatory(
            [
                {"validation_status": "verified", "evidence_text": "x"},
                {"validation_status": "partially_verified", "evidence_text": "y"},
            ]
        )
        == "evidence_partial"
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
            "source_kind": "official",
            "issuing_authority": None,
            "sro_number": None,
            "page_number": 4,
            "section": "Export documentation",
            "snippet": "A Form-E declaration is required for every export shipment.",
            "retrieval_score": 0.81,
            "rerank_score": 0.93,
            "validation_status": "verified",
            "display_primary": True,
        }
    ]


def test_i2_normalize_regulatory_evidence_marks_curated_document_type():
    raw = [
        {
            "source_document": "PSW/TIPP textile product export requirements (curated)",
            "document_type": "product_requirements_structured",
            "issuing_authority": "CACE project (reviewed PSW/TIPP + Export Policy sources)",
            "page_number": 1,
            "validation_status": "partially_verified",
            "evidence_text": "Form-E is required for textile exports.",
        }
    ]
    normalized = normalize_regulatory_evidence(raw)
    assert normalized[0]["source_kind"] == "curated"
    assert normalized[0]["issuing_authority"] == (
        "CACE project (reviewed PSW/TIPP + Export Policy sources)"
    )


def test_i3_normalize_regulatory_evidence_dedupes_and_caps_primary_citations():
    raw = [
        {"source_document_id": "sha256:aaa", "page_number": 1, "evidence_text": "a"},
        {"source_document_id": "sha256:aaa", "page_number": 1, "evidence_text": "a-dup"},
        {"source_document_id": "sha256:bbb", "page_number": 2, "evidence_text": "b"},
        {"source_document_id": "sha256:ccc", "page_number": 3, "evidence_text": "c"},
    ]
    normalized = normalize_regulatory_evidence(raw)
    assert len(normalized) == 3
    assert [item["display_primary"] for item in normalized] == [True, True, False]


def test_i4_display_primary_is_a_display_rank_not_a_legal_source_claim():
    """A curated project record can be the strongest-ranked (display_primary)
    result without becoming a primary *legal* source - that authority
    question is answered only by source_kind, never by display rank."""
    raw = [
        {
            "source_document": "PSW/TIPP textile product export requirements (curated)",
            "document_type": "product_requirements_structured",
            "page_number": 1,
            "evidence_text": "Form-E is required for textile exports.",
        }
    ]
    normalized = normalize_regulatory_evidence(raw)
    assert normalized[0]["display_primary"] is True
    assert normalized[0]["source_kind"] == "curated"


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


# --------------------------------------------------------------------------- #
# Three-way check classification: every check is exactly one of Document
# Evidence, System-Scope, or Regulatory - never two, never neither. Internal
# arithmetic/schema/project-scope checks were previously routed into
# Regulatory Evidence because "any non-empty source_document" was the only
# rule; the classification below is now a strict, non-overlapping partition.
# --------------------------------------------------------------------------- #
def test_n_mvp_pct_support_is_system_scope_not_regulatory():
    check = {"check_id": "mvp_pct_support", "status": "manual_review", "source_document": "config"}
    assert is_system_scope_check(check) is True
    assert is_regulatory_check(check) is False


def test_o_system_scope_statement_names_the_pct_code_and_claims_nothing_legal():
    check = {"check_id": "mvp_pct_support", "pct_code": "61091000"}
    statement = system_scope_statement(check)
    assert statement == "PCT 61091000 is supported by this CACE prototype."
    lowered = statement.lower()
    for forbidden in ("cleared", "approved", "guarantee", "compliant", "authorized"):
        assert forbidden not in lowered


def test_p_system_scope_statement_has_a_generic_fallback_with_no_pct_code():
    assert system_scope_statement({"check_id": "mvp_pct_support"}) == (
        "This input is supported by this CACE prototype."
    )


def test_q_every_check_falls_into_exactly_one_of_the_three_categories():
    """No leakage: for a representative sample spanning all three kinds, the
    system-scope and regulatory predicates never agree with each other, and
    a check that is neither is exactly the document-comparison kind (it has
    a field mapping or falls back to recorded page references)."""
    samples = [
        {"check_id": "mvp_pct_support", "status": "manual_review", "source_document": "config", "pct_code": "40011000"},
        {"check_id": "required_document_form_e", "status": "passed", "source_document": "TIPP clearance"},
        {"check_id": "xr_coo_china", "status": "manual_review", "source_document": "TIPP CPFTA", "sro_number": None},
        {"check_id": "positive_quantity", "status": "passed", "source_document": "Shipment invoice arithmetic"},
        {"check_id": "invoice_line_calculation", "status": "passed", "source_document": "Shipment invoice arithmetic"},
        {"check_id": "item_quantity_match", "status": "failed"},
        {"check_id": "sum_line_totals_match_invoice_total", "status": "passed"},
    ]
    expected = {
        "mvp_pct_support": "system_scope",
        "required_document_form_e": "regulatory",
        "xr_coo_china": "regulatory",
        "positive_quantity": "document",
        "invoice_line_calculation": "document",
        "item_quantity_match": "document",
        "sum_line_totals_match_invoice_total": "document",
    }
    for check in samples:
        scope = is_system_scope_check(check)
        regulatory = is_regulatory_check(check)
        # Mutually exclusive by construction (is_regulatory_check always
        # checks is_system_scope_check first), but assert it explicitly so a
        # future edit to either function cannot silently reintroduce overlap.
        assert not (scope and regulatory), check["check_id"]
        if scope:
            actual = "system_scope"
        elif regulatory:
            actual = "regulatory"
        else:
            actual = "document"
        assert actual == expected[check["check_id"]], check["check_id"]
