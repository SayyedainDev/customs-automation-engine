"""Only a rule from a legal instrument is audited as one.

A clean four-document shipment was routed to human review reporting missing
regulatory provenance. Tracing it found the opposite of the expected cause:
every configured regulatory rule already carried a complete source locator and
effective date. The only flagged check was ``required_fields`` - CACE's own
input-completeness check - whose ``source_document`` is the string "Phase 1
shipment input schema", meaning CACE's own request schema.

Because the auditor treated any non-empty ``source_document`` as a legal
source, it demanded a statutory page number and effective date for CACE's own
schema. No official document can supply those, so the gap was permanent and
imaginary.

Nothing here lowers a provenance threshold: the test below asserts that every
real rule is still audited exactly as before, and that a real rule missing a
page or an effective date is still reported.
"""

from __future__ import annotations

from decimal import Decimal
from datetime import date
from typing import Any

from app.services.customs_audit.agents import (
    DeterministicAuditorAgent,
    _is_legal_check,
)
from app.services.customs_audit.state import AuditorReport, BrokerReport


def _audit(extraction_result: dict[str, Any], status: str) -> AuditorReport:
    """Run the real auditor over one check, with an empty broker report."""
    return DeterministicAuditorAgent().build_report(
        broker_report=BrokerReport(),
        extraction_result=extraction_result,
        deterministic_status=status,
        evidence_by_check={},
    )


def _check(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "check_id": "example",
        "status": "manual_review",
        "source_document": None,
        "sro_number": None,
        "issuing_authority": None,
        "source_page": None,
        "source_locator": None,
        "effective_date": None,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# What counts as a legal rule
# --------------------------------------------------------------------------- #
def test_cace_own_input_schema_is_not_a_legal_source() -> None:
    """"Phase 1 shipment input schema" is CACE's request schema.

    This is the exact check that caused the false provenance gap: it cites a
    source_document, so it qualified as a government rule, and was then
    reported as missing a page number and effective date that no Pakistani
    legal instrument will ever provide for it.
    """
    required_fields = _check(
        check_id="required_fields",
        source_document="Phase 1 shipment input schema",
        validation_status="manual_review_missing_extraction",
    )

    assert _is_legal_check(required_fields) is False


def test_a_rule_from_an_sro_is_a_legal_check() -> None:
    rule = _check(
        check_id="raw_cotton_sbp_deposit_proof",
        source_document="SRO 2486(I)/2025 — amendment to Export Policy Order, 2022",
        sro_number="2486(I)/2025",
        issuing_authority="Government of Pakistan, Ministry of Commerce",
        source_locator="page 1",
        source_page=1,
        effective_date="2025-12-23",
    )

    assert _is_legal_check(rule) is True


def test_a_rule_naming_an_issuing_authority_is_a_legal_check() -> None:
    """Not every instrument is an SRO - the phytosanitary rule is not."""
    rule = _check(
        check_id="raw_cotton_phytosanitary_certificate",
        source_document="Pakistan Plant Quarantine Rules, 2019",
        issuing_authority="Department of Plant Protection",
        source_locator="TIPP measure id 2, page 1",
        effective_date="2022-04-22",
    )

    assert _is_legal_check(rule) is True


# --------------------------------------------------------------------------- #
# The threshold itself is unchanged
# --------------------------------------------------------------------------- #
def test_a_legal_rule_missing_its_page_is_still_reported() -> None:
    """The gate must stay strict for rules it genuinely applies to."""
    extraction_result = {
        "shipment_level_checks": [
            _check(
                check_id="rule_without_page",
                status="failed",
                source_document="Export Policy Order, 2022",
                issuing_authority="Ministry of Commerce",
                source_page=None,
                source_locator=None,
                effective_date="2022-04-22",
            )
        ],
        "items": [],
    }
    report = _audit(extraction_result, "failed")

    assert "rule_without_page: no source page/locator" in report.missing_provenance


def test_a_legal_rule_missing_its_effective_date_is_still_reported() -> None:
    extraction_result = {
        "shipment_level_checks": [
            _check(
                check_id="rule_without_date",
                status="failed",
                source_document="Export Policy Order, 2022",
                issuing_authority="Ministry of Commerce",
                source_page=4,
                source_locator="page 4",
                effective_date=None,
            )
        ],
        "items": [],
    }
    report = _audit(extraction_result, "failed")

    assert (
        "rule_without_date: no verified effective date"
        in report.missing_provenance
    )


def test_an_internal_check_missing_everything_is_never_reported() -> None:
    """The whole point: it is not a legal rule, so it has no legal provenance."""
    extraction_result = {
        "shipment_level_checks": [
            _check(
                check_id="required_fields",
                status="manual_review",
                source_document="Phase 1 shipment input schema",
            )
        ],
        "items": [],
    }
    report = _audit(extraction_result, "manual_review")

    assert list(report.missing_provenance) == []


# --------------------------------------------------------------------------- #
# Nothing was fabricated to achieve this
# --------------------------------------------------------------------------- #
def test_no_effective_date_or_page_was_invented_for_the_internal_check() -> None:
    """The fix classifies the check correctly; it does not give it provenance.

    The alternative - inventing a page number and effective date for CACE's
    own schema so the gate would pass - would have been fabrication.
    """
    from app.services.compliance.general_checks import check_required_fields
    from app.schemas.compliance import ShipmentComplianceInput

    shipment = ShipmentComplianceInput(
        product_name="Raw cotton, other",
        pct_code="52010090",
        quantity=Decimal("1000"),
        unit_price=Decimal("2.00"),
        invoice_line_total=Decimal("2000.00"),
        invoice_total=Decimal("2000.00"),
        net_weight=Decimal("1000.00"),
        gross_weight=Decimal("1025.00"),
        destination_country="United Arab Emirates",
        shipment_date=None,
        uploaded_document_types=["commercial_invoice"],
    )
    result = check_required_fields(shipment, "52010090")

    assert result.status.value == "manual_review"
    assert result.source_page is None
    assert result.source_locator is None
    assert result.effective_date is None
    assert result.sro_number is None
    assert result.issuing_authority is None


def test_the_shipment_date_gap_still_requires_a_person() -> None:
    """The honest reason for manual review is unchanged and must stay."""
    from app.services.compliance.general_checks import check_required_fields
    from app.schemas.compliance import ShipmentComplianceInput

    shipment = ShipmentComplianceInput(
        product_name="Raw cotton, other",
        pct_code="52010090",
        quantity=Decimal("1000"),
        unit_price=Decimal("2.00"),
        invoice_line_total=Decimal("2000.00"),
        invoice_total=Decimal("2000.00"),
        net_weight=Decimal("1000.00"),
        gross_weight=Decimal("1025.00"),
        destination_country="United Arab Emirates",
        shipment_date=None,
        uploaded_document_types=["commercial_invoice"],
    )
    result = check_required_fields(shipment, "52010090")

    assert "shipment_date" in result.message
