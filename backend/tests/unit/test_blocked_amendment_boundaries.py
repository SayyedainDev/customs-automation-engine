"""Currentness boundaries for the seven non-executing EPO amendments."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from app.schemas.compliance import ComplianceCheckStatus, ShipmentComplianceInput
from app.services.compliance.pct_catalog import (
    raw_material_codes,
    supported_pct_codes,
    supported_pct_products,
)
from app.services.compliance.rule_engine import get_compliance_rule_engine
from app.services.compliance.rule_loader import PROJECT_ROOT


IMPACT_PATH = (
    PROJECT_ROOT
    / "regulatory_data/processed/commerce/export_policy/"
    "blocked_amendment_impact.json"
)
EXECUTABLE_RULES_PATH = (
    PROJECT_ROOT
    / "regulatory_data/processed/compliance/textile_mvp_executable_rules.json"
)


def _impact() -> dict:
    return json.loads(IMPACT_PATH.read_text(encoding="utf-8"))


def _shipment(code: str, destination: str = "China") -> ShipmentComplianceInput:
    return ShipmentComplianceInput(
        product_name=supported_pct_products()[code],
        pct_code=code,
        quantity=Decimal("100"),
        unit_price=Decimal("5.50"),
        invoice_line_total=Decimal("550"),
        invoice_total=Decimal("550"),
        net_weight=Decimal("75"),
        gross_weight=Decimal("80"),
        destination_country=destination,
        uploaded_document_types=[
            "commercial_invoice",
            "packing_list",
            "form_e",
            "certificate_of_origin",
        ],
    )


def test_inventory_is_exactly_the_seven_nonexecuting_amendments() -> None:
    data = _impact()
    identifiers = {row["identifier"] for row in data["amendments"]}
    assert identifiers == {
        "SRO 561(I)/2023",
        "SRO 1087(I)/2023",
        "SRO 629(I)/2024",
        "SRO 1021(I)/2024",
        "SRO 705(I)/2025",
        "SRO 1727(I)/2025",
        "SRO 1902(I)/2025",
    }
    assert "SRO 2486(I)/2025" not in identifiers
    assert data["legal_use_status"] == (
        "verification_only_not_executable_not_accepted_rag_evidence"
    )


def test_complete_pdfs_and_recorded_checksums_match() -> None:
    for row in _impact()["amendments"]:
        path = PROJECT_ROOT / row["file_path"]
        assert row["complete_source_pdf_available"] is True
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]


def test_blocked_amendments_cannot_overwrite_active_rules() -> None:
    active = json.loads(EXECUTABLE_RULES_PATH.read_text(encoding="utf-8"))
    active_sources = {
        (row.get("source_document") or "").casefold()
        for row in active["rules"]
    }
    for amendment in _impact()["amendments"]:
        assert amendment["identifier"].casefold() not in active_sources
    assert any("2486(i)/2025" in source for source in active_sources)


def test_broad_headings_schedules_and_product_classes_were_checked() -> None:
    searches = " ".join(_impact()["searches_performed"]).casefold()
    for required in (
        "six-digit",
        "four-digit",
        "chapters 50 through 63",
        "product names",
        "schedule",
        "appendix",
    ):
        assert required in searches
    broad = next(
        row
        for row in _impact()["amendments"]
        if row["identifier"] == "SRO 1902(I)/2025"
    )
    assert "respective headings" in broad["validation_status"]
    assert any(
        evidence["document"] == "SRO 544(I)/2022"
        for evidence in broad["evidence_pages"]
    )


def test_unresolved_values_are_preserved_and_never_guessed() -> None:
    transposition = next(
        row
        for row in _impact()["amendments"]
        if row["identifier"] == "SRO 629(I)/2024"
    )
    assert transposition["action"] == "unrelated to supported textile scope"
    page_two = next(item for item in transposition["unclear_pages"] if item["page"] == 2)
    assert page_two["printed_value"] == "2303.4910"
    assert page_two["conflicting_proposed_value"] == "2903.4910"
    assert "guessed" in transposition["action_note"]


def test_legal_cutoff_and_non_llm_method_are_visible() -> None:
    data = _impact()
    assert data["legal_cutoff_date"] == "2026-07-22"
    assert data["decision_method"] == (
        "deterministic_exact_and_normalized_text_search_without_llm"
    )


def test_no_new_manual_review_rule_is_justified() -> None:
    conclusion = _impact()["conclusion"]
    assert conclusion["confirmed_affected_configured_rules"] == []
    assert conclusion["possible_unresolved_affected_configured_rules"] == []
    assert conclusion["new_manual_review_rules_required"] is False


def test_unaffected_manufactured_code_keeps_original_behavior() -> None:
    result = get_compliance_rule_engine().check(_shipment("62034200"))
    checks = [*result.checks, *result.executable_rule_checks]
    assert not any("pending source validation" in check.message.casefold() for check in checks)
    assert not any(
        check.status is ComplianceCheckStatus.MANUAL_REVIEW
        and check.check_id.startswith("blocked_amendment")
        for check in checks
    )


def test_existing_afghanistan_yarn_boundary_remains_manual_review() -> None:
    result = get_compliance_rule_engine().check(
        _shipment("52052100", destination="Afghanistan")
    )
    check = next(
        item
        for item in result.executable_rule_checks
        if item.check_id == "xr_yarn_afghanistan_duty_drawback"
    )
    assert check.status is ComplianceCheckStatus.MANUAL_REVIEW
    assert "paragraph 7(3)" in (check.source_locator or "")


def test_raw_cotton_specific_rules_still_do_not_leak() -> None:
    engine = get_compliance_rule_engine()
    cotton_only = {
        "xr_52010090_sbp_deposit_proof",
        "xr_52010090_sbp_confirmation",
        "xr_52010090_irrevocable_letter_of_credit",
        "xr_52010090_shipment_within_180_days",
    }
    assert raw_material_codes() == ("52010090",)
    for code in supported_pct_codes():
        result = engine.check(_shipment(code))
        ids = {check.check_id for check in result.executable_rule_checks}
        if code == "52010090":
            assert cotton_only <= ids
        else:
            assert not (cotton_only & ids)
