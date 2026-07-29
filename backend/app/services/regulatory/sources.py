"""Curated, filtered discovery of official documents relevant to the 5 products.

Only documents relevant to the five textile MVP PCT codes are ingested. The
large 93-page Export Policy Order is filtered page-by-page to textile-relevant
pages, so the complete regulatory archive is never ingested wholesale.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
REG = PROJECT_ROOT / "regulatory_data"

ALL_CODES = ["52010090", "52051100", "52094200", "61091000", "63023110"]
NON_RAW_COTTON = ["52051100", "52094200", "61091000", "63023110"]

# A base-order page is kept only if it mentions cotton/textiles or a covered code.
_TEXTILE_RELEVANCE = re.compile(
    r"cotton|textile|5201|5205|5209|6109|6302|Sr\.?\s*No\.?\s*9|Schedule[-\s]*II",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RegulatorySource:
    key: str
    kind: str  # "text_file" | "pdf_pages" | "product_requirements_json"
    path: Path
    source_document: str
    issuing_authority: str
    document_type: str
    validation_status: str
    source_url: str | None = None
    sro_number: str | None = None
    default_pct_codes: list[str] = field(default_factory=list)
    issue_date: date | None = None
    effective_date: date | None = None
    #: Provenance kind, recorded rather than guessed at display time.
    source_kind: str = "unclassified_source"
    #: "current" | "superseded" | "historical_reference"
    currency_status: str = "current"
    #: The textile page filter exists for the 93-page Export Policy Order, of
    #: which only a minority of pages concern textiles. A document that is
    #: wholly on-topic (a PSW export-declaration manual) must not be filtered
    #: by it, or nearly every page is discarded for not saying "cotton".
    restrict_to_textile_pages: bool = True


@dataclass(frozen=True)
class SourceUnit:
    text: str
    page_number: int | None
    section: str | None
    pct_codes: list[str]


def discover_sources() -> list[RegulatorySource]:
    """Return the curated, textile-relevant official document set."""
    return [
        RegulatorySource(
            key="sro_2486_2025_raw_cotton",
            kind="text_file",
            path=REG
            / "processed/commerce/export_policy/validated_text/"
            "sro_2486_i_2025_amendment_to_export_policy_order_2022_validated.txt",
            source_document="SRO 2486(I)/2025 — amendment to Export Policy Order, 2022",
            issuing_authority="Government of Pakistan, Ministry of Commerce",
            document_type="export_policy_amendment",
            validation_status="verified",
            source_url="https://www.commerce.gov.pk/sros/",
            sro_number="2486(I)/2025",
            default_pct_codes=["52010090"],
            issue_date=date(2025, 12, 23),
            source_kind="official_regulation",
            currency_status="current",
        ),
        RegulatorySource(
            key="epo_2022_base_order",
            kind="pdf_pages",
            path=REG
            / "raw/commerce/export_policy/base_order/"
            "export_policy_order_2022_sro_544_i_2022.pdf",
            source_document="Export Policy Order, 2022 — SRO 544(I)/2022",
            issuing_authority="Government of Pakistan, Ministry of Commerce",
            document_type="export_policy_order",
            validation_status="partially_verified",
            source_url=(
                "https://www.commerce.gov.pk/wp-content/uploads/2022/04/"
                "EPO-2022-SRO-544-2022-dt.22.4.22.pdf"
            ),
            sro_number="544(I)/2022",
            default_pct_codes=ALL_CODES,
            issue_date=date(2022, 4, 22),
            source_kind="official_policy",
            currency_status="current",
        ),
        RegulatorySource(
            key="tipp_textile_product_requirements",
            kind="product_requirements_json",
            path=REG
            / "raw/psw/textile_product_requirements/textile_product_requirements.json",
            source_document="PSW/TIPP textile product export requirements (curated)",
            issuing_authority="Pakistan Single Window / TIPP (curated)",
            document_type="product_requirements_structured",
            validation_status="partially_verified",
            source_url="https://tipp.gov.pk/index.php?id=173&r=searchProcedure%2Fview1",
            default_pct_codes=ALL_CODES,
            source_kind="curated_rule_summary",
            currency_status="current",
        ),
        # --- Informational sources for the regulatory assistant --------------
        #
        # These three carry no PCT tags and are never a compliance basis: the
        # deterministic engine reads its rules from the compliance rule files,
        # not from the retrieval corpus. They are here because the assistant is
        # asked about PSW export declarations, electronic certificates of
        # origin and export procedures, and until now the corpus held nothing
        # on any of them - every such question returned "no relevant evidence"
        # while the PDF that answers it sat unindexed on disk.
        #
        # All three are digital PDFs with directly extractable text (no OCR
        # stage, so none of the unresolved OCR defects that block the other
        # Export Policy Order amendments), are marked "included" in
        # document_manifest.json, and are wholly on-topic - so the textile page
        # filter, which exists for the mostly-non-textile base order, is off.
        RegulatorySource(
            key="psw_single_declaration_exports_manual",
            kind="pdf_pages",
            path=REG
            / "raw/psw/single_declaration_export/"
            "psw_user_manual_single_declaration_exports.pdf",
            source_document="PSW User Manual - Single Declaration (Exports)",
            issuing_authority="Pakistan Single Window",
            document_type="psw_user_manual",
            validation_status="partially_verified",
            source_url="https://psw.gov.pk/media/Manuals/PSW-User-Manual-SD-Exports.pdf",
            default_pct_codes=[],
            source_kind="official_manual",
            currency_status="current",
            restrict_to_textile_pages=False,
        ),
        RegulatorySource(
            key="psw_tdap_electronic_certificate_of_origin_manual",
            kind="pdf_pages",
            path=REG
            / "raw/psw/user_manuals/tdap/"
            "psw_tdap_electronic_certificate_of_origin_form_issuance_traders_process"
            "_user_manual.pdf",
            source_document=(
                "PSW User Manual - TDAP Electronic Certificate of Origin / Form "
                "Issuance (Traders Process)"
            ),
            issuing_authority="Pakistan Single Window",
            document_type="psw_user_manual",
            validation_status="partially_verified",
            # No official URL is recorded for this file in the acquisition
            # manifest. Left unset rather than guessed at.
            source_url=None,
            default_pct_codes=[],
            source_kind="official_manual",
            currency_status="current",
            restrict_to_textile_pages=False,
        ),
        RegulatorySource(
            key="tdap_new_exporters_guide_part_a",
            kind="pdf_pages",
            path=REG
            / "raw/tdap/export_document_guides/"
            "tdap_new_exporters_guide_part_a_export_procedures_2020.pdf",
            source_document=(
                "TDAP Step-by-Step Guide for New Exporters - Part A: Export "
                "Procedures (2020)"
            ),
            issuing_authority=(
                "Trade Development Authority of Pakistan, Ministry of Commerce"
            ),
            document_type="tdap_exporter_guide",
            validation_status="partially_verified",
            source_url=None,
            default_pct_codes=[],
            issue_date=date(2020, 11, 1),
            source_kind="official_manual",
            # document_manifest.json records this as dated and non-authoritative
            # for current product-specific compliance. Kept as clearly labelled
            # historical guidance rather than passed off as current procedure.
            currency_status="historical_reference",
            restrict_to_textile_pages=False,
        ),
    ]


def _iter_text_file(source: RegulatorySource) -> Iterator[SourceUnit]:
    text = source.path.read_text(encoding="utf-8")
    yield SourceUnit(
        text=text,
        page_number=1,
        section=source.sro_number and f"SRO {source.sro_number}",
        pct_codes=list(source.default_pct_codes),
    )


def _iter_pdf_pages(source: RegulatorySource) -> Iterator[SourceUnit]:
    import fitz  # type: ignore[import-untyped]  # PyMuPDF (project dependency)

    document = fitz.open(source.path)
    try:
        for index in range(document.page_count):
            page = document[index]
            page_text = page.get_text()
            if not page_text or not page_text.strip():
                continue  # no extractable text on this page
            if source.restrict_to_textile_pages and not _TEXTILE_RELEVANCE.search(
                page_text
            ):
                continue  # relevance filter: skip non-textile pages
            yield SourceUnit(
                text=page_text,
                page_number=index + 1,
                section=f"page {index + 1}",
                pct_codes=list(source.default_pct_codes),
            )
    finally:
        document.close()


def _render_product(product: dict) -> str:
    lines: list[str] = []
    lines.append(f"Product: {product.get('simple_product_name')} "
                 f"(PCT {product.get('display_pct_code')}, {product.get('pct_code')}).")
    export_status = product.get("export_status", {})
    lines.append(f"Export status: {export_status.get('value')} — {export_status.get('note', '')}".strip())
    for key, label in (
        ("licence_required", "Licence"),
        ("permit_required", "Permit"),
        ("certificate_required", "Certificate"),
        ("approval_required", "Approval"),
    ):
        req = product.get(key) or {}
        details = req.get("details") or req.get("verification_status") or req.get("value")
        lines.append(f"{label} required: value={req.get('value')}; {details}")
    conditions = product.get("conditions") or []
    if conditions:
        lines.append("Conditions:")
        lines.extend(f"- {c}" for c in conditions)
    legal_basis = product.get("legal_basis") or []
    if legal_basis:
        lines.append("Legal basis: " + "; ".join(legal_basis))
    urls = product.get("source_urls") or []
    if urls:
        lines.append("Official sources: " + "; ".join(urls))
    return "\n".join(lines)


def _iter_product_requirements(source: RegulatorySource) -> Iterator[SourceUnit]:
    data = json.loads(source.path.read_text(encoding="utf-8"))

    # Common export clearance (Form-E, commercial invoice, packing list).
    common = data["common_export_clearance"]
    common_text = (
        "Common export clearance (all textile products).\n"
        f"Responsible agency: {common['responsible_government_agency']}.\n"
        f"Procedure: {common['procedure']}.\n"
        "Supporting documents required for export clearance:\n"
        + "\n".join(f"- {d}" for d in common["supporting_documents"])
        + f"\nOfficial source: {common['source_url']}"
    )
    yield SourceUnit(common_text, None, "common_export_clearance", list(ALL_CODES))

    # Conditional certificate of origin (China / other destinations).
    coo = data["conditional_certificate_of_origin"]
    coo_lines = [
        "Conditional certificate of origin for textile exports.",
        f"Responsible agency: {coo['responsible_government_agency']}.",
        f"Requirement: {coo['requirement']}",
        f"Applies to selected codes: {', '.join(coo['applies_to_selected_codes'])}.",
    ]
    for procedure in coo["destination_procedures"]:
        coo_lines.append(
            f"- {procedure['destination_or_scheme']}: certificate_required="
            f"{procedure['certificate_required']}; source {procedure.get('source_url')}"
        )
    yield SourceUnit("\n".join(coo_lines), None, "conditional_certificate_of_origin", list(NON_RAW_COTTON))

    # One unit per product.
    for product in data["products"]:
        yield SourceUnit(
            _render_product(product),
            None,
            f"product {product.get('pct_code')}",
            [product.get("pct_code")],
        )


def iter_units(source: RegulatorySource) -> Iterator[SourceUnit]:
    if source.kind == "text_file":
        yield from _iter_text_file(source)
    elif source.kind == "pdf_pages":
        yield from _iter_pdf_pages(source)
    elif source.kind == "product_requirements_json":
        yield from _iter_product_requirements(source)
    else:  # pragma: no cover - guarded by discover_sources
        raise ValueError(f"Unknown source kind: {source.kind}")
