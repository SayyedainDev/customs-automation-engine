"""Mapping evidence for the deterministic textile compliance rule engine."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.schemas.compliance import (
    ComplianceCheckStatus,
    ShipmentComplianceInput,
)
from app.services.compliance.raw_cotton_checks import RAW_COTTON_PCT
from app.services.compliance.rule_engine import DeterministicComplianceRuleEngine
from app.services.compliance.rule_loader import load_compliance_rules

_ALL_SUPPORTED_PCT_CODES = sorted(load_compliance_rules().supported_codes)


def _valid_shipment(pct_code: str) -> ShipmentComplianceInput:
    uploaded = [
        "commercial_invoice",
        "packing_list",
        "form_e",
        "certificate_of_origin",
    ]
    if pct_code == RAW_COTTON_PCT:
        uploaded.extend(
            [
                "sbp_deposit_proof",
                "sbp_confirmation",
                "irrevocable_letter_of_credit",
                "import_permit",
                "phytosanitary_certificate",
            ]
        )
    return ShipmentComplianceInput(
        product_name="Pakistan Cotton Export Product",
        pct_code=pct_code,
        quantity=Decimal("100"),
        unit_price=Decimal("5.50"),
        invoice_line_total=Decimal("550.00"),
        invoice_total=Decimal("550.00"),
        net_weight=Decimal("75.00"),
        gross_weight=Decimal("80.00"),
        destination_country="China",
        shipment_date=date(2026, 7, 15),
        letter_of_credit_date=date(2026, 7, 1),
        uploaded_document_types=uploaded,
    )


@pytest.mark.parametrize("pct_code", _ALL_SUPPORTED_PCT_CODES)
def test_every_supported_pct_code_passes_with_valid_inputs(pct_code: str) -> None:
    rules = load_compliance_rules()
    engine = DeterministicComplianceRuleEngine(rules)
    response = engine.check(_valid_shipment(pct_code))

    req = rules.product_requirements[pct_code]
    has_product_locator = bool(
        req.source_locator or req.tipp_commodity_code or req.source_page is not None
    )
    assert response.supported_product is True
    if has_product_locator:
        assert response.overall_status is ComplianceCheckStatus.PASSED
        assert response.is_compliant is True
    else:
        assert response.overall_status is ComplianceCheckStatus.MANUAL_REVIEW
        assert response.is_compliant is False


@pytest.mark.parametrize("pct_code", _ALL_SUPPORTED_PCT_CODES)
def test_every_supported_pct_code_fails_on_weight_and_arithmetic_defects(
    pct_code: str,
) -> None:
    engine = DeterministicComplianceRuleEngine()
    invalid = _valid_shipment(pct_code).model_copy(
        update={
            "net_weight": Decimal("90.00"),
            "gross_weight": Decimal("80.00"),
            "invoice_line_total": Decimal("999.00"),
        }
    )
    response = engine.check(invalid)

    assert response.supported_product is True
    assert response.overall_status is ComplianceCheckStatus.FAILED
    assert response.is_compliant is False


def test_unsupported_pct_code_routes_to_manual_review() -> None:
    engine = DeterministicComplianceRuleEngine()
    unsupported = _valid_shipment(_ALL_SUPPORTED_PCT_CODES[0]).model_copy(
        update={"pct_code": "99999999"}
    )
    response = engine.check(unsupported)

    assert response.supported_product is False
    assert response.overall_status is ComplianceCheckStatus.MANUAL_REVIEW
    assert response.is_compliant is False
