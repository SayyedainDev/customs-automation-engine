"""Generate synthetic supporting customs documents for the test factory.

Extends ``generate_synthetic_test_bundles.py`` (same PyMuPDF rendering and
rasterization approach) with the *supporting* documents a shipment has to
present: Form-E, certificate of origin, SBP deposit proof and confirmation,
irrevocable letter of credit, phytosanitary certificate, importing-country
permit, goods declaration, bill of lading and export contract.

Two families are produced:

1. **Valid bundles** attached to the existing clean scenarios. Every printed
   field is derived from that scenario's own invoice, so exporter, buyer,
   invoice number, product, PCT code, destination, amount, currency and dates
   agree by construction.
2. **Error scenarios** (Stage 3) carrying exactly one controlled defect each,
   so a single failing check can be attributed to a single cause.

Expected results are **hand-declared** next to each specification. Nothing in
this module imports or calls the compliance engine, the supporting-document
verifier, or the item matcher - the manifest it feeds has to be able to grade
those components.

Every page is watermarked and every identifier is obviously synthetic. No real
bank account, signature, government certificate number, company registration
number or personal identifier appears anywhere in this file.

Run:
    python -m scripts.generate_supporting_documents
    python -m scripts.generate_supporting_documents --only wrong_exporter_on_coo
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from dataclasses import dataclass, field, fields as dataclass_fields
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Only scenario *definitions* and pure rendering helpers are imported - never a
# compliance or verification module.
from scripts.generate_synthetic_test_bundles import (  # noqa: E402
    OUTPUT_ROOT,
    ScenarioSpec,
    _money,
    build_scenarios,
    rasterize_to_scanned_pdf,
)

SUPPORTING_ROOT = OUTPUT_ROOT / "supporting_documents"

_NAMESPACE = uuid.UUID("2f4b6d18-9c3a-4b57-8e21-7d0f5a6c9b34")
_PAGE_W, _PAGE_H = 595.0, 842.0

# Helvetica's PDF base-14 encoding has no em dash, so an em dash here renders as
# a stray glyph. A plain hyphen keeps the mandated sentence legible on the page
# and in OCR output.
WATERMARK = "SYNTHETIC TEST DOCUMENT - NOT VALID FOR TRADE, CUSTOMS OR PAYMENT"

# Fictional bank used everywhere a bank has to be named, so no real institution
# is associated with a fabricated instrument.
SYNTHETIC_BANK = "Synthetic Test Bank Limited"


# --------------------------------------------------------------------------- #
# Printed / expected field model
# --------------------------------------------------------------------------- #
@dataclass
class DocFields:
    """One source of truth: what is printed *is* what is expected back.

    Each attribute maps 1:1 onto a field of ``SupportingDocumentCandidates``.
    ``None`` means the value is deliberately absent from the page, so a correct
    extraction must return null for it rather than inventing one.
    """

    document_number: str | None = None
    issue_date: str | None = None
    expiry_date: str | None = None
    exporter_or_applicant: str | None = None
    buyer_or_beneficiary: str | None = None
    invoice_reference: str | None = None
    contract_reference: str | None = None
    pct_code: str | None = None
    product_or_commodity: str | None = None
    destination_country: str | None = None
    issuing_authority: str | None = None
    bank_name: str | None = None
    amount: str | None = None
    currency: str | None = None
    percentage: str | None = None
    quantity: str | None = None
    shipment_deadline: str | None = None
    related_reference: str | None = None
    treatment_or_inspection: str | None = None

    def printed(self) -> dict[str, str]:
        return {
            item.name: getattr(self, item.name)
            for item in dataclass_fields(self)
            if getattr(self, item.name) is not None
        }

    def expected(self) -> dict[str, str | None]:
        return {item.name: getattr(self, item.name) for item in dataclass_fields(self)}


# Printed label per field. A label is never part of the value - the extraction
# prompt states this explicitly and DEF-003 regression-tests it.
_BASE_LABELS: dict[str, str] = {
    "document_number": "Document Number",
    "issue_date": "Issue Date",
    "expiry_date": "Expiry Date",
    "exporter_or_applicant": "Exporter",
    "buyer_or_beneficiary": "Buyer / Consignee",
    "invoice_reference": "Invoice Number",
    "contract_reference": "Contract Reference",
    "pct_code": "PCT Code",
    "product_or_commodity": "Commodity",
    "destination_country": "Destination Country",
    "issuing_authority": "Issuing Authority",
    "bank_name": "Bank",
    "amount": "Amount",
    "currency": "Currency",
    "percentage": "Deposit Percentage",
    "quantity": "Quantity",
    "shipment_deadline": "Latest Shipment Date",
    "related_reference": "Related Reference",
    "treatment_or_inspection": "Treatment / Inspection",
}

# The printed title drives document-type detection, so it is kept plain and
# unambiguous; qualifiers (CPFTA, non-preferential) go in a separate line.
_TYPE_TITLES: dict[str, str] = {
    "form_e_or_psw_export_declaration": "FORM E EXPORT DECLARATION",
    "certificate_of_origin": "CERTIFICATE OF ORIGIN",
    "sbp_deposit_proof": "SBP DEPOSIT PROOF",
    "sbp_confirmation": "SBP CONFIRMATION",
    "irrevocable_letter_of_credit": "IRREVOCABLE LETTER OF CREDIT",
    "phytosanitary_certificate": "PHYTOSANITARY CERTIFICATE",
    "importing_country_permit": "IMPORT PERMIT",
    "goods_declaration": "GOODS DECLARATION",
    "bill_of_lading": "BILL OF LADING",
    "export_contract": "EXPORT CONTRACT",
}

_TYPE_LABEL_OVERRIDES: dict[str, dict[str, str]] = {
    "form_e_or_psw_export_declaration": {"document_number": "Form E Number"},
    "certificate_of_origin": {"document_number": "Certificate Number"},
    "sbp_deposit_proof": {"document_number": "Deposit Receipt Number"},
    "sbp_confirmation": {
        "document_number": "Confirmation Number",
        "related_reference": "Deposit Receipt Referenced",
    },
    "irrevocable_letter_of_credit": {
        "document_number": "Letter of Credit Number",
        "exporter_or_applicant": "Beneficiary",
        "buyer_or_beneficiary": "Applicant",
        "amount": "Credit Amount",
    },
    "phytosanitary_certificate": {"document_number": "Certificate Number"},
    "importing_country_permit": {
        "document_number": "Permit Number",
        "exporter_or_applicant": "Applicant",
    },
    "goods_declaration": {"document_number": "Goods Declaration Number"},
    "bill_of_lading": {"document_number": "Bill of Lading Number"},
    "export_contract": {"document_number": "Contract Number"},
}


def _label(doc_type: str, field_name: str) -> str:
    return _TYPE_LABEL_OVERRIDES.get(doc_type, {}).get(
        field_name, _BASE_LABELS[field_name]
    )


# --------------------------------------------------------------------------- #
# Document + bundle specifications
# --------------------------------------------------------------------------- #
@dataclass
class SupportingDocSpec:
    """One supporting document plus its independently declared expectation."""

    claimed_type: str
    fields: DocFields
    #: Printed title override, e.g. for the "wrong document type" scenario.
    printed_title: str | None = None
    subtitle: str | None = None
    #: Extra printed lines that carry no extractable field (context only).
    notes: tuple[str, ...] = ()
    # ---- hand-declared expectations -------------------------------------- #
    expected_detected_type: str | None = ""
    #: ``None`` means "deliberately not asserted" - used where the outcome
    #: legitimately depends on how much a degraded scan recovers. The
    #: *content status* is still asserted, because that is the safety property.
    expected_state: str | None = "shipment_matched"
    expected_content_status: str = "passed"
    expected_failed_checks: tuple[str, ...] = ()
    expected_manual_review_checks: tuple[str, ...] = ()
    expected_required_action: str | None = None
    expected_counts_as_present: bool | None = True
    #: Render an unreadable (noise) page instead of the field block.
    unreadable: bool = False
    #: Rasterization DPI for the scanned variant; low values degrade OCR.
    scanned_dpi: int = 150
    #: True when the caller names the type but uploads nothing.
    claimed_only: bool = False

    @property
    def title(self) -> str:
        return self.printed_title or _TYPE_TITLES[self.claimed_type]

    @property
    def slug(self) -> str:
        return self.claimed_type


@dataclass
class SupportingBundle:
    """A set of supporting documents attached to one base shipment scenario."""

    key: str
    base_scenario: str
    title: str
    description: str
    injected_defect: str | None
    documents: list[SupportingDocSpec]
    #: Types named in the request without an uploaded UUID.
    claimed_only_types: list[str] = field(default_factory=list)
    # ---- hand-declared scenario expectations ------------------------------ #
    expected_overall_status: str = "passed"
    expected_human_review: bool = False
    expected_required_action: str | None = None
    #: Where the bundle is written; valid bundles live beside their scenario.
    inline_with_scenario: bool = False

    def folder(self) -> Path:
        if self.inline_with_scenario:
            return OUTPUT_ROOT / self.base_scenario / "supporting"
        return SUPPORTING_ROOT / self.key


def _doc_id(bundle_key: str, doc_slug: str, variant: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{bundle_key}:{doc_slug}:{variant}"))


# --------------------------------------------------------------------------- #
# Field builders derived from the base scenario (so everything agrees)
# --------------------------------------------------------------------------- #
def _scenarios_by_key() -> dict[str, ScenarioSpec]:
    return {scenario.key: scenario for scenario in build_scenarios()}


SCENARIOS = _scenarios_by_key()


def _ref(scenario_key: str) -> ScenarioSpec:
    return SCENARIOS[scenario_key]


def _primary_item(scenario: ScenarioSpec) -> Any:
    return scenario.items[0]


def _suffix(invoice_number: str) -> str:
    """Stable, obviously-synthetic serial derived from the invoice number.

    Traceable back to its shipment and impossible to mistake for a real
    government or bank reference.
    """
    return "".join(c for c in invoice_number if c.isalnum()).upper()


def form_e(scenario: ScenarioSpec, **overrides: Any) -> DocFields:
    item = _primary_item(scenario)
    base = DocFields(
        document_number=f"SYN-FORME-{_suffix(scenario.invoice_number)}",
        issue_date=scenario.invoice_date.isoformat(),
        exporter_or_applicant=scenario.exporter_name,
        buyer_or_beneficiary=scenario.buyer_name,
        invoice_reference=scenario.invoice_number,
        pct_code=item.pct_code,
        product_or_commodity=item.product_name,
        destination_country=scenario.destination_country,
        bank_name=SYNTHETIC_BANK,
        amount=_money(scenario.invoice_total()),
        currency=scenario.currency,
    )
    return _with(base, overrides)


def certificate_of_origin(scenario: ScenarioSpec, **overrides: Any) -> DocFields:
    item = _primary_item(scenario)
    base = DocFields(
        document_number=f"SYN-COO-{_suffix(scenario.invoice_number)}",
        issue_date=scenario.invoice_date.isoformat(),
        exporter_or_applicant=scenario.exporter_name,
        buyer_or_beneficiary=scenario.buyer_name,
        invoice_reference=scenario.invoice_number,
        pct_code=item.pct_code,
        product_or_commodity=item.product_name,
        destination_country=scenario.destination_country,
        issuing_authority="Lahore Chamber of Commerce and Industry",
        quantity=str(item.quantity),
    )
    return _with(base, overrides)


def sbp_deposit_proof(scenario: ScenarioSpec, **overrides: Any) -> DocFields:
    base = DocFields(
        document_number=f"SYN-SBPDEP-{_suffix(scenario.invoice_number)}",
        issue_date=scenario.invoice_date.isoformat(),
        exporter_or_applicant=scenario.exporter_name,
        invoice_reference=scenario.invoice_number,
        issuing_authority="State Bank of Pakistan",
        bank_name=SYNTHETIC_BANK,
        amount=_money(scenario.invoice_total() / Decimal("100")),
        currency=scenario.currency,
        percentage="1",
    )
    return _with(base, overrides)


def sbp_confirmation(scenario: ScenarioSpec, **overrides: Any) -> DocFields:
    base = DocFields(
        document_number=f"SYN-SBPCONF-{_suffix(scenario.invoice_number)}",
        issue_date=scenario.invoice_date.isoformat(),
        exporter_or_applicant=scenario.exporter_name,
        invoice_reference=scenario.invoice_number,
        issuing_authority="State Bank of Pakistan",
        bank_name=SYNTHETIC_BANK,
        related_reference=f"SYN-SBPDEP-{_suffix(scenario.invoice_number)}",
    )
    return _with(base, overrides)


def letter_of_credit(scenario: ScenarioSpec, **overrides: Any) -> DocFields:
    issued = scenario.letter_of_credit_date or scenario.invoice_date
    base = DocFields(
        document_number=f"SYN-LC-{_suffix(scenario.invoice_number)}",
        issue_date=issued.isoformat(),
        expiry_date=(scenario.shipment_date + timedelta(days=60)).isoformat(),
        exporter_or_applicant=scenario.exporter_name,
        buyer_or_beneficiary=scenario.buyer_name,
        invoice_reference=scenario.invoice_number,
        bank_name=SYNTHETIC_BANK,
        amount=_money(scenario.invoice_total()),
        currency=scenario.currency,
        shipment_deadline=(scenario.shipment_date + timedelta(days=30)).isoformat(),
    )
    return _with(base, overrides)


def phytosanitary(scenario: ScenarioSpec, **overrides: Any) -> DocFields:
    item = _primary_item(scenario)
    base = DocFields(
        document_number=f"SYN-PHYTO-{_suffix(scenario.invoice_number)}",
        issue_date=(scenario.shipment_date - timedelta(days=7)).isoformat(),
        exporter_or_applicant=scenario.exporter_name,
        invoice_reference=scenario.invoice_number,
        product_or_commodity=item.product_name,
        destination_country=scenario.destination_country,
        issuing_authority=(
            "Department of Plant Protection, Ministry of National Food "
            "Security and Research"
        ),
        quantity=f"{item.quantity} {item.unit}",
        treatment_or_inspection="Fumigation and visual inspection completed",
    )
    return _with(base, overrides)


def import_permit(scenario: ScenarioSpec, **overrides: Any) -> DocFields:
    item = _primary_item(scenario)
    base = DocFields(
        document_number=f"SYN-IMPPERM-{_suffix(scenario.invoice_number)}",
        issue_date=(scenario.shipment_date - timedelta(days=45)).isoformat(),
        expiry_date=(scenario.shipment_date + timedelta(days=180)).isoformat(),
        exporter_or_applicant=scenario.exporter_name,
        buyer_or_beneficiary=scenario.buyer_name,
        product_or_commodity=item.product_name,
        destination_country=scenario.destination_country,
        issuing_authority="Ministry of Climate Change and Environment",
    )
    return _with(base, overrides)


def goods_declaration(scenario: ScenarioSpec, **overrides: Any) -> DocFields:
    item = _primary_item(scenario)
    base = DocFields(
        document_number=f"SYN-GD-{_suffix(scenario.invoice_number)}",
        issue_date=scenario.shipment_date.isoformat(),
        exporter_or_applicant=scenario.exporter_name,
        buyer_or_beneficiary=scenario.buyer_name,
        invoice_reference=scenario.invoice_number,
        pct_code=item.pct_code,
        product_or_commodity=item.product_name,
        destination_country=scenario.destination_country,
        amount=_money(scenario.invoice_total()),
        currency=scenario.currency,
        quantity=f"{item.quantity} {item.unit}",
    )
    return _with(base, overrides)


def bill_of_lading(scenario: ScenarioSpec, **overrides: Any) -> DocFields:
    item = _primary_item(scenario)
    base = DocFields(
        document_number=f"SYN-BL-{_suffix(scenario.invoice_number)}",
        issue_date=scenario.shipment_date.isoformat(),
        exporter_or_applicant=scenario.exporter_name,
        buyer_or_beneficiary=scenario.buyer_name,
        invoice_reference=scenario.invoice_number,
        product_or_commodity=item.product_name,
        destination_country=scenario.destination_country,
        quantity=f"{item.package_count} CARTONS",
    )
    return _with(base, overrides)


def export_contract(scenario: ScenarioSpec, **overrides: Any) -> DocFields:
    item = _primary_item(scenario)
    base = DocFields(
        document_number=f"SYN-CONTRACT-{_suffix(scenario.invoice_number)}",
        issue_date=(scenario.invoice_date - timedelta(days=30)).isoformat(),
        exporter_or_applicant=scenario.exporter_name,
        buyer_or_beneficiary=scenario.buyer_name,
        contract_reference=f"SYN-CONTRACT-{_suffix(scenario.invoice_number)}",
        product_or_commodity=item.product_name,
        destination_country=scenario.destination_country,
        amount=_money(scenario.invoice_total()),
        currency=scenario.currency,
        quantity=f"{item.quantity} {item.unit}",
    )
    return _with(base, overrides)


def _with(base: DocFields, overrides: dict[str, Any]) -> DocFields:
    for name, value in overrides.items():
        if not hasattr(base, name):
            raise KeyError(f"Unknown supporting-document field: {name}")
        setattr(base, name, value)
    return base


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _draw_watermark(page: "pymupdf.Page") -> None:
    page.insert_text((50, 40), WATERMARK, fontsize=8, fontname="helv")
    page.insert_text((50, _PAGE_H - 40), WATERMARK, fontsize=8, fontname="helv")
    # Kept below the field block (which ends around y=360 for the longest
    # document) so the light grey overlay never sits on top of a value and
    # degrade OCR of the fields themselves.
    page.insert_textbox(
        pymupdf.Rect(60, 600, 540, 740),
        "SYNTHETIC\nTEST DOCUMENT",
        fontsize=44,
        fontname="helv",
        color=(0.88, 0.88, 0.88),
        align=pymupdf.TEXT_ALIGN_CENTER,
    )


def render_supporting_pdf(path: Path, spec: SupportingDocSpec) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
    _draw_watermark(page)

    if spec.unreadable:
        # A deliberately illegible page. Shaded block characters were tried
        # first and were a bad fixture: Tesseract confidently transcribed them
        # as hundreds of "2"s, which reads as a *readable* page carrying no
        # fields. Random pixel noise has no glyph-like structure at all, so the
        # page genuinely yields nothing and the correct behaviour - "this could
        # not be read, a person must look at it" - is what gets exercised.
        rng = random.Random(20260725)
        width, height = 470, 560
        noise = bytes(rng.getrandbits(8) for _ in range(width * height))
        page.insert_image(
            pymupdf.Rect(60, 120, 530, 680),
            pixmap=pymupdf.Pixmap(pymupdf.csGRAY, width, height, noise, 0),
        )
        doc.set_metadata(
            {
                "title": "Synthetic unreadable supporting document",
                "author": "Enterprise Customs Engine synthetic test factory",
                "subject": "Synthetic data for software testing only",
            }
        )
        doc.save(path, deflate=True)
        doc.close()
        return

    y = 78
    page.insert_text((50, y), spec.title, fontsize=15, fontname="helv")
    y += 22
    if spec.subtitle:
        page.insert_text((50, y), spec.subtitle, fontsize=9, fontname="helv")
        y += 20
    page.draw_line((50, y), (545, y), width=0.6)
    y += 22

    for name, value in spec.fields.printed().items():
        page.insert_text(
            (50, y), _label(spec.claimed_type, name), fontsize=9, fontname="helv"
        )
        page.insert_text((230, y), value, fontsize=9, fontname="helv")
        y += 18

    if spec.notes:
        y += 12
        page.draw_line((50, y), (545, y), width=0.4)
        y += 18
        for note in spec.notes:
            page.insert_text((50, y), note, fontsize=8, fontname="helv")
            y += 14

    doc.set_metadata(
        {
            "title": f"Synthetic {spec.title.title()}",
            "author": "Enterprise Customs Engine synthetic test factory",
            "subject": (
                "Synthetic data for software testing only; not a real trade, "
                "customs or payment document"
            ),
        }
    )
    doc.save(path, deflate=True)
    doc.close()


# --------------------------------------------------------------------------- #
# Valid bundles (Stage 2)
# --------------------------------------------------------------------------- #
_CHINA_TEXTILE_SCENARIOS = (
    "clean_cotton_yarn",
    "clean_denim_fabric",
    "clean_cotton_tshirts",
    "clean_bedsheets",
)


def _passing(claimed_type: str, fields: DocFields, **kwargs: Any) -> SupportingDocSpec:
    return SupportingDocSpec(
        claimed_type=claimed_type,
        fields=fields,
        expected_detected_type=claimed_type,
        expected_state="shipment_matched",
        expected_content_status="passed",
        expected_counts_as_present=True,
        **kwargs,
    )


def build_valid_bundles() -> list[SupportingBundle]:
    bundles: list[SupportingBundle] = []

    for key in _CHINA_TEXTILE_SCENARIOS:
        scenario = _ref(key)
        bundles.append(
            SupportingBundle(
                key=f"{key}_supporting",
                base_scenario=key,
                title=f"Complete supporting-document set for {key}",
                description=(
                    "Form-E, CPFTA certificate of origin, goods declaration, bill "
                    "of lading and export contract, all agreeing with the invoice."
                ),
                injected_defect=None,
                inline_with_scenario=True,
                documents=[
                    _passing(
                        "form_e_or_psw_export_declaration",
                        form_e(scenario),
                        subtitle="Pakistan Single Window export declaration (synthetic)",
                    ),
                    _passing(
                        "certificate_of_origin",
                        certificate_of_origin(scenario),
                        subtitle=(
                            "Preferential origin under the China-Pakistan Free "
                            "Trade Agreement (synthetic)"
                        ),
                    ),
                    _passing("goods_declaration", goods_declaration(scenario)),
                    _passing("bill_of_lading", bill_of_lading(scenario)),
                    _passing("export_contract", export_contract(scenario)),
                ],
                expected_overall_status="passed",
                expected_human_review=False,
            )
        )

    # Non-China destination: a general (non-preferential) certificate of origin.
    scenario = _ref("non_china_destination_coo")
    bundles.append(
        SupportingBundle(
            key="non_china_destination_coo_supporting",
            base_scenario="non_china_destination_coo",
            title="Supporting documents for a non-China destination",
            description=(
                "The CPFTA preferential certificate does not apply to Germany, so "
                "a general non-preferential certificate of origin is presented."
            ),
            injected_defect=None,
            inline_with_scenario=True,
            documents=[
                _passing(
                    "form_e_or_psw_export_declaration",
                    form_e(scenario),
                    subtitle="Pakistan Single Window export declaration (synthetic)",
                ),
                _passing(
                    "certificate_of_origin",
                    certificate_of_origin(scenario),
                    subtitle="General non-preferential certificate of origin (synthetic)",
                ),
                _passing("goods_declaration", goods_declaration(scenario)),
                _passing("bill_of_lading", bill_of_lading(scenario)),
                _passing("export_contract", export_contract(scenario)),
            ],
            expected_overall_status="passed",
            expected_human_review=False,
        )
    )

    # Raw cotton carries the full SRO 2486(I)/2025 document set.
    scenario = _ref("clean_raw_cotton")
    bundles.append(
        SupportingBundle(
            key="clean_raw_cotton_supporting",
            base_scenario="clean_raw_cotton",
            title="Complete raw-cotton supporting-document set",
            description=(
                "Every document SRO 2486(I)/2025 requires for raw cotton, plus the "
                "commercial records, all agreeing with the invoice."
            ),
            injected_defect=None,
            inline_with_scenario=True,
            documents=[
                _passing(
                    "form_e_or_psw_export_declaration",
                    form_e(scenario),
                    subtitle="Pakistan Single Window export declaration (synthetic)",
                ),
                _passing(
                    "sbp_deposit_proof",
                    sbp_deposit_proof(scenario),
                    subtitle="Proof of the 1% security deposit (synthetic)",
                ),
                _passing("sbp_confirmation", sbp_confirmation(scenario)),
                _passing("irrevocable_letter_of_credit", letter_of_credit(scenario)),
                _passing("phytosanitary_certificate", phytosanitary(scenario)),
                _passing("importing_country_permit", import_permit(scenario)),
                _passing("goods_declaration", goods_declaration(scenario)),
                _passing("bill_of_lading", bill_of_lading(scenario)),
                _passing("export_contract", export_contract(scenario)),
            ],
            expected_overall_status="passed",
            expected_human_review=False,
        )
    )
    return bundles


# --------------------------------------------------------------------------- #
# Error scenarios (Stage 3) - exactly one controlled defect each
# --------------------------------------------------------------------------- #
def build_error_bundles() -> list[SupportingBundle]:
    tshirts = _ref("clean_cotton_tshirts")
    cotton = _ref("clean_raw_cotton")
    bundles: list[SupportingBundle] = []

    def bundle(
        key: str,
        title: str,
        description: str,
        defect: str,
        base: str,
        documents: list[SupportingDocSpec],
        *,
        claimed_only_types: list[str] | None = None,
        overall: str = "failed",
        human_review: bool = False,
        action: str | None = None,
    ) -> SupportingBundle:
        return SupportingBundle(
            key=key,
            base_scenario=base,
            title=title,
            description=description,
            injected_defect=defect,
            documents=documents,
            claimed_only_types=claimed_only_types or [],
            expected_overall_status=overall,
            expected_human_review=human_review,
            expected_required_action=action,
        )

    # 1. Form-E claimed but no UUID provided.
    bundles.append(
        bundle(
            "supporting_form_e_claimed_without_upload",
            "Form-E named in the request but never uploaded",
            "The caller lists form_e as an uploaded document type but supplies no "
            "document UUID. A name is not evidence.",
            "form_e claimed as a string with no uploaded file",
            "clean_cotton_tshirts",
            [
                _passing("certificate_of_origin", certificate_of_origin(tshirts)),
                SupportingDocSpec(
                    claimed_type="form_e_or_psw_export_declaration",
                    fields=DocFields(),
                    claimed_only=True,
                    expected_detected_type="",
                    expected_state="claimed_only",
                    expected_content_status="failed",
                    expected_failed_checks=(
                        "supporting_form_e_or_psw_export_declaration_uploaded",
                    ),
                    expected_required_action=(
                        "Upload the form e or psw export declaration PDF."
                    ),
                    expected_counts_as_present=False,
                ),
            ],
            claimed_only_types=["form_e"],
            action="Upload the actual Form-E / PSW export declaration PDF.",
        )
    )

    # 2. Wrong document type uploaded as Form-E.
    bundles.append(
        bundle(
            "supporting_wrong_type_uploaded_as_form_e",
            "A bill of lading uploaded in place of the Form-E",
            "A readable, valid bill of lading is uploaded under the form_e slot. "
            "It must not satisfy the Form-E requirement.",
            "bill of lading uploaded as form_e",
            "clean_cotton_tshirts",
            [
                SupportingDocSpec(
                    claimed_type="form_e_or_psw_export_declaration",
                    fields=bill_of_lading(tshirts),
                    printed_title=_TYPE_TITLES["bill_of_lading"],
                    expected_detected_type="bill_of_lading",
                    expected_state="type_mismatch",
                    expected_content_status="failed",
                    expected_failed_checks=(
                        "supporting_form_e_or_psw_export_declaration_type",
                    ),
                    expected_required_action=(
                        "Upload the correct form e or psw export declaration."
                    ),
                    expected_counts_as_present=False,
                ),
                _passing("certificate_of_origin", certificate_of_origin(tshirts)),
            ],
            action="Upload the correct Form-E / PSW export declaration.",
        )
    )

    # 3-6. Certificate-of-origin content mismatches.
    coo_defects: list[tuple[str, str, str, dict[str, Any], str]] = [
        (
            "supporting_coo_wrong_invoice_number",
            "Certificate of origin quoting a different invoice",
            "wrong invoice reference on the certificate of origin",
            {"invoice_reference": "LCG-INV-2026-999"},
            "supporting_certificate_of_origin_invoice_reference_match",
        ),
        (
            "supporting_coo_wrong_exporter",
            "Certificate of origin naming a different exporter",
            "wrong exporter on the certificate of origin",
            {"exporter_or_applicant": "Sialkot Sports Textiles (Pvt.) Ltd."},
            "supporting_certificate_of_origin_exporter_match",
        ),
        (
            "supporting_coo_wrong_destination",
            "Certificate of origin naming a different destination",
            "wrong destination country on the certificate of origin",
            {"destination_country": "Vietnam"},
            "supporting_certificate_of_origin_destination_match",
        ),
        (
            "supporting_coo_wrong_pct_code",
            "Certificate of origin quoting a different PCT code",
            "wrong PCT code on the certificate of origin",
            {"pct_code": "6302.3110"},
            "supporting_certificate_of_origin_pct_match",
        ),
    ]
    for key, title, defect, override, failed_check in coo_defects:
        bundles.append(
            bundle(
                key,
                title,
                "Every other field agrees with the invoice, so exactly one check "
                "may fail.",
                defect,
                "clean_cotton_tshirts",
                [
                    _passing(
                        "form_e_or_psw_export_declaration", form_e(tshirts)
                    ),
                    SupportingDocSpec(
                        claimed_type="certificate_of_origin",
                        fields=certificate_of_origin(tshirts, **override),
                        expected_detected_type="certificate_of_origin",
                        expected_state="type_verified",
                        expected_content_status="failed",
                        expected_failed_checks=(failed_check,),
                        expected_required_action=(
                            "Correct the conflicting supporting document and re-submit."
                        ),
                        expected_counts_as_present=False,
                    ),
                ],
                action="Correct the certificate of origin and re-submit.",
            )
        )

    # 7. Missing certificate number.
    bundles.append(
        bundle(
            "supporting_coo_missing_certificate_number",
            "Certificate of origin with no certificate number printed",
            "The number is genuinely absent from the page, so it must stay null "
            "and become a manual review - never a repaired or invented value.",
            "certificate number absent from the certificate of origin",
            "clean_cotton_tshirts",
            [
                _passing("form_e_or_psw_export_declaration", form_e(tshirts)),
                SupportingDocSpec(
                    claimed_type="certificate_of_origin",
                    fields=certificate_of_origin(tshirts, document_number=None),
                    expected_detected_type="certificate_of_origin",
                    expected_state="type_verified",
                    expected_content_status="manual_review",
                    expected_manual_review_checks=(
                        "supporting_certificate_of_origin_required_fields",
                    ),
                    expected_required_action=(
                        "Confirm the flagged supporting-document values."
                    ),
                    expected_counts_as_present=True,
                ),
            ],
            overall="manual_review",
            human_review=True,
            action="Confirm the certificate number from the certificate of origin.",
        )
    )

    # 8. Expired importing-country permit.
    bundles.append(
        bundle(
            "supporting_expired_import_permit",
            "Importing-country permit that expired before shipment",
            "The permit is readable and matches the shipment, but its printed "
            "expiry date is in the past.",
            "import permit expiry date in the past",
            "clean_raw_cotton",
            [
                _passing("form_e_or_psw_export_declaration", form_e(cotton)),
                SupportingDocSpec(
                    claimed_type="importing_country_permit",
                    fields=import_permit(cotton, expiry_date="2025-01-31"),
                    expected_detected_type="importing_country_permit",
                    expected_state="type_verified",
                    expected_content_status="failed",
                    expected_failed_checks=(
                        "supporting_importing_country_permit_not_expired",
                    ),
                    expected_required_action=(
                        "Correct the conflicting supporting document and re-submit."
                    ),
                    expected_counts_as_present=False,
                ),
            ],
            action="Obtain a valid importing-country permit.",
        )
    )

    # 9. SBP deposit proof with the wrong percentage.
    bundles.append(
        bundle(
            "supporting_sbp_deposit_wrong_percentage",
            "SBP deposit proof stating 0.5% instead of the required 1%",
            "The document is genuine in form but records a deposit below the "
            "percentage the SRO requires.",
            "deposit percentage 0.5 instead of 1",
            "clean_raw_cotton",
            [
                _passing("form_e_or_psw_export_declaration", form_e(cotton)),
                SupportingDocSpec(
                    claimed_type="sbp_deposit_proof",
                    fields=sbp_deposit_proof(
                        cotton,
                        percentage="0.5",
                        amount=_money(cotton.invoice_total() / Decimal("200")),
                    ),
                    expected_detected_type="sbp_deposit_proof",
                    expected_state="type_verified",
                    expected_content_status="failed",
                    expected_failed_checks=("supporting_sbp_deposit_percentage",),
                    expected_required_action=(
                        "Correct the conflicting supporting document and re-submit."
                    ),
                    expected_counts_as_present=False,
                ),
            ],
            action="Deposit the full 1% security amount and upload corrected proof.",
        )
    )

    # 10. SBP confirmation referencing the wrong deposit receipt.
    bundles.append(
        bundle(
            "supporting_sbp_confirmation_wrong_deposit_reference",
            "SBP confirmation pointing at a different deposit receipt",
            "The confirmation quotes a deposit receipt number that does not match "
            "the deposit proof supplied with this shipment.",
            "related deposit reference does not match the deposit proof",
            "clean_raw_cotton",
            [
                _passing("form_e_or_psw_export_declaration", form_e(cotton)),
                _passing("sbp_deposit_proof", sbp_deposit_proof(cotton)),
                SupportingDocSpec(
                    claimed_type="sbp_confirmation",
                    fields=sbp_confirmation(
                        cotton, related_reference="SYN-SBPDEP-00000000"
                    ),
                    expected_detected_type="sbp_confirmation",
                    expected_state="type_verified",
                    expected_content_status="failed",
                    expected_failed_checks=(
                        "supporting_sbp_confirmation_related_deposit_match",
                    ),
                    expected_required_action=(
                        "Correct the conflicting supporting document and re-submit."
                    ),
                    expected_counts_as_present=False,
                ),
            ],
            action="Upload the confirmation that references this shipment's deposit.",
        )
    )

    # 11. Letter of credit with the wrong beneficiary.
    bundles.append(
        bundle(
            "supporting_lc_wrong_beneficiary",
            "Letter of credit issued in favour of a different beneficiary",
            "The credit is readable and in date, but its beneficiary is not the "
            "exporter on the invoice.",
            "letter-of-credit beneficiary is not the exporter",
            "clean_raw_cotton",
            [
                _passing("form_e_or_psw_export_declaration", form_e(cotton)),
                SupportingDocSpec(
                    claimed_type="irrevocable_letter_of_credit",
                    fields=letter_of_credit(
                        cotton,
                        exporter_or_applicant="Karachi Cotton Exports (Pvt.) Ltd.",
                    ),
                    expected_detected_type="irrevocable_letter_of_credit",
                    expected_state="type_verified",
                    expected_content_status="failed",
                    expected_failed_checks=(
                        "supporting_irrevocable_letter_of_credit_exporter_match",
                        "supporting_letter_of_credit_beneficiary_match",
                    ),
                    expected_required_action=(
                        "Correct the conflicting supporting document and re-submit."
                    ),
                    expected_counts_as_present=False,
                ),
            ],
            action="Obtain a letter of credit naming this exporter as beneficiary.",
        )
    )

    # 12. Letter of credit whose latest-shipment date has already passed.
    bundles.append(
        bundle(
            "supporting_lc_expired_shipment_deadline",
            "Letter of credit whose latest shipment date precedes the shipment",
            "The credit's printed latest-shipment date is before the declared "
            "shipment date, so the shipment cannot be presented under it.",
            "LC latest shipment date earlier than the shipment date",
            "clean_raw_cotton",
            [
                _passing("form_e_or_psw_export_declaration", form_e(cotton)),
                SupportingDocSpec(
                    claimed_type="irrevocable_letter_of_credit",
                    fields=letter_of_credit(
                        cotton,
                        shipment_deadline=(
                            cotton.shipment_date - timedelta(days=10)
                        ).isoformat(),
                    ),
                    expected_detected_type="irrevocable_letter_of_credit",
                    expected_state="type_verified",
                    expected_content_status="failed",
                    expected_failed_checks=(
                        "supporting_letter_of_credit_shipment_deadline",
                    ),
                    expected_required_action=(
                        "Correct the conflicting supporting document and re-submit."
                    ),
                    expected_counts_as_present=False,
                ),
            ],
            action="Amend the letter of credit's latest shipment date.",
        )
    )

    # 13. Phytosanitary certificate for a different commodity.
    bundles.append(
        bundle(
            "supporting_phytosanitary_wrong_commodity",
            "Phytosanitary certificate covering a different commodity",
            "The certificate certifies rice, not the raw cotton being exported.",
            "phytosanitary commodity does not match the shipment",
            "clean_raw_cotton",
            [
                _passing("form_e_or_psw_export_declaration", form_e(cotton)),
                SupportingDocSpec(
                    claimed_type="phytosanitary_certificate",
                    fields=phytosanitary(
                        cotton, product_or_commodity="Milled rice, long grain"
                    ),
                    expected_detected_type="phytosanitary_certificate",
                    expected_state="type_verified",
                    expected_content_status="failed",
                    expected_failed_checks=(
                        "supporting_phytosanitary_commodity_match",
                    ),
                    expected_required_action=(
                        "Correct the conflicting supporting document and re-submit."
                    ),
                    expected_counts_as_present=False,
                ),
            ],
            action="Obtain a phytosanitary certificate covering raw cotton.",
        )
    )

    # 14. Low-confidence scanned supporting document.
    bundles.append(
        bundle(
            "supporting_low_confidence_scan",
            "Badly scanned certificate of origin",
            "The scan is legible enough to attempt OCR but not enough to trust. "
            "Uncertainty must route to a human, never to a legal failure or pass.",
            "certificate of origin rasterized at a degraded resolution",
            "clean_cotton_tshirts",
            [
                _passing("form_e_or_psw_export_declaration", form_e(tshirts)),
                SupportingDocSpec(
                    claimed_type="certificate_of_origin",
                    fields=certificate_of_origin(tshirts),
                    # How much a 55-DPI scan recovers is not deterministic, so
                    # the state and the presence contribution are deliberately
                    # left unasserted. What *is* asserted is the safety
                    # property: a bad read must produce manual_review - never a
                    # pass, and never a legal failure.
                    expected_detected_type=None,
                    expected_state=None,
                    expected_content_status="manual_review",
                    expected_manual_review_checks=(),
                    expected_required_action=None,
                    expected_counts_as_present=None,
                    scanned_dpi=55,
                ),
            ],
            overall="manual_review",
            human_review=True,
            action="Re-scan the certificate of origin, or confirm its values manually.",
        )
    )

    # 15. Completely unreadable supporting document.
    bundles.append(
        bundle(
            "supporting_unreadable_document",
            "Unreadable supporting document",
            "The uploaded PDF yields no usable text even after OCR. The system "
            "must say it could not read it, not guess what it was.",
            "supporting document with no recoverable text",
            "clean_cotton_tshirts",
            [
                _passing("form_e_or_psw_export_declaration", form_e(tshirts)),
                SupportingDocSpec(
                    claimed_type="certificate_of_origin",
                    fields=DocFields(),
                    unreadable=True,
                    expected_detected_type="",
                    expected_state="unreadable",
                    expected_content_status="manual_review",
                    expected_manual_review_checks=(
                        "supporting_certificate_of_origin_readable",
                    ),
                    expected_required_action=(
                        "Confirm the document contents manually."
                    ),
                    expected_counts_as_present=False,
                ),
            ],
            overall="manual_review",
            human_review=True,
            action="Upload a readable copy of the certificate of origin.",
        )
    )
    return bundles


def build_bundles() -> list[SupportingBundle]:
    return build_valid_bundles() + build_error_bundles()


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def _document_entry(
    bundle: SupportingBundle, spec: SupportingDocSpec, folder: Path
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "claimed_document_type": spec.claimed_type,
        "uploaded": not spec.claimed_only,
        "expected_detected_type": spec.expected_detected_type or None,
        "expected_state": spec.expected_state,
        "expected_content_status": spec.expected_content_status,
        "expected_failed_checks": list(spec.expected_failed_checks),
        "expected_manual_review_checks": list(spec.expected_manual_review_checks),
        "expected_required_action": spec.expected_required_action,
        "expected_authenticity_status": "not_externally_verified",
        "expected_counts_as_present": spec.expected_counts_as_present,
        "expected_fields": spec.fields.expected(),
    }
    if spec.claimed_only:
        entry["text_pdf"] = None
        entry["scanned_pdf"] = None
        entry["note"] = (
            "Deliberately not uploaded: the request names this type but supplies "
            "no document UUID."
        )
        return entry

    text_path = folder / f"supporting_{spec.slug}_text.pdf"
    scanned_path = folder / f"supporting_{spec.slug}_scanned.pdf"
    render_supporting_pdf(text_path, spec)
    rasterize_to_scanned_pdf(text_path, scanned_path, dpi=spec.scanned_dpi)

    entry["text_pdf"] = str(text_path.relative_to(OUTPUT_ROOT))
    entry["scanned_pdf"] = str(scanned_path.relative_to(OUTPUT_ROOT))
    entry["scanned_dpi"] = spec.scanned_dpi
    entry["placeholder_document_id"] = {
        "text": _doc_id(bundle.key, spec.slug, "text"),
        "scanned": _doc_id(bundle.key, spec.slug, "scanned"),
    }
    return entry


def generate_bundle(bundle: SupportingBundle) -> Path:
    folder = bundle.folder()
    folder.mkdir(parents=True, exist_ok=True)
    scenario = _ref(bundle.base_scenario)

    documents = [_document_entry(bundle, spec, folder) for spec in bundle.documents]

    expected = {
        "bundle_key": bundle.key,
        "title": bundle.title,
        "description": bundle.description,
        "injected_defect": bundle.injected_defect,
        "base_scenario": bundle.base_scenario,
        "base_scenario_documents": {
            "commercial_invoice_text": (
                f"{bundle.base_scenario}/synthetic_commercial_invoice_text.pdf"
            ),
            "packing_list_text": (
                f"{bundle.base_scenario}/synthetic_packing_list_text.pdf"
            ),
            "commercial_invoice_scanned": (
                f"{bundle.base_scenario}/synthetic_commercial_invoice_scanned.pdf"
            ),
            "packing_list_scanned": (
                f"{bundle.base_scenario}/synthetic_packing_list_scanned.pdf"
            ),
        },
        "shipment": {
            "exporter": scenario.exporter_name,
            "buyer": scenario.buyer_name,
            "invoice_number": scenario.invoice_number,
            "destination_country": scenario.destination_country,
            "shipment_date": scenario.shipment_date.isoformat(),
            "pct_code": _primary_item(scenario).pct_code,
            "product": _primary_item(scenario).product_name,
            "invoice_total": _money(scenario.invoice_total()),
            "currency": scenario.currency,
        },
        "claimed_only_document_types": bundle.claimed_only_types,
        "expected_overall_status": bundle.expected_overall_status,
        "expected_human_review": bundle.expected_human_review,
        "expected_required_action": bundle.expected_required_action,
        "documents": documents,
        "expected_note": (
            "Hand-declared from this bundle's stated purpose and Pakistan export "
            "rules. Produced without importing or calling the compliance engine "
            "or the supporting-document verifier, so it can grade them."
        ),
    }
    (folder / "supporting_documents_expected.json").write_text(
        json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (folder / "supporting_api_request.json").write_text(
        json.dumps(build_request_json(bundle), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return folder


def build_request_json(bundle: SupportingBundle) -> dict[str, Any]:
    scenario = _ref(bundle.base_scenario)
    return {
        "commercial_invoice_document_id": "<replace-with-uploaded-invoice-uuid>",
        "packing_list_document_id": "<replace-with-uploaded-packing-list-uuid>",
        "supporting_documents": [
            {
                "document_type": spec.claimed_type,
                "document_id": f"<replace-with-uploaded-{spec.slug}-uuid>",
            }
            for spec in bundle.documents
            if not spec.claimed_only
        ],
        "additional_uploaded_document_types": bundle.claimed_only_types,
        "shipment_date": scenario.shipment_date.isoformat(),
        "letter_of_credit_date": (
            scenario.letter_of_credit_date.isoformat()
            if scenario.letter_of_credit_date
            else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        help="Generate only this bundle key (may be repeated).",
    )
    args = parser.parse_args()

    bundles = build_bundles()
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {b.key for b in bundles}
        if unknown:
            raise SystemExit(f"Unknown bundle key(s): {', '.join(sorted(unknown))}")
        bundles = [b for b in bundles if b.key in wanted]

    total_pdfs = 0
    for bundle in bundles:
        folder = generate_bundle(bundle)
        uploaded = sum(1 for spec in bundle.documents if not spec.claimed_only)
        total_pdfs += uploaded * 2
        print(f"Generated {bundle.key} -> {folder} ({uploaded * 2} PDFs)")
    print(f"{len(bundles)} bundles, {total_pdfs} supporting-document PDFs")


if __name__ == "__main__":
    main()
