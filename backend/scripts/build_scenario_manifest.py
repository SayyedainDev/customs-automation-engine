"""Build the INDEPENDENT scenario manifest used to grade live evaluation runs.

Independence contract (enforced by tests):

* This module must never import or call the deterministic compliance engine,
  the executable-rule evaluator, the item matcher, or the multi-line service.
  Expected statuses are **hand-declared** below from each scenario's stated
  purpose, not computed by the software under test.
* Factual expectations (names, numbers, weights) come from the explicit
  scenario definitions in ``generate_synthetic_test_bundles.build_scenarios``
  and are then **cross-validated against the actual text of the generated
  PDFs**, so a divergence between the definition and the shipped document is
  reported rather than silently trusted.

Run:
    python -m scripts.build_scenario_manifest
"""

from __future__ import annotations

import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# NOTE: only the scenario *definitions* are imported. Importing any compliance
# module here would break the independence contract above.
from scripts.generate_supporting_documents import (  # noqa: E402
    SupportingBundle,
    build_bundles,
)
from scripts.generate_synthetic_test_bundles import (  # noqa: E402
    LineItemSpec,
    ScenarioSpec,
    build_scenarios,
)

FACTORY_ROOT = PROJECT_ROOT / "synthetic_factory"
MANIFEST_PATH = FACTORY_ROOT / "scenario_manifest.json"
VALIDATION_REPORT_PATH = (
    PROJECT_ROOT / "backend" / "reports" / "scenario_manifest_validation.md"
)

# Documents the deterministic engine treats as universally required for export
# clearance (commercial invoice + packing list are implicit in the upload pair).
_ALWAYS_REQUIRED = ("form_e",)


