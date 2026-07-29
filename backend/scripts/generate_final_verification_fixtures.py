"""Generate the three narrow final-verification shipment fixture families.

The generator deliberately reuses the repository's invoice, packing-list and
supporting-document factories. Base fixtures are embedded-text PDFs committed
under ``synthetic_factory/final_verification``. Controlled test variants are
materialized in caller-provided temporary directories, avoiding redundant
committed PDFs.

Run:
    python -m scripts.generate_final_verification_fixtures
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.generate_supporting_documents import (
    SupportingDocSpec,
    certificate_of_origin,
    form_e,
    render_supporting_pdf,
)
from scripts.generate_synthetic_test_bundles import (
    OUTPUT_ROOT,
    LineItemSpec,
    ScenarioSpec,
    _money,
    render_commercial_invoice_pdf,
    render_packing_list_pdf,
)

FINAL_FIXTURE_ROOT = OUTPUT_ROOT / "final_verification"
VARIANTS = (
    "clean_pass",
    "missing_supporting_document",
    "invoice_packing_mismatch",
    "destination_condition",
    "uncertain_extraction",
)


@dataclass(frozen=True)
class FamilyDefinition:
    pct_code: str
    display_pct_code: str
    product_name: str
    category: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    net_weight: Decimal
    gross_weight: Decimal
    package_count: int
    invoice_number: str
    shipment_reference: str


FAMILIES = (
    FamilyDefinition(
        pct_code="62034200",
        display_pct_code="6203.4200",
        product_name="Men's woven cotton trousers",
        category="woven_garment",
        quantity=Decimal("1200"),
        unit="PCS",
        unit_price=Decimal("14.50"),
        net_weight=Decimal("720.00"),
        gross_weight=Decimal("780.00"),
        package_count=40,
        invoice_number="SYN-CACE-62034200-001",
        shipment_reference="SYN-SHP-62034200-001",
    ),
    FamilyDefinition(
        pct_code="61051000",
        display_pct_code="6105.1000",
        product_name="Men's knitted cotton shirts",
        category="knitted_garment",
        quantity=Decimal("1500"),
        unit="PCS",
        unit_price=Decimal("9.75"),
        net_weight=Decimal("600.00"),
        gross_weight=Decimal("660.00"),
        package_count=50,
        invoice_number="SYN-CACE-61051000-001",
        shipment_reference="SYN-SHP-61051000-001",
    ),
    FamilyDefinition(
        pct_code="63026010",
        display_pct_code="6302.6010",
        product_name="Cotton terry towels, mill-made",
        category="made_up",
        quantity=Decimal("2400"),
        unit="PCS",
        unit_price=Decimal("4.25"),
        net_weight=Decimal("960.00"),
        gross_weight=Decimal("1040.00"),
        package_count=80,
        invoice_number="SYN-CACE-63026010-001",
        shipment_reference="SYN-SHP-63026010-001",
    ),
)


def _family(pct_code: str) -> FamilyDefinition:
    for family in FAMILIES:
        if family.pct_code == pct_code:
            return family
    raise KeyError(f"Unknown final-verification PCT code: {pct_code}")


def build_scenario(pct_code: str, *, destination: str = "China") -> ScenarioSpec:
    family = _family(pct_code)
    return ScenarioSpec(
        key=f"final_verification_{family.pct_code}",
        title=f"Final verification: {family.product_name}",
        description="Complete synthetic embedded-text export shipment.",
        injected_defect=None,
        exporter_name="Synthetic Crescent Textiles (Pvt.) Ltd.",
        exporter_address="Plot SYN-17, Test Industrial Estate, Lahore, Pakistan",
        buyer_name="Synthetic Dragon Imports Co.",
        buyer_address="88 Demonstration Road, Shanghai, China",
        invoice_number=family.invoice_number,
        invoice_date=date(2026, 7, 15),
        destination_country=destination,
        origin_country="Pakistan",
        shipment_reference=family.shipment_reference,
        shipment_date=date(2026, 7, 20),
        currency="USD",
        packing_list_number=f"{family.invoice_number}-PL",
        include_full_shipment_summary=True,
        additional_uploaded_document_types=[
            "form_e_or_psw_export_declaration",
            "certificate_of_origin",
        ],
        items=[
            LineItemSpec(
                product_name=family.product_name,
                pct_code=family.display_pct_code,
                quantity=family.quantity,
                unit=family.unit,
                unit_price=family.unit_price,
                net_weight=family.net_weight,
                gross_weight=family.gross_weight,
                package_count=family.package_count,
            )
        ],
    )


def _summary_notes(scenario: ScenarioSpec) -> tuple[str, ...]:
    item = scenario.items[0]
    return (
        f"Exporter Address: {scenario.exporter_address}",
        f"Consignee Address: {scenario.buyer_address}",
        f"Country of Origin: {scenario.origin_country}",
        f"Shipment Reference: {scenario.shipment_reference}",
        f"Invoice Date: {scenario.invoice_date.isoformat()}",
        f"Currency: {scenario.currency}",
        f"Unit: {item.unit}",
        f"Unit Price: {scenario.currency} {_money(item.unit_price)}",
        f"Line Total: {scenario.currency} {_money(item.resolved_line_total())}",
        f"Invoice Total: {scenario.currency} {_money(scenario.invoice_total())}",
        f"Package Count: {item.package_count} CARTONS",
        f"Net Weight: {_money(item.net_weight)} KG",
        f"Gross Weight: {_money(item.gross_weight)} KG",
    )


def _supporting_specs(scenario: ScenarioSpec) -> tuple[SupportingDocSpec, SupportingDocSpec]:
    reference = scenario.shipment_reference
    notes = _summary_notes(scenario)
    return (
        SupportingDocSpec(
            claimed_type="form_e_or_psw_export_declaration",
            fields=form_e(scenario, related_reference=reference),
            subtitle="PSW EXPORT DECLARATION - SYNTHETIC",
            notes=notes,
        ),
        SupportingDocSpec(
            claimed_type="certificate_of_origin",
            fields=certificate_of_origin(scenario, related_reference=reference),
            subtitle="NON-PREFERENTIAL - SYNTHETIC",
            notes=notes,
        ),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _document_metadata(path: Path, document_type: str) -> dict[str, Any]:
    return {
        "filename": path.name,
        "document_type": document_type,
        "sha256": _sha256(path),
        "embedded_text": True,
        "synthetic": True,
    }


def _render_documents(
    scenario: ScenarioSpec,
    output_dir: Path,
    *,
    include_coo: bool = True,
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    invoice_path = output_dir / "commercial_invoice.pdf"
    packing_path = output_dir / "packing_list.pdf"
    form_e_path = output_dir / "form_e_psw_export_declaration.pdf"
    coo_path = output_dir / "certificate_of_origin.pdf"
    render_commercial_invoice_pdf(invoice_path, scenario)
    render_packing_list_pdf(packing_path, scenario)
    form_spec, coo_spec = _supporting_specs(scenario)
    render_supporting_pdf(form_e_path, form_spec)
    documents = [
        _document_metadata(invoice_path, "commercial_invoice"),
        _document_metadata(packing_path, "packing_list"),
        _document_metadata(form_e_path, "form_e_or_psw_export_declaration"),
    ]
    if include_coo:
        render_supporting_pdf(coo_path, coo_spec)
        documents.append(_document_metadata(coo_path, "certificate_of_origin"))
    return documents


def _scenario_metadata(
    scenario: ScenarioSpec,
    documents: list[dict[str, Any]],
    *,
    variant: str,
    expected: str,
    injected_defect: str | None,
) -> dict[str, Any]:
    family = _family(scenario.items[0].pct_code.replace(".", ""))
    item = scenario.items[0]
    return {
        "fixture_schema_version": "1.0",
        "family": family.pct_code,
        "category": family.category,
        "variant": variant,
        "expected_deterministic_status": expected,
        "injected_defect": injected_defect,
        "shipment": {
            "exporter": scenario.exporter_name,
            "exporter_address": scenario.exporter_address,
            "consignee": scenario.buyer_name,
            "consignee_address": scenario.buyer_address,
            "invoice_number": scenario.invoice_number,
            "invoice_date": scenario.invoice_date.isoformat(),
            "destination": scenario.destination_country,
            "currency": scenario.currency,
            "pct_code": family.pct_code,
            "display_pct_code": family.display_pct_code,
            "product_description": item.product_name,
            "quantity": str(item.quantity),
            "unit": item.unit,
            "unit_price": _money(item.unit_price),
            "line_total": _money(item.resolved_line_total()),
            "invoice_total": _money(scenario.invoice_total()),
            "package_count": item.package_count,
            "net_weight_kg": _money(item.net_weight),
            "gross_weight_kg": _money(item.gross_weight),
            "country_of_origin": scenario.origin_country,
            "shipment_reference": scenario.shipment_reference,
        },
        "documents": documents,
        "variants": {
            "clean_pass": "Use all four base PDFs.",
            "missing_supporting_document": "Omit certificate_of_origin.pdf for China.",
            "invoice_packing_mismatch": "Regenerate packing gross weight with +25 KG.",
            "destination_condition": "Regenerate for Germany and omit the conditional COO.",
            "uncertain_extraction": (
                "Regenerate invoice quantity as 120O/150O/240O (letter O); "
                "the extractor must preserve the evidence and require review."
            ),
        },
        "safety": {
            "real_personal_information": False,
            "real_customer_information": False,
            "valid_for_trade": False,
        },
    }


def materialize_variant(pct_code: str, variant: str, output_dir: Path) -> dict[str, Any]:
    if variant not in VARIANTS:
        raise ValueError(f"Unknown variant {variant!r}; expected one of {VARIANTS}")
    scenario = build_scenario(pct_code)
    include_coo = True
    # The documents themselves are clean. Current product-level "no licence /
    # no permit / conditional certificate / no approval" records lack complete
    # official provenance, so the executable evidence gate correctly retains a
    # manual-review outcome without changing those legal rules.
    expected = "manual_review"
    defect: str | None = None
    if variant == "missing_supporting_document":
        include_coo = False
        expected = "failed"
        defect = "Certificate of Origin omitted for China."
    elif variant == "invoice_packing_mismatch":
        scenario.items[0].packing_gross_weight = scenario.items[0].gross_weight + Decimal("25")
        expected = "failed"
        defect = "Packing-list gross weight exceeds invoice gross weight by 25 KG."
    elif variant == "destination_condition":
        scenario = build_scenario(pct_code, destination="Germany")
        scenario.buyer_name = "Synthetic Rhine Retail GmbH"
        scenario.buyer_address = "10 Demonstration Strasse, Hamburg, Germany"
        include_coo = False
        expected = "manual_review"
        defect = "COO omitted outside China; condition must not become a hard failure."
    elif variant == "uncertain_extraction":
        scenario.items[0].invoice_quantity_display = str(scenario.items[0].quantity).replace(
            "0", "O", 1
        )
        expected = "manual_review"
        defect = "Invoice quantity contains an ambiguous letter O; value must not be guessed."

    documents = _render_documents(scenario, output_dir, include_coo=include_coo)
    metadata = _scenario_metadata(
        scenario,
        documents,
        variant=variant,
        expected=expected,
        injected_defect=defect,
    )
    (output_dir / "fixture_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return metadata


def generate_base_families(root: Path = FINAL_FIXTURE_ROOT) -> list[Path]:
    generated: list[Path] = []
    for family in FAMILIES:
        folder = root / family.pct_code
        materialize_variant(family.pct_code, "clean_pass", folder)
        generated.append(folder)
    return generated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=FINAL_FIXTURE_ROOT)
    args = parser.parse_args()
    for folder in generate_base_families(args.output_root):
        print(f"Generated {folder}")


if __name__ == "__main__":
    main()
