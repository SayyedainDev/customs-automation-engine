"""Unit tests for the field -> check_id dependency registry.

Pure-function tests: no database, no LangGraph, no I/O. These pin down
exactly which checks a correction to a given field is expected to rerun -
the deterministic alternative to asking an LLM to guess.
"""

from __future__ import annotations

import pytest

from app.services.customs_audit.dependency_map import (
    InvalidFieldPathError,
    is_known_field_path,
    parse_field_path,
    resolve_affected_checks,
)


def test_a_parses_an_invoice_line_item_field_path():
    parsed = parse_field_path("invoice.line_items[1].quantity")
    assert parsed.document == "invoice"
    assert parsed.item_index == 1
    assert parsed.field == "quantity"


def test_b_parses_a_packing_list_item_field_path():
    parsed = parse_field_path("packing_list.items[2].quantity")
    assert parsed.document == "packing_list"
    assert parsed.item_index == 2
    assert parsed.field == "quantity"


def test_c_parses_a_header_level_field_path():
    parsed = parse_field_path("invoice.destination_country")
    assert parsed.document == "invoice"
    assert parsed.item_index is None
    assert parsed.field == "destination_country"


def test_d_rejects_a_field_path_outside_the_grammar():
    for bad in (
        "invoice.line_items[1].__class__",
        "invoice.line_items[1]",
        "not_a_document.quantity",
        "invoice.line_items[-1].quantity",
        "invoice; DROP TABLE workflows;",
        "",
    ):
        with pytest.raises(InvalidFieldPathError):
            parse_field_path(bad)
        assert is_known_field_path(bad) is False


def test_e_rejects_a_syntactically_valid_but_unmapped_field():
    with pytest.raises(KeyError):
        resolve_affected_checks("invoice.line_items[1].currency")
    assert is_known_field_path("invoice.line_items[1].currency") is False


# --- The exact dependency chains from the task's own examples ------------ #
def test_f_invoice_quantity_dependency_chain():
    result = resolve_affected_checks("invoice.line_items[1].quantity")
    assert set(result.direct_check_ids) == {
        "positive_quantity",
        "item_quantity_match",
        "item_line_calculation",
        "invoice_line_calculation",
        "sum_line_totals_match_invoice_total",
    }
    assert result.regulatory_family is False
    assert result.regulatory_context_changed is False


def test_g_invoice_unit_price_dependency_chain():
    result = resolve_affected_checks("invoice.line_items[1].unit_price")
    assert set(result.direct_check_ids) == {
        "positive_unit_price",
        "item_line_calculation",
        "invoice_line_calculation",
        "sum_line_totals_match_invoice_total",
    }


def test_h_packing_net_weight_dependency_chain():
    result = resolve_affected_checks("packing_list.items[1].net_weight")
    assert set(result.direct_check_ids) == {
        "item_net_weight_match",
        "packing_net_weight_total",
        "weight_consistency",
    }


def test_i_packing_gross_weight_dependency_chain():
    result = resolve_affected_checks("packing_list.items[1].gross_weight")
    assert set(result.direct_check_ids) == {
        "item_gross_weight_match",
        "packing_gross_weight_total",
        "weight_consistency",
    }


def test_j_pct_code_is_a_regulatory_family_field():
    result = resolve_affected_checks("invoice.line_items[1].pct_code")
    assert "item_pct_code_match" in result.direct_check_ids
    assert "mvp_pct_support" in result.direct_check_ids
    assert result.regulatory_family is True
    assert result.regulatory_context_changed is True


def test_k_destination_is_a_regulatory_family_field():
    result = resolve_affected_checks("invoice.destination_country")
    assert result.regulatory_family is True
    assert result.regulatory_context_changed is True


def test_l_pure_arithmetic_field_never_marks_regulatory_context_changed():
    for field_path in (
        "invoice.line_items[1].quantity",
        "invoice.line_items[1].unit_price",
        "invoice.line_items[1].line_total",
        "invoice.line_items[1].net_weight",
        "invoice.line_items[1].gross_weight",
        "packing_list.items[1].quantity",
        "packing_list.items[1].net_weight",
        "packing_list.items[1].gross_weight",
    ):
        result = resolve_affected_checks(field_path)
        assert result.regulatory_context_changed is False, field_path


# --- Regulatory-family expansion against a real shipment's own checks ---- #
def test_m_regulatory_family_expands_against_present_check_ids_only():
    result = resolve_affected_checks("invoice.line_items[1].pct_code")
    present = [
        "item_quantity_match",
        "xr_61091000_export_status",
        "xr_coo_china",
        "mvp_pct_support",
        "positive_quantity",
    ]
    resolved = result.resolve_check_ids(present)
    assert "xr_61091000_export_status" in resolved
    assert "xr_coo_china" in resolved
    assert "mvp_pct_support" in resolved
    # Never invents a check_id the shipment does not actually have.
    assert "xr_52010090_export_status" not in resolved
    assert "item_quantity_match" not in resolved


def test_n_non_regulatory_field_never_pulls_in_regulatory_checks():
    result = resolve_affected_checks("invoice.line_items[1].quantity")
    present = ["xr_61091000_export_status", "mvp_pct_support", "item_quantity_match"]
    resolved = result.resolve_check_ids(present)
    assert "xr_61091000_export_status" not in resolved
    assert "mvp_pct_support" not in resolved
    assert "item_quantity_match" in resolved


def test_o_resolve_check_ids_deduplicates_and_preserves_order():
    result = resolve_affected_checks("invoice.line_items[1].quantity")
    resolved = result.resolve_check_ids([])
    assert resolved == list(dict.fromkeys(resolved))
    assert resolved[0] == "positive_quantity"