# --------------------------------------------------------------------------- #
# Hand-declared behavioural expectations.
#
# Each entry is written from the scenario's stated purpose and Pakistan export
# rules, NOT from running the engine. `expected_primary_status` is the status a
# correct implementation must return for the TEXT variant.
# --------------------------------------------------------------------------- #
BEHAVIOUR: dict[str, dict[str, Any]] = {
    "clean_raw_cotton": {
        "purpose": (
            "A fully compliant raw-cotton export: every SRO 2486(I)/2025 "
            "condition is satisfied and shipment is inside the 180-day window."
        ),
        "expected_primary_status": "passed",
        "expected_failed_checks": [],
        "expected_manual_review_checks": [],
        "expected_human_review": False,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "All required documents (Form-E, phytosanitary certificate, SBP "
            "deposit proof, SBP confirmation, irrevocable LC, import permit) are "
            "supplied; arithmetic and weights agree; 152 days elapsed since the "
            "letter of credit, inside the 180-day limit."
        ),
    },
    "clean_cotton_yarn": {
        "purpose": "A fully compliant cotton-yarn export to China under CPFTA.",
        "expected_primary_status": "passed",
        "expected_failed_checks": [],
        "expected_manual_review_checks": [],
        "expected_human_review": False,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "Form-E and a certificate of origin are supplied for a China "
            "destination; arithmetic and weights agree."
        ),
    },
    "clean_denim_fabric": {
        "purpose": "A fully compliant denim-fabric export to China under CPFTA.",
        "expected_primary_status": "passed",
        "expected_failed_checks": [],
        "expected_manual_review_checks": [],
        "expected_human_review": False,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "Form-E and a certificate of origin are supplied for a China "
            "destination; arithmetic and weights agree."
        ),
    },
    "clean_cotton_tshirts": {
        "purpose": (
            "A fully compliant cotton T-shirt export to China with weights "
            "printed on the invoice line itself."
        ),
        "expected_primary_status": "passed",
        "expected_failed_checks": [],
        "expected_manual_review_checks": [],
        "expected_human_review": False,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "Form-E and a certificate of origin are supplied; the invoice line "
            "carries its own weights so no fallback is needed."
        ),
    },
    "clean_bedsheets": {
        "purpose": "A fully compliant cotton bed-sheet export to China under CPFTA.",
        "expected_primary_status": "passed",
        "expected_failed_checks": [],
        "expected_manual_review_checks": [],
        "expected_human_review": False,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "Form-E and a certificate of origin are supplied for a China "
            "destination; arithmetic and weights agree."
        ),
    },
    "single_line_declared_total_fallback": {
        "purpose": (
            "A real single-line invoice that prints the weight only once as a "
            "document-level declared total, exercising the deterministic "
            "single_line_declared_total fallback."
        ),
        "expected_primary_status": "passed",
        "expected_failed_checks": [],
        "expected_manual_review_checks": [],
        "expected_human_review": False,
        "expected_fallback_runs": True,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "The invoice line has no printed weight, so the verified "
            "document-level declared totals (75.00 / 80.00 KG) are mapped onto "
            "the single line with derivation_method=single_line_declared_total. "
            "After the fallback the invoice and packing-list weights agree."
        ),
    },
    "multi_line_shipment": {
        "purpose": (
            "Three products on one invoice/packing-list pair, confirming "
            "per-item matching and that the single-line weight fallback does "
            "NOT fire for a multi-line invoice."
        ),
        "expected_primary_status": "passed",
        "expected_failed_checks": [],
        "expected_manual_review_checks": [],
        "expected_human_review": False,
        "expected_fallback_runs": False,
        "expected_item_matching": (
            "3 invoice lines match 3 packing items by line reference, in order"
        ),
        "outcome_explanation": (
            "Every line prints its own weight, so the fallback must not run. "
            "All three lines match by line reference and every arithmetic and "
            "weight check agrees."
        ),
    },
    "non_china_destination_coo": {
        "purpose": (
            "A cotton-yarn export to Germany, exercising the general TDAP "
            "certificate-of-origin path instead of the China/CPFTA path."
        ),
        "expected_primary_status": "passed",
        "expected_failed_checks": [],
        "expected_manual_review_checks": [],
        "expected_human_review": False,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "A certificate of origin is supplied, satisfying the destination "
            "requirement for a non-China market; the China/CPFTA-specific rule "
            "must resolve to not_applicable rather than passed."
        ),
    },
    "error_missing_form_e": {
        "purpose": "A positively-required export document is absent.",
        "expected_primary_status": "failed",
        "expected_failed_checks": ["required_document_form_e"],
        "expected_manual_review_checks": [],
        "expected_human_review": True,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "Form-E is required for export clearance and was not supplied. A "
            "missing positively-required document is a definite violation, not "
            "uncertainty, so this must be a hard failure."
        ),
    },
    "error_quantity_mismatch": {
        "purpose": "The invoice and packing list state conflicting quantities.",
        "expected_primary_status": "failed",
        "expected_failed_checks": ["item_quantity_match"],
        "expected_manual_review_checks": [],
        "expected_human_review": True,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "Invoice declares 100 PCS and the packing list declares 99 PCS. "
            "Both values were read confidently, so this is a confirmed "
            "cross-document conflict and a definite failure."
        ),
    },
    "error_arithmetic_mismatch": {
        "purpose": "Quantity x unit price does not equal the declared line total.",
        "expected_primary_status": "failed",
        "expected_failed_checks": ["item_line_calculation"],
        "expected_manual_review_checks": [],
        "expected_human_review": True,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "100 x 5.50 = 550.00 but the invoice declares 500.00. Pure "
            "arithmetic over confidently-read values, so a definite failure."
        ),
    },
    "error_weight_inversion": {
        "purpose": "Declared gross weight is lower than declared net weight.",
        "expected_primary_status": "failed",
        "expected_failed_checks": ["weight_consistency"],
        "expected_manual_review_checks": [],
        "expected_human_review": True,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "Net 80.00 KG and gross 75.00 KG are physically impossible. Both "
            "documents agree on these values, so it is a definite failure "
            "rather than an extraction uncertainty."
        ),
    },
    "error_raw_cotton_missing_sbp_deposit": {
        "purpose": (
            "A raw-cotton-specific required document under SRO 2486(I)/2025 is "
            "absent."
        ),
        "expected_primary_status": "failed",
        "expected_failed_checks": ["raw_cotton_sbp_deposit_proof"],
        "expected_manual_review_checks": [],
        "expected_human_review": True,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "Raw cotton requires proof of the 1% SBP security deposit. It was "
            "not supplied, so the deterministic engine must fail the shipment."
        ),
    },
    "error_raw_cotton_deadline_exceeded": {
        "purpose": "Raw cotton shipped outside the SRO 2486(I)/2025 180-day window.",
        "expected_primary_status": "failed",
        "expected_failed_checks": ["raw_cotton_shipment_within_180_days"],
        "expected_manual_review_checks": [],
        "expected_human_review": True,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "The letter of credit is dated 2026-01-01 and shipment occurred "
            "2026-07-15, which is 195 days later and past the 180-day legal "
            "limit. Both dates are supplied, so this is a definite failure."
        ),
    },
    "manual_review_unsupported_pct_code": {
        "purpose": (
            "The PCT code is well-formed but outside the curated textile MVP "
            "catalog."
        ),
        "expected_primary_status": "manual_review",
        "expected_failed_checks": [],
        "expected_manual_review_checks": ["mvp_pct_support"],
        "expected_human_review": True,
        "expected_fallback_runs": False,
        "expected_item_matching": "1 invoice line matches 1 packing item by line reference",
        "outcome_explanation": (
            "PCT code 9999.9999 does not appear in the curated MVP PCT list. "
            "Product-requirement and executable-rule checks are skipped for an "
            "unsupported code (nothing to evaluate them against), so the only "
            "non-passing check is mvp_pct_support itself; the deterministic "
            "engine correctly routes the shipment to manual review rather than "
            "guessing at applicable requirements."
        ),
    },
}


