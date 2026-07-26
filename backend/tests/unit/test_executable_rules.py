"""Tests for the Stage 2 executable-rule layer."""

from datetime import date
from decimal import Decimal

from app.schemas.compliance import ComplianceCheckStatus, ShipmentComplianceInput
from app.services.compliance.executable_rule_loader import load_executable_rules
from app.services.compliance.executable_rule_models import (
    ExecutableRuleSet,
    RuleValidationStatus,
)
from app.services.compliance.rule_engine import DeterministicComplianceRuleEngine


ALL_CODES = ["52010090", "52051100", "52094200", "61091000", "63023110"]


def _raw_cotton(**overrides) -> ShipmentComplianceInput:
    data = {
        "product_name": "Raw cotton, other",
        "pct_code": "52010090",
        "quantity": Decimal("1000"),
        "unit_price": Decimal("2.00"),
        "invoice_line_total": Decimal("2000.00"),
        "invoice_total": Decimal("2000.00"),
        "net_weight": Decimal("1000"),
        "gross_weight": Decimal("1025"),
        "destination_country": "United Arab Emirates",
        "shipment_date": date(2026, 6, 1),
        "letter_of_credit_date": date(2026, 1, 1),
        "uploaded_document_types": [
            "commercial_invoice",
            "packing_list",
            "form_e",
            "sbp_deposit_proof",
            "sbp_confirmation",
            "irrevocable_letter_of_credit",
            "phytosanitary_certificate",
            "import_permit",
        ],
    }
    data.update(overrides)
    return ShipmentComplianceInput.model_validate(data)


def _checks(response):
    return {check.check_id: check for check in response.executable_rule_checks}


def test_executable_rules_load_and_validate() -> None:
    rule_set = load_executable_rules()
    assert isinstance(rule_set, ExecutableRuleSet)
    assert len(rule_set.rules) >= 20
    # Every rule carries a validation status and note (provenance discipline).
    for rule in rule_set.rules:
        assert isinstance(rule.validation_status, RuleValidationStatus)
        assert rule.validation_note


def test_every_supported_product_has_executable_rules() -> None:
    rule_set = load_executable_rules()
    for code in ALL_CODES:
        assert rule_set.rules_for(code), f"no executable rules for {code}"


def test_raw_cotton_rules_carry_sro_and_page_provenance() -> None:
    response = DeterministicComplianceRuleEngine().check(_raw_cotton())
    checks = _checks(response)
    deposit = checks["xr_52010090_sbp_deposit_proof"]
    assert deposit.sro_number == "2486(I)/2025"
    assert deposit.source_page == 1
    assert deposit.source_url is not None


def test_documented_no_requirement_resolves_to_not_applicable() -> None:
    # approval_required is now recorded as an explicit "no requirement" under the
    # documented Export Policy Order 2022 general-permission policy, so it
    # resolves cleanly to not_applicable instead of forcing manual review.
    response = DeterministicComplianceRuleEngine().check(_raw_cotton())
    approval = _checks(response)["xr_52010090_approval_required"]
    assert approval.status == ComplianceCheckStatus.NOT_APPLICABLE


def test_shipment_deadline_fails_when_outside_window() -> None:
    late = _raw_cotton(
        letter_of_credit_date=date(2026, 1, 1),
        shipment_date=date(2026, 9, 1),  # ~243 days later
    )
    response = DeterministicComplianceRuleEngine().check(late)
    deadline = _checks(response)["xr_52010090_shipment_within_180_days"]
    assert deadline.status == ComplianceCheckStatus.FAILED
    assert "outside" in deadline.message


def test_shipment_deadline_manual_review_when_dates_missing() -> None:
    response = DeterministicComplianceRuleEngine().check(
        _raw_cotton(letter_of_credit_date=None)
    )
    deadline = _checks(response)["xr_52010090_shipment_within_180_days"]
    assert deadline.status == ComplianceCheckStatus.MANUAL_REVIEW


def test_china_certificate_of_origin_is_destination_scoped() -> None:
    engine = DeterministicComplianceRuleEngine()
    china = ShipmentComplianceInput.model_validate(
        {
            "product_name": "Cotton knitted T-shirts",
            "pct_code": "61091000",
            "quantity": Decimal("100"),
            "unit_price": Decimal("5.50"),
            "invoice_line_total": Decimal("550.00"),
            "invoice_total": Decimal("550.00"),
            "net_weight": Decimal("75"),
            "gross_weight": Decimal("80"),
            "destination_country": "Germany",
            "shipment_date": date(2026, 7, 20),
            "uploaded_document_types": ["commercial_invoice", "packing_list", "form_e"],
        }
    )
    coo = _checks(engine.check(china))["xr_coo_china"]
    # The China rule does not apply to a Germany shipment.
    assert coo.status == ComplianceCheckStatus.NOT_APPLICABLE


def test_complete_shipment_reaches_executable_pass() -> None:
    """With verified effective dates recorded, a complete, fully documented
    shipment reaches a genuine executable pass for every supported product."""
    engine = DeterministicComplianceRuleEngine()
    full_docs = [
        "commercial_invoice",
        "packing_list",
        "form_e",
        "certificate_of_origin",
        "sbp_deposit_proof",
        "sbp_confirmation",
        "irrevocable_letter_of_credit",
        "phytosanitary_certificate",
        "import_permit",
        "product_licence",
        "product_permit",
        "product_certificate",
        "product_approval",
    ]
    for code in ALL_CODES:
        shipment = ShipmentComplianceInput.model_validate(
            {
                "product_name": "X",
                "pct_code": code,
                "quantity": Decimal("100"),
                "unit_price": Decimal("5.50"),
                "invoice_line_total": Decimal("550.00"),
                "invoice_total": Decimal("550.00"),
                "net_weight": Decimal("75"),
                "gross_weight": Decimal("80"),
                "destination_country": "China",
                "shipment_date": date(2026, 6, 1),
                "letter_of_credit_date": date(2026, 1, 1),
                "uploaded_document_types": full_docs,
            }
        )
        response = engine.check(shipment)
        assert response.executable_overall_status == ComplianceCheckStatus.PASSED


def test_executable_layer_does_not_change_authoritative_status() -> None:
    """The executable layer is additive and never mutates the legacy result."""
    response = DeterministicComplianceRuleEngine().check(_raw_cotton())
    # The authoritative overall_status is still computed only from `checks`.
    legacy_ids = {check.check_id for check in response.checks}
    exec_ids = {check.check_id for check in response.executable_rule_checks}
    assert legacy_ids and exec_ids
    assert not (legacy_ids & exec_ids)  # namespaced separately (xr_*)
