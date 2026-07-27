"""Tests for the business-readable audit report and the single-line weight
fallback (Problems 1-4 of the customs-audit UX fix).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.schemas.compliance import (
    ComplianceCheckStatus,
    ShipmentComplianceInput,
)
from app.schemas.multi_line_extraction import (
    InvoiceLineItem,
    MultiLineCommercialInvoiceExtraction,
    PackingListItem,
)
from app.schemas.shipment_extraction import (
    ExtractedField,
    ExtractionMethod,
    FieldValidationStatus,
)
from app.services.compliance.rule_engine import DeterministicComplianceRuleEngine
from app.services.compliance.rule_loader import reload_compliance_rules
from app.services.customs_audit.report import build_audit_report
from app.services.multi_line.line_item_checks import per_item_checks
from app.services.multi_line_shipment_service import (
    _apply_single_line_declared_weight_fallback,
    _build_item_shipment_input,
)

_DOC = uuid4()


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _field(value, *, page: int = 1, conf: str = "0.95", note: str = "ok"):
    return ExtractedField(
        value=value,
        source_document_id=_DOC,
        source_page=page,
        extraction_method=ExtractionMethod.PDF_TEXT_LLM_STRUCTURED_OUTPUT,
        confidence=Decimal(conf),
        validation_status=FieldValidationStatus.VERIFIED,
        validation_note=note,
    )


def _missing_field():
    # A None value is coerced to manual_review / null by ExtractedField.
    return ExtractedField(
        value=None,
        source_document_id=_DOC,
        source_page=1,
        extraction_method=ExtractionMethod.NOT_EXTRACTED_OCR_REQUIRED,
        confidence=Decimal("0.0"),
        validation_status=FieldValidationStatus.MANUAL_REVIEW,
        validation_note="not printed on the product line",
    )


def _invoice_item(*, net, gross, index: int = 1, line: int = 1):
    return InvoiceLineItem(
        item_index=index,
        line_number=_field(line),
        product_name=_field("Cotton knitted T-shirts"),
        pct_code=_field("6109.1000"),
        quantity=_field(Decimal("100")),
        unit=_field("PCS"),
        unit_price=_field(Decimal("5.50")),
        line_total=_field(Decimal("550.00")),
        net_weight=_field(net) if net is not None else _missing_field(),
        gross_weight=_field(gross) if gross is not None else _missing_field(),
        item_source_page=1,
        item_confidence=Decimal("0.90"),
        item_validation_status=(
            FieldValidationStatus.VERIFIED
            if net is not None and gross is not None
            else FieldValidationStatus.MANUAL_REVIEW
        ),
        item_note="",
    )


def _invoice(items, *, net_total="75.00", gross_total="80.00"):
    return MultiLineCommercialInvoiceExtraction(
        exporter_name=_field("Lahore Cotton Garments (Pvt.) Ltd."),
        buyer_name=_field("Shanghai Sample Trading Co., Ltd."),
        invoice_number=_field("LCG-INV-2026-001"),
        invoice_date=_field("2026-07-20"),
        currency=_field("USD"),
        destination_country=_field("China"),
        invoice_total=_field(Decimal("550.00")),
        declared_net_weight_total=_field(Decimal(net_total)) if net_total else _missing_field(),
        declared_gross_weight_total=_field(Decimal(gross_total)) if gross_total else _missing_field(),
        line_items=items,
    )


def _packing_item(*, net="75.00", gross="80.00", index: int = 1):
    return PackingListItem(
        item_index=index,
        line_number=_field(1),
        product_name=_field("Cotton knitted T-shirts"),
        pct_code=_field("6109.1000"),
        quantity=_field(Decimal("100")),
        unit=_field("PCS"),
        net_weight=_field(Decimal(net)),
        gross_weight=_field(Decimal(gross)),
        package_count=_field(5),
        item_source_page=1,
        item_confidence=Decimal("0.9"),
        item_validation_status=FieldValidationStatus.VERIFIED,
        item_note="",
    )


def _tshirt_shipment(**overrides):
    base = dict(
        product_name="Cotton knitted T-shirts",
        pct_code="6109.1000",
        quantity=Decimal("100"),
        unit_price=Decimal("5.50"),
        invoice_line_total=Decimal("550.00"),
        invoice_total=Decimal("550.00"),
        net_weight=Decimal("75.00"),
        gross_weight=Decimal("80.00"),
        destination_country="China",
        shipment_date="2026-07-20",
        uploaded_document_types=[
            "commercial_invoice",
            "packing_list",
            "form_e",
            "certificate_of_origin",
        ],
    )
    base.update(overrides)
    return ShipmentComplianceInput.model_validate(base)


# --------------------------------------------------------------------------- #
# Problem 2 - single-line weight fallback
# --------------------------------------------------------------------------- #
def test_3_single_line_invoice_uses_document_net_weight():
    invoice = _invoice([_invoice_item(net=None, gross=None)])
    fixed = _apply_single_line_declared_weight_fallback(invoice)
    assert fixed.line_items[0].net_weight.value == Decimal("75.00")


def test_4_single_line_invoice_uses_document_gross_weight():
    invoice = _invoice([_invoice_item(net=None, gross=None)])
    fixed = _apply_single_line_declared_weight_fallback(invoice)
    assert fixed.line_items[0].gross_weight.value == Decimal("80.00")


def test_5_derived_weight_records_document_total_provenance():
    invoice = _invoice([_invoice_item(net=None, gross=None)])
    fixed = _apply_single_line_declared_weight_fallback(invoice)
    net = fixed.line_items[0].net_weight
    assert net.derivation_method == "single_line_declared_total"
    assert net.original_field_location == "invoice_header_or_total"
    assert net.validation_status == FieldValidationStatus.VERIFIED
    # The document-level total is preserved unchanged.
    assert fixed.declared_net_weight_total.value == Decimal("75.00")


def test_6_single_line_weights_match_packing_and_pass():
    invoice = _apply_single_line_declared_weight_fallback(
        _invoice([_invoice_item(net=None, gross=None)])
    )
    checks = per_item_checks(invoice.line_items[0], _packing_item())
    by_id = {c.check_id: c for c in checks}
    assert by_id["item_net_weight_match"].status == ComplianceCheckStatus.PASSED
    assert by_id["item_gross_weight_match"].status == ComplianceCheckStatus.PASSED

    from app.schemas.multi_line_extraction import MultiLineShipmentRequest

    request = MultiLineShipmentRequest(
        commercial_invoice_document_id=uuid4(),
        packing_list_document_id=uuid4(),
        shipment_date="2026-07-20",
        additional_uploaded_document_types=["form_e", "certificate_of_origin"],
    )
    shipment_input = _build_item_shipment_input(
        request=request,
        invoice=invoice,
        invoice_item=invoice.line_items[0],
        checks=checks,
    )
    assert shipment_input.net_weight == Decimal("75.00")
    assert shipment_input.gross_weight == Decimal("80.00")


def test_7_multi_line_invoice_never_assigns_total_weight():
    invoice = _invoice(
        [
            _invoice_item(net=None, gross=None, index=1, line=1),
            _invoice_item(net=None, gross=None, index=2, line=2),
        ]
    )
    fixed = _apply_single_line_declared_weight_fallback(invoice)
    assert fixed.line_items[0].net_weight.value is None
    assert fixed.line_items[1].net_weight.value is None
    assert fixed.line_items[0].gross_weight.value is None


# --------------------------------------------------------------------------- #
# Problem 3 - failure vs uncertainty
# --------------------------------------------------------------------------- #
def test_8_missing_field_produces_manual_review_not_failed():
    engine = DeterministicComplianceRuleEngine(reload_compliance_rules())
    response = engine.check(_tshirt_shipment(net_weight=None))
    required = next(c for c in response.checks if c.check_id == "required_fields")
    assert required.status == ComplianceCheckStatus.MANUAL_REVIEW
    assert response.overall_status != ComplianceCheckStatus.FAILED


def test_9_arithmetic_mismatch_still_fails():
    engine = DeterministicComplianceRuleEngine(reload_compliance_rules())
    response = engine.check(_tshirt_shipment(invoice_line_total=Decimal("500.00"), invoice_total=Decimal("500.00")))
    assert response.overall_status == ComplianceCheckStatus.FAILED


def test_10_missing_required_document_still_fails():
    engine = DeterministicComplianceRuleEngine(reload_compliance_rules())
    response = engine.check(
        _tshirt_shipment(
            uploaded_document_types=["commercial_invoice", "packing_list", "certificate_of_origin"]
        )
    )
    form_e = next(c for c in response.checks if c.check_id == "required_document_form_e")
    assert form_e.status == ComplianceCheckStatus.FAILED
    assert response.overall_status == ComplianceCheckStatus.FAILED


# --------------------------------------------------------------------------- #
# Problems 1 & 4 - report content
# --------------------------------------------------------------------------- #
def _extraction_state(*, overall="failed", form_e_present=True, quantity_mismatch=False):
    item_checks = []
    if quantity_mismatch:
        item_checks.append(
            {
                "check_id": "item_quantity_match",
                "check_name": "Quantity",
                "status": "failed",
                "message": "Quantity mismatch: invoice has '100' and packing list has '99'.",
                "invoice_source_page": 1,
                "packing_list_source_page": 1,
            }
        )
    compliance_checks = []
    if not form_e_present:
        compliance_checks.append(
            {
                "check_id": "required_document_form_e",
                "check_name": "Form-E is present",
                "status": "failed",
                "message": "Missing required document: Form-E.",
                "required_document": "form_e",
                "source_document": "TIPP Customs Clearance Procedure",
                "sro_number": None,
                "source_page": None,
                "issuing_authority": "Federal Board of Revenue",
                "validation_status": "partially_verified",
            }
        )
    extraction = {
        "overall_status": overall,
        "invoice": {
            "exporter_name": {"value": "Lahore Cotton Garments (Pvt.) Ltd."},
            "buyer_name": {"value": "Shanghai Sample Trading Co., Ltd."},
            "invoice_number": {"value": "LCG-INV-2026-001"},
            "invoice_date": {"value": "2026-07-20"},
            "currency": {"value": "USD"},
            "destination_country": {"value": "China"},
            "invoice_total": {"value": "550.00"},
            "declared_net_weight_total": {"value": "75.00"},
            "declared_gross_weight_total": {"value": "80.00"},
            "line_items": [
                {
                    "item_index": 1,
                    "line_number": {"value": 1},
                    "product_name": {"value": "Cotton knitted T-shirts"},
                    "pct_code": {"value": "6109.1000"},
                    "quantity": {"value": "100"},
                    "unit": {"value": "PCS"},
                    "unit_price": {"value": "5.50"},
                    "line_total": {"value": "550.00"},
                    "net_weight": {"value": "75.00"},
                    "gross_weight": {"value": "80.00"},
                    "item_source_page": 1,
                    "item_confidence": "0.95",
                }
            ],
        },
        "packing_list": {
            "items": [
                {"item_index": 1, "package_count": {"value": 5}, "item_source_page": 1}
            ]
        },
        "items": [
            {
                "item_reference": "invoice_line_1",
                "invoice_item_index": 1,
                "packing_item_index": 1,
                "match_strategy": "line_reference",
                "item_checks": item_checks,
                "compliance": {"checks": compliance_checks},
            }
        ],
        "shipment_level_checks": [],
    }
    return {
        "extraction_result": extraction,
        "deterministic_compliance_result": {"overall_status": overall},
        "broker_report": {"report_confidence": 0.71, "violations": []},
        "auditor_report": {"recommended_action": "continue", "violations": []},
        "consensus_result": {"requires_human_review": overall != "passed"},
    }


def test_1_report_shows_destination_from_extraction():
    report = build_audit_report(_extraction_state(overall="passed"))
    assert report["shipment_summary"]["destination"] == "China"
    assert report["shipment_summary"]["exporter"] == "Lahore Cotton Garments (Pvt.) Ltd."


def test_2_report_shows_line_items():
    report = build_audit_report(_extraction_state(overall="passed"))
    assert len(report["line_items"]) == 1
    line = report["line_items"][0]
    assert line["product_name"] == "Cotton knitted T-shirts"
    assert line["pct_code"] == "6109.1000"
    assert line["quantity"] == "100"
    assert line["match_method"] == "line reference"


def test_11_report_separates_missing_documents_from_mismatches():
    report = build_audit_report(
        _extraction_state(form_e_present=False, quantity_mismatch=True)
    )
    problems = report["problems"]
    assert problems["missing_documents"] == [
        (
            "The invoice and packing list were processed, but Form-E was not "
            "provided as a supporting document."
        )
    ]
    assert any("Quantity mismatch" in m for m in problems["document_mismatches"])
    assert problems["document_mismatches"] != problems["missing_documents"]


def test_12_report_gives_clear_required_actions():
    report = build_audit_report(
        _extraction_state(form_e_present=False, quantity_mismatch=True)
    )
    actions = report["required_actions"]
    assert (
        "Obtain Form-E from the body that issues it and file it with the "
        "shipment documents before customs submission."
    ) in actions
    assert any("quantity" in a.lower() for a in actions)
    assert report["overall_result"] == "FAILED"


def test_13_report_consolidates_duplicate_required_document_findings():
    state = _extraction_state(form_e_present=False)
    checks = state["extraction_result"]["items"][0]["compliance"]["checks"]
    checks.extend(
        [
            {
                "check_id": "xr_common_form_e",
                "check_name": "Form-E required for export clearance",
                "status": "failed",
                "message": "The required document 'form_e' is missing.",
                "required_document": "form_e",
                "source_document": "Export Policy Order 2022",
                "sro_number": "544(I)/2022",
                "source_page": 1,
                "issuing_authority": "Ministry of Commerce",
                "validation_status": "partially_verified",
            },
            {
                "check_id": "destination_certificate_of_origin",
                "check_name": "Destination-based certificate of origin",
                "status": "failed",
                "message": "Export to China requires a certificate of origin.",
                "required_document": "certificate_of_origin",
                "source_document": "TIPP Certificate of Origin Procedure",
                "sro_number": None,
                "source_page": None,
                "issuing_authority": "Trade Development Authority of Pakistan",
                "validation_status": "partially_verified",
            },
            {
                "check_id": "xr_coo_china",
                "check_name": "Certificate of origin for export to China under CPFTA",
                "status": "failed",
                "message": "The destination-specific document is missing.",
                "required_document": "certificate_of_origin",
                "source_document": "CPFTA Certificate of Origin Procedure",
                "sro_number": None,
                "source_page": None,
                "issuing_authority": "Trade Development Authority of Pakistan",
                "validation_status": "partially_verified",
            },
        ]
    )

    report = build_audit_report(state)

    assert report["problems"]["missing_documents"] == [
        (
            "The invoice and packing list were processed, but Form-E was not "
            "provided as a supporting document."
        ),
        (
            "The invoice and packing list were processed, but Certificate of "
            "origin was not provided as a supporting document."
        ),
    ]
    assert report["required_actions"] == [
        (
            "Obtain Form-E from the body that issues it and file it with the "
            "shipment documents before customs submission."
        ),
        (
            "Obtain Certificate of origin from the body that issues it and "
            "file it with the shipment documents before customs submission."
        ),
    ]
    # Consolidation affects the business summary only. Both the legacy and
    # executable legal sources remain available for audit/explanation.
    sources = {
        item["source_document"] for item in report["compliance_evidence"]
    }
    assert {
        "TIPP Customs Clearance Procedure",
        "Export Policy Order 2022",
        "TIPP Certificate of Origin Procedure",
        "CPFTA Certificate of Origin Procedure",
    } <= sources