def _d(value: Decimal | int | str) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def _item_manifest(item: LineItemSpec) -> dict[str, Any]:
    return {
        "line_number": item.line_number,
        "product_name": item.product_name,
        "pct_code": item.pct_code,
        "quantity": str(item.quantity),
        "unit": item.unit,
        "unit_price": _d(item.unit_price),
        "line_total": _d(item.resolved_line_total()),
        "invoice_net_weight": (
            None if item.omit_invoice_line_weight else _d(item.net_weight)
        ),
        "invoice_gross_weight": (
            None if item.omit_invoice_line_weight else _d(item.gross_weight)
        ),
        "packing_quantity": str(item.resolved_packing_quantity()),
        "packing_net_weight": _d(item.resolved_packing_net_weight()),
        "packing_gross_weight": _d(item.resolved_packing_gross_weight()),
        "package_count": item.package_count,
    }


def build_manifest_entry(scenario: ScenarioSpec, variant: str) -> dict[str, Any]:
    behaviour = BEHAVIOUR[scenario.key]
    supplied = list(scenario.additional_uploaded_document_types)
    missing = [doc for doc in _ALWAYS_REQUIRED if doc not in supplied]
    if scenario.key == "error_raw_cotton_missing_sbp_deposit":
        missing = ["sbp_deposit_proof"]

    return {
        "scenario_id": scenario.key,
        "variant": variant,
        "title": scenario.title,
        "purpose": behaviour["purpose"],
        "pdf_variant": variant,
        "expected_exporter": scenario.exporter_name,
        "expected_buyer": scenario.buyer_name,
        "expected_invoice_number": scenario.invoice_number,
        "expected_invoice_date": scenario.invoice_date.isoformat(),
        "expected_destination": scenario.destination_country,
        "expected_currency": scenario.currency,
        "expected_invoice_total": _d(scenario.invoice_total()),
        "expected_line_item_count": len(scenario.items),
        "expected_products": [i.product_name for i in scenario.items],
        "expected_pct_codes": [i.pct_code for i in scenario.items],
        "expected_quantities": [str(i.quantity) for i in scenario.items],
        "expected_units": [i.unit for i in scenario.items],
        "expected_unit_prices": [_d(i.unit_price) for i in scenario.items],
        "expected_line_totals": [_d(i.resolved_line_total()) for i in scenario.items],
        "expected_item_net_weights": [
            None if i.omit_invoice_line_weight else _d(i.net_weight)
            for i in scenario.items
        ],
        "expected_item_gross_weights": [
            None if i.omit_invoice_line_weight else _d(i.gross_weight)
            for i in scenario.items
        ],
        "expected_total_net_weight": _d(scenario.declared_net_weight_total()),
        "expected_total_gross_weight": _d(scenario.declared_gross_weight_total()),
        "expected_package_count": sum(i.package_count for i in scenario.items),
        "expected_items": [_item_manifest(i) for i in scenario.items],
        "expected_supplied_document_types": supplied,
        "deliberately_missing_document_types": missing,
        "injected_defect": scenario.injected_defect,
        "shipment_date": scenario.shipment_date.isoformat(),
        "letter_of_credit_date": (
            scenario.letter_of_credit_date.isoformat()
            if scenario.letter_of_credit_date
            else None
        ),
        "expected_extraction_behaviour": (
            "Every header field and every line item must be extracted from the "
            "rasterized image via Tesseract OCR before structuring."
            if variant == "scanned"
            else "Every header field and every line item must be extracted from "
            "the embedded PDF text layer without OCR."
        ),
        "expected_item_matching": behaviour["expected_item_matching"],
        "expected_primary_status": behaviour["expected_primary_status"],
        "expected_failed_checks": behaviour["expected_failed_checks"],
        "expected_manual_review_checks": behaviour["expected_manual_review_checks"],
        "expected_ocr_runs": variant == "scanned",
        "expected_single_line_fallback_runs": behaviour["expected_fallback_runs"],
        "expected_human_review": behaviour["expected_human_review"],
        "outcome_explanation": behaviour["outcome_explanation"],
        "status_authority": "deterministic_python_engine_only",
    }


