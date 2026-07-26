"""Generate a comprehensive synthetic customs test-document factory.

Follows the pattern of ``create_live_demo_documents.py`` (PyMuPDF-generated
PDFs) and the shape of the existing bundle at
``synthetic_factory/synthetic_customs_test_bundle/``.

Every product identity, PCT code and required-document rule used here is read
from the real regulatory data (``regulatory_data/config/textile_mvp_pct_codes.json``
and ``regulatory_data/raw/psw/textile_product_requirements/textile_product_requirements.json``)
through the application's own loaders. No requirement is invented.

For every scenario this script:

1. Builds a "ground truth" invoice + packing-list extraction, i.e. Pydantic
   objects shaped exactly like what a correct extraction of the generated PDF
   should produce.
2. Runs that ground truth through the *real* deterministic pipeline functions
   (the single-line weight fallback, item matching, per-item checks, the
   compliance engine, and shipment-level checks) so the "expected" results in
   each ``expected_extraction_and_checks.json`` are computed by the actual
   application code, not hand-predicted.
3. Renders a commercial invoice and packing list as text PDFs from the same
   ground truth, plus a rasterized (image-only, no embedded text) "scanned"
   version of each to exercise the Tesseract OCR fallback path.

Run:
    python -m scripts.generate_synthetic_test_bundles
    python -m scripts.generate_synthetic_test_bundles --only clean_raw_cotton
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.schemas.compliance import ComplianceCheckResponse  # noqa: E402
from app.schemas.multi_line_extraction import (  # noqa: E402
    InvoiceLineItem,
    MultiLineCommercialInvoiceExtraction,
    MultiLinePackingListExtraction,
    MultiLineShipmentRequest,
    PackingListItem,
)
from app.schemas.shipment_extraction import (  # noqa: E402
    ExtractedField,
    ExtractionMethod,
    FieldValidationStatus,
)
from app.services.compliance.rule_engine import (  # noqa: E402
    DeterministicComplianceRuleEngine,
)
from app.services.compliance.rule_loader import (  # noqa: E402
    load_compliance_rules,
    reload_compliance_rules,
)
from app.services.multi_line.item_matching import ItemMatch, match_line_items  # noqa: E402
from app.services.multi_line.line_item_checks import shipment_level_checks  # noqa: E402

# These four names are the exact private helpers the live pipeline uses to
# roll up item-level provenance, validate PCT codes, apply the single-line
# declared-total weight fallback, and build one matched item's full result.
# Importing them (instead of re-implementing the logic) guarantees the
# "expected" results in every JSON file are computed by the real application
# code and cannot silently drift from it.
from app.services.multi_line_shipment_service import (  # noqa: E402
    _INVOICE_ITEM_VALUE_FIELDS,
    _PACKING_ITEM_VALUE_FIELDS,
    _apply_single_line_declared_weight_fallback,
    _build_item_result,
    _overall_shipment_status,
    _roll_up_item,
    _validate_pct,
)

REG = PROJECT_ROOT / "regulatory_data"
PCT_CONFIG_PATH = REG / "config" / "textile_mvp_pct_codes.json"
PRODUCT_REQ_PATH = (
    REG / "raw" / "psw" / "textile_product_requirements" / "textile_product_requirements.json"
)
OUTPUT_ROOT = PROJECT_ROOT / "synthetic_factory"

_NAMESPACE = uuid.UUID("6c9a6e2e-6f3c-4e7a-9a0e-3c1a2b4d5e6f")
_BANNER = "SYNTHETIC TEST DOCUMENT — FOR SOFTWARE TESTING ONLY"
_PAGE_W, _PAGE_H = 595.0, 842.0


def _load_reference_data() -> tuple[dict, dict]:
    """Read the real PCT config + product requirements (source of truth)."""
    pct_config = json.loads(PCT_CONFIG_PATH.read_text(encoding="utf-8"))
    product_reqs = json.loads(PRODUCT_REQ_PATH.read_text(encoding="utf-8"))
    return pct_config, product_reqs


PCT_CONFIG, PRODUCT_REQS = _load_reference_data()
PRODUCTS_BY_CODE = {p["pct_code"]: p for p in PCT_CONFIG["products"]}
RAW_COTTON_PCT = "52010090"


def _money(value: Decimal | str | int) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def _doc_id(scenario_key: str, role: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{scenario_key}:{role}")


# --------------------------------------------------------------------------- #
# Scenario data model
# --------------------------------------------------------------------------- #
@dataclass
class LineItemSpec:
    product_name: str
    pct_code: str  # display form, e.g. "6109.1000"
    quantity: Decimal
    unit: str
    unit_price: Decimal
    net_weight: Decimal
    gross_weight: Decimal
    package_count: int
    line_total: Decimal | None = None  # None => quantity * unit_price
    packing_quantity: Decimal | None = None  # None => same as invoice quantity
    packing_net_weight: Decimal | None = None  # None => same as invoice net_weight
    packing_gross_weight: Decimal | None = None  # None => same as invoice gross_weight
    omit_invoice_line_weight: bool = False  # True => weight not printed on this line
    line_number: int = 1

    def resolved_line_total(self) -> Decimal:
        if self.line_total is not None:
            return self.line_total
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    def resolved_packing_quantity(self) -> Decimal:
        return self.packing_quantity if self.packing_quantity is not None else self.quantity

    def resolved_packing_net_weight(self) -> Decimal:
        return (
            self.packing_net_weight
            if self.packing_net_weight is not None
            else self.net_weight
        )

    def resolved_packing_gross_weight(self) -> Decimal:
        return (
            self.packing_gross_weight
            if self.packing_gross_weight is not None
            else self.gross_weight
        )


@dataclass
class ScenarioSpec:
    key: str
    title: str
    description: str
    injected_defect: str | None
    exporter_name: str
    buyer_name: str
    invoice_number: str
    invoice_date: date
    destination_country: str
    shipment_date: date
    items: list[LineItemSpec]
    additional_uploaded_document_types: list[str] = field(default_factory=list)
    letter_of_credit_date: date | None = None
    currency: str = "USD"
    packing_list_number: str | None = None
    omit_invoice_line_weights: bool = False  # applies to every line item

    def invoice_total(self) -> Decimal:
        return sum((item.resolved_line_total() for item in self.items), Decimal("0.00"))

    def declared_net_weight_total(self) -> Decimal:
        return sum((item.net_weight for item in self.items), Decimal("0.00"))

    def declared_gross_weight_total(self) -> Decimal:
        return sum((item.gross_weight for item in self.items), Decimal("0.00"))

    def packing_net_weight_total(self) -> Decimal:
        return sum(
            (item.resolved_packing_net_weight() for item in self.items), Decimal("0.00")
        )

    def packing_gross_weight_total(self) -> Decimal:
        return sum(
            (item.resolved_packing_gross_weight() for item in self.items), Decimal("0.00")
        )


def _product(pct_code_key: str) -> dict:
    return PRODUCTS_BY_CODE[pct_code_key]


def _tshirt_item(**overrides: Any) -> LineItemSpec:
    base = dict(
        product_name=_product("61091000")["simple_product_name"],
        pct_code=_product("61091000")["display_pct_code"],
        quantity=Decimal("100"),
        unit="PCS",
        unit_price=Decimal("5.50"),
        net_weight=Decimal("75.00"),
        gross_weight=Decimal("80.00"),
        package_count=5,
    )
    base.update(overrides)
    return LineItemSpec(**base)


def _yarn_item(**overrides: Any) -> LineItemSpec:
    base = dict(
        product_name=_product("52051100")["simple_product_name"],
        pct_code=_product("52051100")["display_pct_code"],
        quantity=Decimal("5000"),
        unit="KG",
        unit_price=Decimal("3.20"),
        net_weight=Decimal("5000.00"),
        gross_weight=Decimal("5050.00"),
        package_count=50,
    )
    base.update(overrides)
    return LineItemSpec(**base)


def _denim_item(**overrides: Any) -> LineItemSpec:
    base = dict(
        product_name=_product("52094200")["simple_product_name"],
        pct_code=_product("52094200")["display_pct_code"],
        quantity=Decimal("2000"),
        unit="MTR",
        unit_price=Decimal("4.50"),
        net_weight=Decimal("1800.00"),
        gross_weight=Decimal("1850.00"),
        package_count=40,
    )
    base.update(overrides)
    return LineItemSpec(**base)


def _bedsheet_item(**overrides: Any) -> LineItemSpec:
    base = dict(
        product_name=_product("63023110")["simple_product_name"],
        pct_code=_product("63023110")["display_pct_code"],
        quantity=Decimal("2000"),
        unit="PCS",
        unit_price=Decimal("6.75"),
        net_weight=Decimal("1200.00"),
        gross_weight=Decimal("1260.00"),
        package_count=60,
    )
    base.update(overrides)
    return LineItemSpec(**base)


def _raw_cotton_item(**overrides: Any) -> LineItemSpec:
    base = dict(
        product_name=_product("52010090")["simple_product_name"],
        pct_code=_product("52010090")["display_pct_code"],
        quantity=Decimal("1000"),
        unit="KG",
        unit_price=Decimal("2.00"),
        net_weight=Decimal("1000.00"),
        gross_weight=Decimal("1025.00"),
        package_count=20,
    )
    base.update(overrides)
    return LineItemSpec(**base)


_RAW_COTTON_CLEAN_DOCS = [
    "form_e",
    "phytosanitary_certificate",
    "sbp_deposit_proof",
    "sbp_confirmation",
    "irrevocable_letter_of_credit",
    "import_permit",
]


def build_scenarios() -> list[ScenarioSpec]:
    scenarios: list[ScenarioSpec] = []

    # --- 1-5: one clean scenario per product ------------------------------ #
    scenarios.append(
        ScenarioSpec(
            key="clean_raw_cotton",
            title="Clean raw-cotton shipment (all SRO 2486(I)/2025 conditions met)",
            description=(
                "Raw cotton export to the United Arab Emirates with the SBP deposit "
                "proof, SBP confirmation, irrevocable letter of credit and "
                "phytosanitary certificate all present, shipped well within the "
                "180-day letter-of-credit window."
            ),
            injected_defect=None,
            exporter_name="Multan Raw Cotton Traders (Pvt.) Ltd.",
            buyer_name="Al Ain Fibre Trading LLC",
            invoice_number="MRC-INV-2026-014",
            invoice_date=date(2026, 5, 28),
            destination_country="United Arab Emirates",
            shipment_date=date(2026, 6, 1),
            letter_of_credit_date=date(2026, 1, 1),
            items=[_raw_cotton_item()],
            additional_uploaded_document_types=list(_RAW_COTTON_CLEAN_DOCS),
            packing_list_number="MRC-PL-2026-014",
        )
    )
    scenarios.append(
        ScenarioSpec(
            key="clean_cotton_yarn",
            title="Clean cotton-yarn shipment to China",
            description="Single-line cotton yarn export to China with a certificate of origin under CPFTA.",
            injected_defect=None,
            exporter_name="Faisalabad Yarn Spinners (Pvt.) Ltd.",
            buyer_name="Suzhou Textile Import Co., Ltd.",
            invoice_number="FYS-INV-2026-101",
            invoice_date=date(2026, 6, 10),
            destination_country="China",
            shipment_date=date(2026, 6, 15),
            items=[_yarn_item()],
            additional_uploaded_document_types=["form_e", "certificate_of_origin"],
            packing_list_number="FYS-PL-2026-101",
        )
    )
    scenarios.append(
        ScenarioSpec(
            key="clean_denim_fabric",
            title="Clean denim-fabric shipment to China",
            description="Single-line denim fabric export to China with a certificate of origin under CPFTA.",
            injected_defect=None,
            exporter_name="Karachi Denim Mills (Pvt.) Ltd.",
            buyer_name="Guangzhou Fashion Fabrics Co., Ltd.",
            invoice_number="KDM-INV-2026-207",
            invoice_date=date(2026, 6, 18),
            destination_country="China",
            shipment_date=date(2026, 6, 22),
            items=[_denim_item()],
            additional_uploaded_document_types=["form_e", "certificate_of_origin"],
            packing_list_number="KDM-PL-2026-207",
        )
    )
    scenarios.append(
        ScenarioSpec(
            key="clean_cotton_tshirts",
            title="Clean cotton knitted T-shirt shipment to China",
            description=(
                "Single-line cotton T-shirt export to China, weights printed on "
                "the invoice line itself (not relying on the document-total fallback)."
            ),
            injected_defect=None,
            exporter_name="Lahore Cotton Garments (Pvt.) Ltd.",
            buyer_name="Shanghai Sample Trading Co., Ltd.",
            invoice_number="LCG-INV-2026-002",
            invoice_date=date(2026, 6, 25),
            destination_country="China",
            shipment_date=date(2026, 6, 29),
            items=[_tshirt_item()],
            additional_uploaded_document_types=["form_e", "certificate_of_origin"],
            packing_list_number="LCG-PL-2026-002",
        )
    )
    scenarios.append(
        ScenarioSpec(
            key="clean_bedsheets",
            title="Clean cotton bed-sheet shipment to China",
            description="Single-line mill-made cotton bed-sheet export to China with a certificate of origin under CPFTA.",
            injected_defect=None,
            exporter_name="Faisalabad Home Textiles (Pvt.) Ltd.",
            buyer_name="Hangzhou Home Living Co., Ltd.",
            invoice_number="FHT-INV-2026-305",
            invoice_date=date(2026, 7, 1),
            destination_country="China",
            shipment_date=date(2026, 7, 5),
            items=[_bedsheet_item()],
            additional_uploaded_document_types=["form_e", "certificate_of_origin"],
            packing_list_number="FHT-PL-2026-305",
        )
    )

    # --- 6: single-line declared-total weight fallback --------------------- #
    scenarios.append(
        ScenarioSpec(
            key="single_line_declared_total_fallback",
            title="Single-line T-shirt invoice with weights only as a document total",
            description=(
                "The invoice prints Net Weight / Gross Weight only once, as a "
                "document-level declared total at the bottom of the page, not on "
                "the product line itself. The packing list prints the weight on "
                "its line as usual. This exercises the deterministic "
                "single_line_declared_total fallback."
            ),
            injected_defect=None,
            exporter_name="Lahore Cotton Garments (Pvt.) Ltd.",
            buyer_name="Shanghai Sample Trading Co., Ltd.",
            invoice_number="LCG-INV-2026-001",
            invoice_date=date(2026, 7, 20),
            destination_country="China",
            shipment_date=date(2026, 7, 20),
            items=[_tshirt_item(omit_invoice_line_weight=True)],
            additional_uploaded_document_types=["form_e", "certificate_of_origin"],
            packing_list_number="LCG-PL-2026-001",
            omit_invoice_line_weights=True,
        )
    )

    # --- 7: multi-line shipment -------------------------------------------- #
    scenarios.append(
        ScenarioSpec(
            key="multi_line_shipment",
            title="Multi-line shipment: T-shirts, denim and bed sheets to China",
            description=(
                "Three products on one invoice/packing-list pair, each with its "
                "own printed weight, confirming per-item matching and that the "
                "single-line weight fallback correctly does not fire for a "
                "multi-line invoice."
            ),
            injected_defect=None,
            exporter_name="Punjab Textile Exporters Consortium",
            buyer_name="Shanghai Sample Trading Co., Ltd.",
            invoice_number="PTE-INV-2026-410",
            invoice_date=date(2026, 7, 8),
            destination_country="China",
            shipment_date=date(2026, 7, 12),
            items=[
                _tshirt_item(line_number=1),
                _denim_item(line_number=2, quantity=Decimal("500"), unit_price=Decimal("4.50"),
                            net_weight=Decimal("450.00"), gross_weight=Decimal("460.00"),
                            package_count=10),
                _bedsheet_item(line_number=3, quantity=Decimal("300"), unit_price=Decimal("6.75"),
                                net_weight=Decimal("180.00"), gross_weight=Decimal("190.00"),
                                package_count=9),
            ],
            additional_uploaded_document_types=["form_e", "certificate_of_origin"],
            packing_list_number="PTE-PL-2026-410",
        )
    )

    # --- 8: non-China destination certificate-of-origin path --------------- #
    scenarios.append(
        ScenarioSpec(
            key="non_china_destination_coo",
            title="Cotton-yarn shipment to Germany (non-CPFTA certificate of origin)",
            description=(
                "Same product as the clean cotton-yarn scenario, but shipped to "
                "Germany instead of China, exercising the general "
                "'other TDAP certificate-of-origin destinations' path instead of "
                "the China/CPFTA-specific path."
            ),
            injected_defect=None,
            exporter_name="Faisalabad Yarn Spinners (Pvt.) Ltd.",
            buyer_name="Bremen Textile Handels GmbH",
            invoice_number="FYS-INV-2026-118",
            invoice_date=date(2026, 6, 30),
            destination_country="Germany",
            shipment_date=date(2026, 7, 3),
            items=[_yarn_item()],
            additional_uploaded_document_types=["form_e", "certificate_of_origin"],
            packing_list_number="FYS-PL-2026-118",
        )
    )

    # --- 9-14: error-injection scenarios ------------------------------------ #
    scenarios.append(
        ScenarioSpec(
            key="error_missing_form_e",
            title="Missing Form-E",
            description="Otherwise clean T-shirt shipment with the Form-E declaration not provided.",
            injected_defect="Form-E was not uploaded with the shipment.",
            exporter_name="Lahore Cotton Garments (Pvt.) Ltd.",
            buyer_name="Shanghai Sample Trading Co., Ltd.",
            invoice_number="LCG-INV-2026-011",
            invoice_date=date(2026, 6, 12),
            destination_country="China",
            shipment_date=date(2026, 6, 16),
            items=[_tshirt_item()],
            additional_uploaded_document_types=["certificate_of_origin"],  # form_e omitted
            packing_list_number="LCG-PL-2026-011",
        )
    )
    scenarios.append(
        ScenarioSpec(
            key="error_quantity_mismatch",
            title="Invoice/packing-list quantity mismatch",
            description="Invoice declares 100 PCS; the packing list declares 99 PCS for the same line.",
            injected_defect="Packing-list quantity (99) does not match the invoice quantity (100).",
            exporter_name="Lahore Cotton Garments (Pvt.) Ltd.",
            buyer_name="Shanghai Sample Trading Co., Ltd.",
            invoice_number="LCG-INV-2026-012",
            invoice_date=date(2026, 6, 12),
            destination_country="China",
            shipment_date=date(2026, 6, 16),
            items=[_tshirt_item(packing_quantity=Decimal("99"))],
            additional_uploaded_document_types=["form_e", "certificate_of_origin"],
            packing_list_number="LCG-PL-2026-012",
        )
    )
    scenarios.append(
        ScenarioSpec(
            key="error_arithmetic_mismatch",
            title="Quantity times unit price does not equal the declared line total",
            description="100 PCS at USD 5.50 should total USD 550.00, but the invoice declares USD 500.00.",
            injected_defect="Declared line total (500.00) does not equal quantity x unit price (550.00).",
            exporter_name="Lahore Cotton Garments (Pvt.) Ltd.",
            buyer_name="Shanghai Sample Trading Co., Ltd.",
            invoice_number="LCG-INV-2026-013",
            invoice_date=date(2026, 6, 12),
            destination_country="China",
            shipment_date=date(2026, 6, 16),
            items=[_tshirt_item(line_total=Decimal("500.00"))],
            additional_uploaded_document_types=["form_e", "certificate_of_origin"],
            packing_list_number="LCG-PL-2026-013",
        )
    )
    scenarios.append(
        ScenarioSpec(
            key="error_weight_inversion",
            title="Gross weight lower than net weight",
            description=(
                "Invoice and packing list agree with each other, but both declare "
                "a gross weight (75.00 KG) lower than the net weight (80.00 KG), "
                "which is physically impossible."
            ),
            injected_defect="Declared gross weight (75.00) is lower than the declared net weight (80.00).",
            exporter_name="Lahore Cotton Garments (Pvt.) Ltd.",
            buyer_name="Shanghai Sample Trading Co., Ltd.",
            invoice_number="LCG-INV-2026-014",
            invoice_date=date(2026, 6, 12),
            destination_country="China",
            shipment_date=date(2026, 6, 16),
            items=[_tshirt_item(net_weight=Decimal("80.00"), gross_weight=Decimal("75.00"))],
            additional_uploaded_document_types=["form_e", "certificate_of_origin"],
            packing_list_number="LCG-PL-2026-014",
        )
    )
    scenarios.append(
        ScenarioSpec(
            key="error_raw_cotton_missing_sbp_deposit",
            title="Raw cotton shipment missing the SBP deposit proof",
            description="Same as the clean raw-cotton scenario, but the proof of the 1% SBP security deposit is not provided.",
            injected_defect="Proof of the 1% SBP security deposit was not uploaded with the shipment.",
            exporter_name="Multan Raw Cotton Traders (Pvt.) Ltd.",
            buyer_name="Al Ain Fibre Trading LLC",
            invoice_number="MRC-INV-2026-015",
            invoice_date=date(2026, 5, 28),
            destination_country="United Arab Emirates",
            shipment_date=date(2026, 6, 1),
            letter_of_credit_date=date(2026, 1, 1),
            items=[_raw_cotton_item()],
            additional_uploaded_document_types=[
                d for d in _RAW_COTTON_CLEAN_DOCS if d != "sbp_deposit_proof"
            ],
            packing_list_number="MRC-PL-2026-015",
        )
    )
    scenarios.append(
        ScenarioSpec(
            key="error_raw_cotton_deadline_exceeded",
            title="Raw cotton shipped more than 180 days after the letter of credit",
            description=(
                "Same as the clean raw-cotton scenario, but the shipment date is "
                "195 days after the letter-of-credit date, past the 180-day "
                "SRO 2486(I)/2025 window."
            ),
            injected_defect="Shipment occurred 195 days after the letter-of-credit date (limit is 180 days).",
            exporter_name="Multan Raw Cotton Traders (Pvt.) Ltd.",
            buyer_name="Al Ain Fibre Trading LLC",
            invoice_number="MRC-INV-2026-016",
            invoice_date=date(2026, 7, 10),
            destination_country="United Arab Emirates",
            shipment_date=date(2026, 7, 15),
            letter_of_credit_date=date(2026, 1, 1),
            items=[_raw_cotton_item()],
            additional_uploaded_document_types=list(_RAW_COTTON_CLEAN_DOCS),
            packing_list_number="MRC-PL-2026-016",
        )
    )
    scenarios.append(
        ScenarioSpec(
            key="manual_review_unsupported_pct_code",
            title="PCT code outside the validated MVP catalog",
            description=(
                "A well-formed 8-digit PCT code that does not appear in the "
                "textile MVP support list, on an otherwise clean invoice and "
                "packing list."
            ),
            injected_defect=(
                "PCT code 9999.9999 is not a supported product under the "
                "current MVP scope."
            ),
            exporter_name="Lahore Cotton Garments (Pvt.) Ltd.",
            buyer_name="Shanghai Sample Trading Co., Ltd.",
            invoice_number="LCG-INV-2026-017",
            invoice_date=date(2026, 6, 12),
            destination_country="China",
            shipment_date=date(2026, 6, 16),
            items=[
                _tshirt_item(
                    pct_code="9999.9999",
                    product_name="Unclassified Synthetic Fibre Blend",
                )
            ],
            additional_uploaded_document_types=["form_e", "certificate_of_origin"],
            packing_list_number="LCG-PL-2026-017",
        )
    )

    return scenarios


# --------------------------------------------------------------------------- #
# Ground-truth ExtractedField / item builders
# --------------------------------------------------------------------------- #
def _field(
    value: Any,
    *,
    doc_id: uuid.UUID,
    page: int = 1,
    confidence: Decimal = Decimal("0.97"),
    note: str = "Read from the document.",
) -> ExtractedField:
    return ExtractedField(
        value=value,
        source_document_id=doc_id,
        source_page=page,
        extraction_method=ExtractionMethod.PDF_TEXT_LLM_STRUCTURED_OUTPUT,
        confidence=confidence,
        validation_status=FieldValidationStatus.VERIFIED,
        validation_note=note,
    )


def _missing_field(
    *,
    doc_id: uuid.UUID,
    page: int = 1,
    note: str,
) -> ExtractedField:
    return ExtractedField(
        value=None,
        source_document_id=doc_id,
        source_page=page,
        extraction_method=ExtractionMethod.PDF_TEXT_LLM_STRUCTURED_OUTPUT,
        confidence=Decimal("0.0"),
        validation_status=FieldValidationStatus.MANUAL_REVIEW,
        validation_note=note,
    )


def _build_invoice_item(
    spec: LineItemSpec, item_index: int, invoice_doc_id: uuid.UUID
) -> InvoiceLineItem:
    fields: dict[str, ExtractedField] = {
        "line_number": _field(spec.line_number, doc_id=invoice_doc_id),
        "product_name": _field(spec.product_name, doc_id=invoice_doc_id),
        "pct_code": _field(spec.pct_code, doc_id=invoice_doc_id),
        "quantity": _field(spec.quantity, doc_id=invoice_doc_id),
        "unit": _field(spec.unit, doc_id=invoice_doc_id),
        "unit_price": _field(spec.unit_price, doc_id=invoice_doc_id),
        "line_total": _field(spec.resolved_line_total(), doc_id=invoice_doc_id),
    }
    if spec.omit_invoice_line_weight:
        fields["net_weight"] = _missing_field(
            doc_id=invoice_doc_id,
            note=(
                "Not printed on the invoice line; only a document-level "
                "declared total was found."
            ),
        )
        fields["gross_weight"] = _missing_field(
            doc_id=invoice_doc_id,
            note=(
                "Not printed on the invoice line; only a document-level "
                "declared total was found."
            ),
        )
    else:
        fields["net_weight"] = _field(spec.net_weight, doc_id=invoice_doc_id)
        fields["gross_weight"] = _field(spec.gross_weight, doc_id=invoice_doc_id)

    fields["pct_code"] = _validate_pct(fields["pct_code"])
    source_page, confidence, status, note = _roll_up_item(fields, _INVOICE_ITEM_VALUE_FIELDS)
    return InvoiceLineItem(
        item_index=item_index,
        **fields,
        item_source_page=source_page,
        item_confidence=confidence,
        item_validation_status=status,
        item_note=note,
    )


def _build_packing_item(
    spec: LineItemSpec, item_index: int, packing_doc_id: uuid.UUID
) -> PackingListItem:
    fields: dict[str, ExtractedField] = {
        "line_number": _field(spec.line_number, doc_id=packing_doc_id),
        "product_name": _field(spec.product_name, doc_id=packing_doc_id),
        "pct_code": _field(spec.pct_code, doc_id=packing_doc_id),
        "quantity": _field(spec.resolved_packing_quantity(), doc_id=packing_doc_id),
        "unit": _field(spec.unit, doc_id=packing_doc_id),
        "net_weight": _field(spec.resolved_packing_net_weight(), doc_id=packing_doc_id),
        "gross_weight": _field(spec.resolved_packing_gross_weight(), doc_id=packing_doc_id),
        "package_count": _field(spec.package_count, doc_id=packing_doc_id),
    }
    fields["pct_code"] = _validate_pct(fields["pct_code"])
    source_page, confidence, status, note = _roll_up_item(fields, _PACKING_ITEM_VALUE_FIELDS)
    return PackingListItem(
        item_index=item_index,
        **fields,
        item_source_page=source_page,
        item_confidence=confidence,
        item_validation_status=status,
        item_note=note,
    )


def build_invoice_extraction(
    scenario: ScenarioSpec, invoice_doc_id: uuid.UUID
) -> MultiLineCommercialInvoiceExtraction:
    line_items = [
        _build_invoice_item(item, index, invoice_doc_id)
        for index, item in enumerate(scenario.items, start=1)
    ]
    return MultiLineCommercialInvoiceExtraction(
        exporter_name=_field(scenario.exporter_name, doc_id=invoice_doc_id),
        buyer_name=_field(scenario.buyer_name, doc_id=invoice_doc_id),
        invoice_number=_field(scenario.invoice_number, doc_id=invoice_doc_id),
        invoice_date=_field(scenario.invoice_date, doc_id=invoice_doc_id),
        currency=_field(scenario.currency, doc_id=invoice_doc_id),
        destination_country=_field(scenario.destination_country, doc_id=invoice_doc_id),
        invoice_total=_field(scenario.invoice_total(), doc_id=invoice_doc_id),
        declared_net_weight_total=_field(
            scenario.declared_net_weight_total(), doc_id=invoice_doc_id
        ),
        declared_gross_weight_total=_field(
            scenario.declared_gross_weight_total(), doc_id=invoice_doc_id
        ),
        line_items=line_items,
    )


def build_packing_extraction(
    scenario: ScenarioSpec, packing_doc_id: uuid.UUID
) -> MultiLinePackingListExtraction:
    items = [
        _build_packing_item(item, index, packing_doc_id)
        for index, item in enumerate(scenario.items, start=1)
    ]
    return MultiLinePackingListExtraction(
        declared_net_weight_total=_field(
            scenario.packing_net_weight_total(), doc_id=packing_doc_id
        ),
        declared_gross_weight_total=_field(
            scenario.packing_gross_weight_total(), doc_id=packing_doc_id
        ),
        items=items,
    )


# --------------------------------------------------------------------------- #
# Run the real pipeline to compute expected results
# --------------------------------------------------------------------------- #
def compute_expected_results(scenario: ScenarioSpec) -> dict[str, Any]:
    invoice_doc_id = _doc_id(scenario.key, "invoice")
    packing_doc_id = _doc_id(scenario.key, "packing")

    raw_invoice = build_invoice_extraction(scenario, invoice_doc_id)
    packing = build_packing_extraction(scenario, packing_doc_id)

    # The exact deterministic fallback the live pipeline runs before matching.
    invoice = _apply_single_line_declared_weight_fallback(raw_invoice)

    matches: list[ItemMatch] = match_line_items(invoice.line_items, packing.items)

    request = MultiLineShipmentRequest(
        commercial_invoice_document_id=invoice_doc_id,
        packing_list_document_id=packing_doc_id,
        shipment_date=scenario.shipment_date,
        letter_of_credit_date=scenario.letter_of_credit_date,
        additional_uploaded_document_types=scenario.additional_uploaded_document_types,
    )
    engine = DeterministicComplianceRuleEngine()

    item_results = [
        _build_item_result(request=request, invoice=invoice, match=match, engine=engine)
        for match in matches
    ]
    shipment_checks = shipment_level_checks(invoice, packing, matches)
    overall_status = _overall_shipment_status(item_results, shipment_checks)

    items_out = []
    for result in item_results:
        derived_fields = []
        if result.invoice_item_index is not None:
            invoice_item = next(
                i for i in invoice.line_items if i.item_index == result.invoice_item_index
            )
            for name in ("net_weight", "gross_weight"):
                derived: ExtractedField = getattr(invoice_item, name)
                if derived.derivation_method:
                    derived_fields.append(
                        {
                            "field": name,
                            "derivation_method": derived.derivation_method,
                            "original_field_location": derived.original_field_location,
                            "value": str(derived.value),
                        }
                    )
        compliance: ComplianceCheckResponse | None = result.compliance
        items_out.append(
            {
                "item_reference": result.item_reference,
                "product_name": result.product_name,
                "pct_code": result.pct_code,
                "match_status": result.match_status.value,
                "match_strategy": result.match_strategy.value if result.match_strategy else None,
                "overall_item_status": result.status.value,
                "cross_document_checks": {
                    c.check_id: c.status.value for c in result.item_checks
                },
                "phase1_compliance_checks": (
                    {c.check_id: c.status.value for c in compliance.checks}
                    if compliance
                    else {}
                ),
                "executable_rule_checks": (
                    {c.check_id: c.status.value for c in compliance.executable_rule_checks}
                    if compliance
                    else {}
                ),
                "derived_fields": derived_fields,
            }
        )

    return {
        "overall_status": overall_status.value,
        "items": items_out,
        "shipment_level_checks": {c.check_id: c.status.value for c in shipment_checks},
        "raw_invoice": raw_invoice,
        "invoice_after_fallback": invoice,
        "packing": packing,
    }


# --------------------------------------------------------------------------- #
# PDF rendering
# --------------------------------------------------------------------------- #
@dataclass
class Column:
    label: str
    x: float
    width: float
    align: str = "left"  # "left" | "right"


def _text_width(text: str, fontsize: float, fontname: str = "helv") -> float:
    return pymupdf.get_text_length(text, fontname=fontname, fontsize=fontsize)


def _draw_cell(page: "pymupdf.Page", column: Column, y: float, text: str, fontsize: float) -> None:
    x = column.x
    if column.align == "right":
        x = column.x + column.width - _text_width(text, fontsize)
    page.insert_text((x, y), text, fontsize=fontsize, fontname="helv")


def _draw_table(
    page: "pymupdf.Page",
    y: float,
    columns: list[Column],
    rows: list[list[str]],
    *,
    fontsize: float = 7.5,
    header_fontsize: float = 7.5,
    row_height: float = 16.0,
) -> float:
    left = columns[0].x
    right = columns[-1].x + columns[-1].width
    page.draw_line((left, y), (right, y), width=0.6)
    y += 12
    for column in columns:
        _draw_cell(page, column, y, column.label, header_fontsize)
    y += 6
    page.draw_line((left, y), (right, y), width=0.6)
    y += 12
    for row in rows:
        for column, value in zip(columns, row):
            _draw_cell(page, column, y, value, fontsize)
        y += row_height
    page.draw_line((left, y - row_height + 4), (right, y - row_height + 4), width=0.6)
    return y


def _draw_kv_block(page: "pymupdf.Page", y: float, rows: list[tuple[str, str]]) -> float:
    for label, value in rows:
        page.insert_text((50, y), label, fontsize=9, fontname="helv")
        page.insert_text((190, y), value, fontsize=9, fontname="helv")
        y += 17
    return y


def render_commercial_invoice_pdf(path: Path, scenario: ScenarioSpec) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
    y: float = 46
    page.insert_text((50, y), _BANNER, fontsize=8, fontname="helv")
    y += 24
    page.insert_text((50, y), "COMMERCIAL INVOICE", fontsize=15, fontname="helv")
    y += 30

    y = _draw_kv_block(
        page,
        y,
        [
            ("Exporter", scenario.exporter_name),
            ("Buyer / Consignee", scenario.buyer_name),
            ("Invoice Number", scenario.invoice_number),
            ("Invoice Date", scenario.invoice_date.isoformat()),
            ("Destination Country", scenario.destination_country),
            ("Currency", scenario.currency),
        ],
    )
    y += 14

    if scenario.omit_invoice_line_weights:
        columns = [
            Column("Line", 50, 26),
            Column("Product Description", 80, 175),
            Column("PCT Code", 258, 62),
            Column("Quantity", 324, 46, align="right"),
            Column("Unit", 374, 34),
            Column("Unit Price", 412, 62, align="right"),
            Column("Line Total", 478, 67, align="right"),
        ]
        rows = [
            [
                str(item.line_number),
                item.product_name,
                item.pct_code,
                str(item.quantity),
                item.unit,
                _money(item.unit_price),
                _money(item.resolved_line_total()),
            ]
            for item in scenario.items
        ]
    else:
        columns = [
            Column("Line", 50, 22),
            Column("Product Description", 74, 140),
            Column("PCT Code", 216, 55),
            Column("Quantity", 273, 40, align="right"),
            Column("Unit", 316, 28),
            Column("Unit Price", 347, 50, align="right"),
            Column("Line Total", 400, 55, align="right"),
            Column("Net Wt (KG)", 458, 50, align="right"),
            Column("Gross Wt (KG)", 511, 55, align="right"),
        ]
        rows = [
            [
                str(item.line_number),
                item.product_name,
                item.pct_code,
                str(item.quantity),
                item.unit,
                _money(item.unit_price),
                _money(item.resolved_line_total()),
                _money(item.net_weight),
                _money(item.gross_weight),
            ]
            for item in scenario.items
        ]

    y = _draw_table(page, y, columns, rows)
    y += 22

    footer_rows = [("Invoice Total", f"{scenario.currency} {_money(scenario.invoice_total())}")]
    if scenario.omit_invoice_line_weights:
        footer_rows.append(("Net Weight", f"{_money(scenario.declared_net_weight_total())} KG"))
        footer_rows.append(
            ("Gross Weight", f"{_money(scenario.declared_gross_weight_total())} KG")
        )
    else:
        footer_rows.append(
            ("Declared Net Weight Total", f"{_money(scenario.declared_net_weight_total())} KG")
        )
        footer_rows.append(
            (
                "Declared Gross Weight Total",
                f"{_money(scenario.declared_gross_weight_total())} KG",
            )
        )
    _draw_kv_block(page, y, footer_rows)

    doc.set_metadata(
        {
            "title": f"Synthetic commercial invoice - {scenario.key}",
            "author": "Enterprise Customs Engine synthetic test factory",
            "subject": "Synthetic data for software testing only; not a real commercial document",
        }
    )
    doc.save(path, deflate=True)
    doc.close()


def render_packing_list_pdf(path: Path, scenario: ScenarioSpec) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
    y: float = 46
    page.insert_text((50, y), _BANNER, fontsize=8, fontname="helv")
    y += 24
    page.insert_text((50, y), "PACKING LIST", fontsize=15, fontname="helv")
    y += 30

    y = _draw_kv_block(
        page,
        y,
        [
            ("Packing List Number", scenario.packing_list_number or f"{scenario.invoice_number}-PL"),
            ("Related Invoice Number", scenario.invoice_number),
            ("Destination Country", scenario.destination_country),
        ],
    )
    y += 14

    columns = [
        Column("Item", 50, 24),
        Column("Product Description", 76, 138),
        Column("PCT Code", 216, 55),
        Column("Quantity", 273, 40, align="right"),
        Column("Unit", 316, 28),
        Column("Packages", 347, 42, align="right"),
        Column("Net Wt (KG)", 392, 55, align="right"),
        Column("Gross Wt (KG)", 450, 60, align="right"),
    ]
    rows = [
        [
            str(item.line_number),
            item.product_name,
            item.pct_code,
            str(item.resolved_packing_quantity()),
            item.unit,
            str(item.package_count),
            _money(item.resolved_packing_net_weight()),
            _money(item.resolved_packing_gross_weight()),
        ]
        for item in scenario.items
    ]
    y = _draw_table(page, y, columns, rows)
    y += 22

    total_packages = sum(item.package_count for item in scenario.items)
    _draw_kv_block(
        page,
        y,
        [
            ("Total Packages", f"{total_packages} CARTONS"),
            ("Declared Net Weight Total", f"{_money(scenario.packing_net_weight_total())} KG"),
            (
                "Declared Gross Weight Total",
                f"{_money(scenario.packing_gross_weight_total())} KG",
            ),
        ],
    )

    doc.set_metadata(
        {
            "title": f"Synthetic packing list - {scenario.key}",
            "author": "Enterprise Customs Engine synthetic test factory",
            "subject": "Synthetic data for software testing only; not a real commercial document",
        }
    )
    doc.save(path, deflate=True)
    doc.close()


def rasterize_to_scanned_pdf(text_pdf_path: Path, scanned_pdf_path: Path, *, dpi: int = 150) -> None:
    """Render every page of a text PDF to an image and rebuild an image-only
    PDF with no embedded text layer, so the app's OCR fallback path fires.

    Grayscale at 150 DPI is plenty for Tesseract and keeps files small; saving
    without ``deflate=True`` stores the inserted image uncompressed and can
    balloon a one-page invoice to 10+ MB.
    """
    source = pymupdf.open(text_pdf_path)
    output = pymupdf.open()
    for page_number in range(source.page_count):
        page = source[page_number]
        pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        png_bytes = pixmap.tobytes("png")
        new_page = output.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=png_bytes)
    output.set_metadata(
        {
            "title": source.metadata.get("title", "Synthetic scanned document"),
            "author": "Enterprise Customs Engine synthetic test factory",
            "subject": "Synthetic data for software testing only; rasterized to exercise OCR",
        }
    )
    output.save(scanned_pdf_path, deflate=True, garbage=4)
    output.close()
    source.close()


# --------------------------------------------------------------------------- #
# expected_extraction_and_checks.json assembly
# --------------------------------------------------------------------------- #
def _invoice_json(scenario: ScenarioSpec, raw_invoice: MultiLineCommercialInvoiceExtraction) -> dict:
    return {
        "exporter_name": scenario.exporter_name,
        "buyer_name": scenario.buyer_name,
        "invoice_number": scenario.invoice_number,
        "invoice_date": scenario.invoice_date.isoformat(),
        "currency": scenario.currency,
        "destination_country": scenario.destination_country,
        "invoice_total": _money(scenario.invoice_total()),
        "declared_net_weight_total": _money(scenario.declared_net_weight_total()),
        "declared_gross_weight_total": _money(scenario.declared_gross_weight_total()),
        "line_items": [
            {
                "line_number": item.line_number,
                "product_name": item.product_name,
                "pct_code": item.pct_code,
                "quantity": str(item.quantity),
                "unit": item.unit,
                "unit_price": _money(item.unit_price),
                "line_total": _money(item.resolved_line_total()),
                "net_weight": (
                    None if item.omit_invoice_line_weight else _money(item.net_weight)
                ),
                "gross_weight": (
                    None if item.omit_invoice_line_weight else _money(item.gross_weight)
                ),
            }
            for item in scenario.items
        ],
    }


def _packing_json(scenario: ScenarioSpec) -> dict:
    return {
        "packing_list_number": scenario.packing_list_number or f"{scenario.invoice_number}-PL",
        "related_invoice_number": scenario.invoice_number,
        "destination_country": scenario.destination_country,
        "declared_net_weight_total": _money(scenario.packing_net_weight_total()),
        "declared_gross_weight_total": _money(scenario.packing_gross_weight_total()),
        "items": [
            {
                "line_number": item.line_number,
                "product_name": item.product_name,
                "pct_code": item.pct_code,
                "quantity": str(item.resolved_packing_quantity()),
                "unit": item.unit,
                "net_weight": _money(item.resolved_packing_net_weight()),
                "gross_weight": _money(item.resolved_packing_gross_weight()),
                "package_count": str(item.package_count),
            }
            for item in scenario.items
        ],
    }


def build_expected_json(scenario: ScenarioSpec, computed: dict[str, Any]) -> dict:
    return {
        "scenario": scenario.title,
        "scenario_key": scenario.key,
        "description": scenario.description,
        "injected_defect": scenario.injected_defect,
        "pct_codes": sorted({item.pct_code for item in scenario.items}),
        "commercial_invoice": _invoice_json(scenario, computed["raw_invoice"]),
        "packing_list": _packing_json(scenario),
        "request": {
            "shipment_date": scenario.shipment_date.isoformat(),
            "letter_of_credit_date": (
                scenario.letter_of_credit_date.isoformat()
                if scenario.letter_of_credit_date
                else None
            ),
            "additional_uploaded_document_types": scenario.additional_uploaded_document_types,
        },
        "expected_results": {
            "overall_status": computed["overall_status"],
            "items": computed["items"],
            "shipment_level_checks": computed["shipment_level_checks"],
        },
        "expected_note": (
            "Computed by running the real deterministic pipeline (single-line "
            "weight fallback, item matching, per-item compliance checks and "
            "shipment-level checks) against this scenario's ground-truth "
            "extraction, so these statuses reflect the actual application "
            "code rather than a hand-written prediction."
        ),
    }


def build_request_json(scenario: ScenarioSpec) -> dict:
    return {
        "commercial_invoice_document_id": "<replace-with-uploaded-invoice-uuid>",
        "packing_list_document_id": "<replace-with-uploaded-packing-list-uuid>",
        "shipment_date": scenario.shipment_date.isoformat(),
        "letter_of_credit_date": (
            scenario.letter_of_credit_date.isoformat()
            if scenario.letter_of_credit_date
            else None
        ),
        "additional_uploaded_document_types": scenario.additional_uploaded_document_types,
    }


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def generate_scenario(scenario: ScenarioSpec) -> Path:
    folder = OUTPUT_ROOT / scenario.key
    folder.mkdir(parents=True, exist_ok=True)

    invoice_text_path = folder / "synthetic_commercial_invoice_text.pdf"
    packing_text_path = folder / "synthetic_packing_list_text.pdf"
    invoice_scanned_path = folder / "synthetic_commercial_invoice_scanned.pdf"
    packing_scanned_path = folder / "synthetic_packing_list_scanned.pdf"

    render_commercial_invoice_pdf(invoice_text_path, scenario)
    render_packing_list_pdf(packing_text_path, scenario)
    rasterize_to_scanned_pdf(invoice_text_path, invoice_scanned_path)
    rasterize_to_scanned_pdf(packing_text_path, packing_scanned_path)

    computed = compute_expected_results(scenario)
    expected = build_expected_json(scenario, computed)
    (folder / "expected_extraction_and_checks.json").write_text(
        json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (folder / "multi_line_api_request.json").write_text(
        json.dumps(build_request_json(scenario), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return folder


def write_scenario_matrix_readme(scenarios: list[ScenarioSpec]) -> None:
    lines = [
        "# Synthetic Customs Test Factory",
        "",
        "This directory contains fictional, generated documents for testing only.",
        "Nothing here is a real shipment, company, or government filing.",
        "",
        "## Original hand-built bundle",
        "",
        "`synthetic_customs_test_bundle/` is the original single-scenario bundle "
        "(clean single-line cotton T-shirt shipment to China). It is unchanged by "
        "this factory.",
        "",
        "## Generated scenario matrix",
        "",
        "Every folder below was produced by "
        "`backend/scripts/generate_synthetic_test_bundles.py`, which reads the real "
        "PCT codes and product requirements from `regulatory_data/` and computes "
        "each `expected_extraction_and_checks.json` by running the actual "
        "deterministic pipeline code (weight fallback, item matching, compliance "
        "engine, shipment-level checks) against a ground-truth extraction - the "
        "expected results are not hand-guessed.",
        "",
        "Each folder contains:",
        "",
        "- `synthetic_commercial_invoice_text.pdf` / `synthetic_packing_list_text.pdf` "
        "- selectable-text PDFs.",
        "- `synthetic_commercial_invoice_scanned.pdf` / "
        "`synthetic_packing_list_scanned.pdf` - image-only rasterized PDFs with no "
        "embedded text, to exercise the Tesseract OCR fallback path.",
        "- `expected_extraction_and_checks.json` - ground-truth shipment data plus "
        "the computed expected status of every check.",
        "- `multi_line_api_request.json` - a ready-to-use request body for "
        "`POST /api/v1/compliance/check-documents/multi-line` (or the customs-audit "
        "workflow), with the document-ID placeholders left for you to fill in after "
        "uploading.",
        "",
        "| Folder | PCT code(s) | Scenario | Expected overall status |",
        "| --- | --- | --- | --- |",
    ]
    for scenario in scenarios:
        computed = compute_expected_results(scenario)
        codes = ", ".join(sorted({item.pct_code for item in scenario.items}))
        lines.append(
            f"| `{scenario.key}/` | {codes} | {scenario.title} | "
            f"`{computed['overall_status']}` |"
        )
    lines += [
        "",
        "## Running a scenario through the workflow",
        "",
        "```bash",
        "cd backend",
        "python -m scripts.run_synthetic_folder_workflow ../synthetic_factory/<scenario_key>",
        "```",
        "",
        "Or point it at the whole `synthetic_factory/` directory to run every "
        "scenario (original bundle plus the generated matrix) in one pass; the "
        "runner discovers each `_text`/`_scanned` pair per folder automatically.",
        "",
        "## Regenerating",
        "",
        "```bash",
        "cd backend",
        "python -m scripts.generate_synthetic_test_bundles",
        "```",
        "",
        "All names, addresses, invoice numbers, prices, and shipment details are "
        "fictional.",
        "",
    ]
    (OUTPUT_ROOT / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        help="Generate only this scenario key (may be repeated).",
    )
    args = parser.parse_args()

    reload_compliance_rules()
    scenarios = build_scenarios()
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {s.key for s in scenarios}
        if unknown:
            raise SystemExit(f"Unknown scenario key(s): {', '.join(sorted(unknown))}")
        scenarios = [s for s in scenarios if s.key in wanted]

    for scenario in scenarios:
        folder = generate_scenario(scenario)
        print(f"Generated {scenario.key} -> {folder}")

    if not args.only:
        write_scenario_matrix_readme(build_scenarios())
        print(f"Wrote {OUTPUT_ROOT / 'README.md'}")


if __name__ == "__main__":
    main()
