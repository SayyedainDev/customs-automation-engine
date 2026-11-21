"""Scenario evidence for zero-token hybrid invoice and packing extraction."""

from __future__ import annotations

from decimal import Decimal

from app.schemas.multi_line_extraction import (
    MultiLineInvoiceCandidates,
    MultiLinePackingListCandidates,
)
from app.schemas.shipment_extraction import FieldValidationStatus
from app.services.extraction.hybrid_orchestrator import (
    REGEX_LABELED_CONFIDENCE,
    extract_invoice_hybrid,
    extract_packing_hybrid,
)


def test_invoice_hybrid_resolves_labeled_fields_with_provenance() -> None:
    invoice_text = "\n".join(
        [
            "COMMERCIAL INVOICE",
            "Exporter",
            "Lahore Cotton Garments (Pvt.) Ltd.",
            "Buyer / Consignee",
            "Shanghai Sample Trading Co., Ltd.",
            "Invoice Number",
            "LCG-INV-2026-002",
            "Invoice Date",
            "2026-06-25",
            "Destination Country",
            "China",
            "Currency",
            "USD",
            "Invoice Total",
            "USD 550.00",
            "Declared Net Weight Total",
            "75.00 KG",
            "Declared Gross Weight Total",
            "80.00 KG",
        ]
    )

    candidates, telemetry = extract_invoice_hybrid(invoice_text)
    assert isinstance(candidates, MultiLineInvoiceCandidates)
    assert telemetry.fields_from_regex > 0
    assert candidates.exporter_name.value == "Lahore Cotton Garments (Pvt.) Ltd."
    assert candidates.exporter_name.confidence == REGEX_LABELED_CONFIDENCE
    assert candidates.exporter_name.validation_status == FieldValidationStatus.VERIFIED
    assert candidates.buyer_name.value == "Shanghai Sample Trading Co., Ltd."
    assert candidates.invoice_number.value == "LCG-INV-2026-002"
    assert candidates.destination_country.value == "China"
    assert candidates.currency.value == "USD"
    assert candidates.invoice_total.value == Decimal("550.00")


def test_packing_hybrid_handles_resolved_and_missing_fields_safely() -> None:
    packing_text = "\n".join(
        [
            "PACKING LIST",
            "Declared Net Weight Total",
            "120.00 KG",
            "Declared Gross Weight Total",
            "132.00 KG",
            "Total Packages",
            "15 CTNS",
        ]
    )

    candidates, telemetry = extract_packing_hybrid(packing_text)
    assert isinstance(candidates, MultiLinePackingListCandidates)
    assert telemetry.fields_from_regex > 0
    assert candidates.declared_net_weight_total.value == Decimal("120.00")
    assert candidates.declared_gross_weight_total.value == Decimal("132.00")
    assert candidates.declared_package_count_total.value == 15

    sparse_candidates, sparse_telemetry = extract_packing_hybrid(
        "Unstructured scanned packing note without labels"
    )
    assert sparse_telemetry.fields_from_regex == 0
    assert sparse_candidates.declared_net_weight_total.value is None
    assert (
        sparse_candidates.declared_net_weight_total.validation_status
        == FieldValidationStatus.MANUAL_REVIEW
    )