# --------------------------------------------------------------------------- #
# PDF cross-validation (evidence, not derivation)
# --------------------------------------------------------------------------- #
def _pdf_text(path: Path) -> str:
    document = pymupdf.open(path)
    text = "\n".join(
        document[page_number].get_text() for page_number in range(document.page_count)
    )
    document.close()
    return text


def _normalise_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def validate_entry_against_pdf(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Confirm every declared fact is literally visible in the generated PDF."""
    folder = FACTORY_ROOT / entry["scenario_id"]
    invoice_text = _normalise_spaces(_pdf_text(folder / "synthetic_commercial_invoice_text.pdf"))
    packing_text = _normalise_spaces(_pdf_text(folder / "synthetic_packing_list_text.pdf"))

    findings: list[dict[str, Any]] = []

    def check(label: str, needle: str, haystack: str, source: str) -> None:
        findings.append(
            {
                "field": label,
                "expected_value": needle,
                "source_document": source,
                "found_in_pdf": needle in haystack,
            }
        )

    check("expected_exporter", entry["expected_exporter"], invoice_text, "commercial invoice")
    check("expected_buyer", entry["expected_buyer"], invoice_text, "commercial invoice")
    check("expected_invoice_number", entry["expected_invoice_number"], invoice_text, "commercial invoice")
    check("expected_invoice_date", entry["expected_invoice_date"], invoice_text, "commercial invoice")
    check("expected_destination", entry["expected_destination"], invoice_text, "commercial invoice")
    check("expected_currency", entry["expected_currency"], invoice_text, "commercial invoice")
    check("expected_invoice_total", entry["expected_invoice_total"], invoice_text, "commercial invoice")
    check("expected_total_net_weight", entry["expected_total_net_weight"], invoice_text, "commercial invoice")
    check("expected_total_gross_weight", entry["expected_total_gross_weight"], invoice_text, "commercial invoice")
    check("expected_invoice_number (packing cross-reference)", entry["expected_invoice_number"], packing_text, "packing list")

    for index, item in enumerate(entry["expected_items"], start=1):
        check(f"item{index}.product_name", item["product_name"], invoice_text, "commercial invoice")
        check(f"item{index}.pct_code", item["pct_code"], invoice_text, "commercial invoice")
        check(f"item{index}.line_total", item["line_total"], invoice_text, "commercial invoice")
        check(f"item{index}.packing_quantity", item["packing_quantity"], packing_text, "packing list")
        check(f"item{index}.packing_net_weight", item["packing_net_weight"], packing_text, "packing list")
        if item["invoice_net_weight"] is None:
            findings.append(
                {
                    "field": f"item{index}.invoice_net_weight",
                    "expected_value": "(absent from the invoice line by design)",
                    "source_document": "commercial invoice",
                    "found_in_pdf": True,
                    "note": (
                        "Declared only as a document-level total; the "
                        "single-line fallback must supply the line value."
                    ),
                }
            )
        else:
            check(f"item{index}.invoice_net_weight", item["invoice_net_weight"], invoice_text, "commercial invoice")

    return findings


def validate_supporting_entry_against_pdf(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Confirm each declared supporting-document field is really on its page."""
    findings: list[dict[str, Any]] = []
    for document in entry["expected_documents"]:
        if not document["uploaded"] or not document["pdf"]:
            continue
        text = _pdf_text(FACTORY_ROOT / document["pdf"])
        for field_name, value in document["expected_fields"].items():
            if value is None:
                continue
            findings.append(
                {
                    "document": document["claimed_document_type"],
                    "field": field_name,
                    "expected_value": value,
                    "found_in_pdf": str(value) in text,
                }
            )
    return findings


def build_supporting_entry(bundle: SupportingBundle, variant: str) -> dict[str, Any]:
    """Manifest entry for one supporting-document bundle.

    Reads the bundle's own hand-declared expectations. Like the shipment
    entries, nothing here is produced by the verifier being graded.
    """
    folder = bundle.folder()
    expected_path = folder / "supporting_documents_expected.json"
    declared = json.loads(expected_path.read_text(encoding="utf-8"))

    documents: list[dict[str, Any]] = []
    for spec, entry in zip(bundle.documents, declared["documents"]):
        pdf = entry["text_pdf"] if variant == "text" else entry["scanned_pdf"]
        documents.append(
            {
                "claimed_document_type": spec.claimed_type,
                "uploaded": not spec.claimed_only,
                "pdf": pdf,
                "expected_detected_type": spec.expected_detected_type or None,
                "expected_state": spec.expected_state,
                "expected_content_status": spec.expected_content_status,
                "expected_failed_checks": list(spec.expected_failed_checks),
                "expected_manual_review_checks": list(
                    spec.expected_manual_review_checks
                ),
                "expected_authenticity_status": "not_externally_verified",
                "expected_counts_as_present": spec.expected_counts_as_present,
                "expected_fields": spec.fields.expected(),
                "expected_required_action": spec.expected_required_action,
            }
        )

    return {
        "scenario_id": bundle.key,
        "variant": variant,
        "kind": "supporting_documents",
        "title": bundle.title,
        "purpose": bundle.description,
        "base_scenario": bundle.base_scenario,
        "base_scenario_documents": declared["base_scenario_documents"],
        "shipment": declared["shipment"],
        "injected_defect": bundle.injected_defect,
        "claimed_only_document_types": bundle.claimed_only_types,
        "expected_primary_status": bundle.expected_overall_status,
        "expected_human_review": bundle.expected_human_review,
        "expected_required_action": bundle.expected_required_action,
        "expected_documents": documents,
        "expected_false_pass": False,
        "grading_note": (
            "A claimed-only document type must never satisfy a requirement, and "
            "no document may ever be reported as externally authentic."
        ),
    }


def main() -> None:
    scenarios = build_scenarios()
    entries: list[dict[str, Any]] = []
    for scenario in scenarios:
        for variant in ("text", "scanned"):
            entries.append(build_manifest_entry(scenario, variant))

    bundles = build_bundles()
    supporting_entries: list[dict[str, Any]] = []
    for bundle in bundles:
        for variant in ("text", "scanned"):
            supporting_entries.append(build_supporting_entry(bundle, variant))

    manifest = {
        "schema_version": "1.1",
        "independence_contract": (
            "Expected statuses in this manifest are hand-declared from each "
            "scenario's stated purpose and Pakistan export rules. This file is "
            "produced without importing or calling the deterministic compliance "
            "engine, the executable-rule evaluator, or the item matcher, so it "
            "can be used to grade those components."
        ),
        "scenario_count": len(scenarios),
        "entry_count": len(entries),
        "scenarios": entries,
        "supporting_document_bundle_count": len(bundles),
        "supporting_document_entry_count": len(supporting_entries),
        "supporting_document_scenarios": supporting_entries,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Validation report.
    lines = [
        "# Scenario Manifest Validation",
        "",
        "Every factual expectation in `synthetic_factory/scenario_manifest.json` is "
        "cross-checked below against the literal text of the generated PDFs. A `yes` "
        "in **Found in PDF** means the exact expected string is present in the "
        "document, so the manifest is describing the shipped artefact rather than an "
        "assumption.",
        "",
        "Expected *statuses* are hand-declared from each scenario's purpose and are "
        "deliberately NOT produced by the compliance engine, so they can grade it.",
        "",
    ]
    total = 0
    mismatches: list[str] = []
    for scenario in scenarios:
        entry = build_manifest_entry(scenario, "text")
        findings = validate_entry_against_pdf(entry)
        ok = sum(1 for f in findings if f["found_in_pdf"])
        total += len(findings)
        lines.append(f"## `{scenario.key}`")
        lines.append("")
        lines.append(f"- Purpose: {BEHAVIOUR[scenario.key]['purpose']}")
        lines.append(
            f"- Hand-declared expected status: "
            f"**{BEHAVIOUR[scenario.key]['expected_primary_status']}**"
        )
        lines.append(f"- PDF evidence checks passed: **{ok}/{len(findings)}**")
        lines.append("")
        lines.append("| Field | Expected value | Source | Found in PDF |")
        lines.append("| --- | --- | --- | --- |")
        for finding in findings:
            mark = "yes" if finding["found_in_pdf"] else "**NO**"
            if not finding["found_in_pdf"]:
                mismatches.append(f"{scenario.key}: {finding['field']}")
            lines.append(
                f"| {finding['field']} | `{finding['expected_value']}` | "
                f"{finding['source_document']} | {mark} |"
            )
        lines.append("")

    lines.append("# Supporting-Document Bundles")
    lines.append("")
    lines.append(
        "Each supporting document's declared field values are checked against the "
        "literal text of the generated supporting PDF, exactly as above."
    )
    lines.append("")
    supporting_total = 0
    supporting_mismatches: list[str] = []
    for bundle in bundles:
        entry = build_supporting_entry(bundle, "text")
        findings = validate_supporting_entry_against_pdf(entry)
        ok = sum(1 for f in findings if f["found_in_pdf"])
        supporting_total += len(findings)
        for finding in findings:
            if not finding["found_in_pdf"]:
                supporting_mismatches.append(
                    f"{bundle.key}: {finding['document']}.{finding['field']}"
                )
        uploaded = sum(1 for spec in bundle.documents if not spec.claimed_only)
        claimed_only = len(bundle.documents) - uploaded
        lines.append(f"## `{bundle.key}`")
        lines.append("")
        lines.append(f"- Purpose: {bundle.description}")
        lines.append(f"- Injected defect: {bundle.injected_defect or 'none'}")
        lines.append(
            f"- Documents: **{uploaded} uploaded**, {claimed_only} claimed-only "
            f"(name supplied without a UUID)"
        )
        lines.append(
            f"- Hand-declared expected status: **{bundle.expected_overall_status}**"
        )
        lines.append(f"- PDF evidence checks passed: **{ok}/{len(findings)}**")
        lines.append("")

    lines.insert(
        6,
        f"**Result: {total - len(mismatches)}/{total} shipment expectations and "
        f"{supporting_total - len(supporting_mismatches)}/{supporting_total} "
        f"supporting-document expectations verified directly against PDF text.**"
        + (
            ""
            if not mismatches
            else "\n\nUnverified: " + ", ".join(mismatches)
        ),
    )
    lines.insert(7, "")
    VALIDATION_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    VALIDATION_REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(
        f"Wrote {MANIFEST_PATH} ({len(entries)} shipment entries, "
        f"{len(supporting_entries)} supporting-document entries)"
    )
    print(f"Wrote {VALIDATION_REPORT_PATH}")
    print(f"Shipment PDF evidence:   {total - len(mismatches)}/{total} verified")
    print(
        f"Supporting PDF evidence: "
        f"{supporting_total - len(supporting_mismatches)}/{supporting_total} verified"
    )
    for label, items in (
        ("UNVERIFIED SHIPMENT EXPECTATIONS", mismatches),
        ("UNVERIFIED SUPPORTING EXPECTATIONS", supporting_mismatches),
    ):
        if items:
            print(f"{label}:")
            for item in items:
                print(f"  - {item}")


if __name__ == "__main__":
    main()
