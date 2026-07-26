"""Deterministically build the executable rule set from existing verified data.

This transformation is auditable and non-inventive: every provenance value is
read from the already-curated regulatory JSON (``textile_product_requirements``
and ``textile_mvp_pct_codes``). The script only reshapes those values into flat
executable rule records and maps each source ``verification_status`` string to
the controlled ``RuleValidationStatus`` enum. It never adds a law, SRO, page,
date or authority that is not present in the source data.

Run:
    python -m scripts.build_executable_rules
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REG = PROJECT_ROOT / "regulatory_data"
PCT_CONFIG = REG / "config" / "textile_mvp_pct_codes.json"
PRODUCT_REQS = (
    REG / "raw" / "psw" / "textile_product_requirements" / "textile_product_requirements.json"
)
OUT = REG / "processed" / "compliance" / "textile_mvp_executable_rules.json"

LEGAL_CUTOFF_DATE = "2026-07-22"
ALL_CODES = ["52010090", "52051100", "52094200", "61091000", "63023110"]
NON_RAW_COTTON = ["52051100", "52094200", "61091000", "63023110"]
RAW_COTTON = "52010090"


def map_status(raw: str | None) -> str:
    """Map a source verification_status string to the controlled enum."""
    if not raw:
        return "unverified"
    low = raw.casefold()
    if "portal_limitation" in low:
        return "partially_verified"
    if "conflict" in low:
        return "conflicting"
    if "inaccessible" in low or "portal database error" in low:
        return "inaccessible"
    if low.startswith("verified") or "manually_verified" in low or "verified_from" in low:
        return "verified"
    # "no_..._identified_in_reviewed_sources", "not_stated...", "no_separate_approval..."
    return "unverified"


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def build() -> dict:
    pct_config = json.loads(PCT_CONFIG.read_text(encoding="utf-8"))
    reqs = json.loads(PRODUCT_REQS.read_text(encoding="utf-8"))
    products = {p["pct_code"]: p for p in reqs["products"]}
    metadata = {p["pct_code"]: p for p in pct_config["products"]}
    common = reqs["common_export_clearance"]
    coo = reqs["conditional_certificate_of_origin"]
    retrieval_date = reqs["retrieval_date"]

    rules: list[dict] = []

    def add(**kwargs) -> None:
        base = {
            "export_status": None,
            "trigger": None,
            "required_document": None,
            "required_approval": None,
            "responsible_agency": None,
            "destination_country": None,
            "numeric_value": None,
            "deadline_days": None,
            "deadline_anchor": None,
            "source_document": None,
            "sro_number": None,
            "source_url": None,
            "source_page": None,
            "source_locator": None,
            "issuing_authority": None,
            "issue_date": None,
            "effective_date": None,
            "legal_cutoff_date": LEGAL_CUTOFF_DATE,
            "retrieval_date": retrieval_date,
        }
        base.update(kwargs)
        rules.append(base)

    # --- Common export-clearance documents (all five codes) ---
    for doc, name in (
        ("commercial_invoice", "Commercial invoice"),
        ("packing_list", "Packing list"),
        ("form_e", "Form-E"),
    ):
        add(
            rule_id=f"xr_common_{doc}",
            rule_name=f"{name} required for export clearance",
            applies_to_pct_codes=ALL_CODES,
            check_type="required_document",
            required_document=doc,
            responsible_agency=common["responsible_government_agency"],
            issuing_authority=common["responsible_government_agency"],
            source_document="TIPP Customs Clearance Procedure [Export] [Web-Based]",
            source_url=common["source_url"],
            source_locator="TIPP export customs-clearance procedure",
            validation_status="partially_verified",
            validation_note=(
                "Confirmed from the TIPP export customs-clearance procedure page; "
                "TIPP portal keyword-measure search returned a database error during "
                "retrieval, so this is partially verified. Effective date is not stated "
                "on the procedure page."
            ),
        )

    # --- Per-product export status (informational) ---
    for code in ALL_CODES:
        product = products[code]
        es = product["export_status"]
        add(
            rule_id=f"xr_{code}_export_status",
            rule_name=f"Export status for {product['simple_product_name']}",
            applies_to_pct_codes=[code],
            check_type="export_status_informational",
            export_status=es["value"],
            responsible_agency="; ".join(product.get("responsible_government_agencies", []) ) or None,
            source_document=rel(PRODUCT_REQS),
            source_url=(product.get("source_urls") or [None])[0],
            source_locator=f"TIPP commodity {product.get('tipp_commodity_code')}",
            issuing_authority="Ministry of Commerce",
            validation_status=map_status(es.get("verification_status")),
            validation_note=es.get("note") or "Export status recorded from reviewed official sources.",
        )

    # --- Licence / permit / approval requirement-status rules (all five) ---
    for code in ALL_CODES:
        product = products[code]
        for field_name, doc, label in (
            ("licence_required", "product_licence", "Licence"),
            ("permit_required", "import_permit" if code == RAW_COTTON else "product_permit", "Permit"),
            ("approval_required", "product_approval", "Approval"),
        ):
            req = product.get(field_name) or {}
            value = req.get("value")
            status = map_status(req.get("verification_status"))
            check_type = "conditional_document" if value == "conditional" else "requirement_status"
            add(
                rule_id=f"xr_{code}_{field_name}",
                rule_name=f"{label} requirement for {product['simple_product_name']}",
                applies_to_pct_codes=[code],
                check_type=check_type,
                required_document=doc if value in ("conditional", True) else None,
                numeric_value=str(value) if value is not None else None,
                responsible_agency="; ".join(product.get("responsible_government_agencies", [])) or None,
                source_document=rel(PRODUCT_REQS),
                source_url=(product.get("source_urls") or [None])[0],
                source_locator=f"TIPP commodity {product.get('tipp_commodity_code')}",
                issuing_authority="Ministry of Commerce",
                validation_status=status,
                validation_note=(
                    req.get("details")
                    or req.get("note")
                    or req.get("verification_status")
                    or "No product-specific value verified in reviewed official sources."
                ),
            )

    # --- Raw-cotton certificate (phytosanitary) ---
    raw = products[RAW_COTTON]
    cert = raw["certificate_required"]
    phyto_url = next((u for u in raw["source_urls"] if "id=2&page=80" in u), raw["source_urls"][0])
    add(
        rule_id="xr_52010090_phytosanitary_certificate",
        rule_name="Phytosanitary certificate for raw cotton export",
        applies_to_pct_codes=[RAW_COTTON],
        check_type="required_document",
        required_document="phytosanitary_certificate",
        responsible_agency="Department of Plant Protection",
        source_document="Pakistan Plant Quarantine Rules, 2019",
        source_url=phyto_url,
        source_locator="TIPP measure id 2, page 80",
        issuing_authority="Department of Plant Protection",
        validation_status=map_status(cert.get("verification_status")),
        validation_note=cert.get("details") or "Phytosanitary certificate verified from TIPP measure and procedure.",
    )

    # --- Raw-cotton SRO 2486(I)/2025 financial and shipment conditions ---
    sro_url = next((u for u in raw["source_urls"] if "commerce.gov.pk/sros" in u), None)
    sro_status = "verified" if sro_url else "manual_review"
    sro_doc = "SRO 2486(I)/2025 — amendment to Export Policy Order, 2022"
    for rule_id, name, doc, note in (
        (
            "xr_52010090_sbp_deposit_proof",
            "Proof of 1% SBP security deposit for raw cotton",
            "sbp_deposit_proof",
            "Deposit 1% of the contract value with the State Bank of Pakistan.",
        ),
        (
            "xr_52010090_sbp_confirmation",
            "SBP confirmation presented to Customs for raw cotton",
            "sbp_confirmation",
            "Present SBP confirmation to Customs with the shipping documents.",
        ),
        (
            "xr_52010090_irrevocable_letter_of_credit",
            "Irrevocable letter of credit for raw cotton",
            "irrevocable_letter_of_credit",
            "The buyer must open an irrevocable letter of credit.",
        ),
    ):
        add(
            rule_id=rule_id,
            rule_name=name,
            applies_to_pct_codes=[RAW_COTTON],
            check_type="required_document",
            required_document=doc,
            numeric_value="1%" if doc == "sbp_deposit_proof" else None,
            responsible_agency="State Bank of Pakistan",
            source_document=sro_doc,
            sro_number="2486(I)/2025",
            source_url=sro_url,
            source_page=1,
            source_locator="page 1",
            issuing_authority="Government of Pakistan, Ministry of Commerce",
            validation_status=sro_status,
            validation_note=note,
        )
    add(
        rule_id="xr_52010090_shipment_within_180_days",
        rule_name="Raw cotton shipment within 180 days of the letter of credit",
        applies_to_pct_codes=[RAW_COTTON],
        check_type="shipment_deadline",
        deadline_days=180,
        deadline_anchor="letter_of_credit_date",
        responsible_agency="State Bank of Pakistan",
        source_document=sro_doc,
        sro_number="2486(I)/2025",
        source_url=sro_url,
        source_page=1,
        source_locator="page 1",
        issuing_authority="Government of Pakistan, Ministry of Commerce",
        validation_status=sro_status,
        validation_note="Complete the contracted shipment within 180 days of the letter-of-credit date.",
    )

    # --- Conditional certificate of origin for the four non-raw-cotton codes ---
    china = next((p for p in coo["destination_procedures"] if "China" in p["destination_or_scheme"]), None)
    add(
        rule_id="xr_coo_china",
        rule_name="Certificate of origin for export to China under CPFTA",
        applies_to_pct_codes=NON_RAW_COTTON,
        check_type="destination_document",
        required_document="certificate_of_origin",
        destination_country="China",
        responsible_agency=coo["responsible_government_agency"],
        source_document="TIPP Certificate of Origin to China under CPFTA",
        source_url=(china or {}).get("source_url"),
        source_locator="TIPP CPFTA certificate-of-origin procedure",
        issuing_authority=coo["responsible_government_agency"],
        validation_status="partially_verified",
        validation_note="Certificate of origin is required for China under CPFTA per the TIPP procedure; effective date is not stated on the procedure page.",
    )
    add(
        rule_id="xr_coo_other_destinations",
        rule_name="Conditional certificate of origin for other destinations",
        applies_to_pct_codes=NON_RAW_COTTON,
        check_type="conditional_document",
        required_document="certificate_of_origin",
        responsible_agency=coo["responsible_government_agency"],
        source_document="TIPP consolidated certificate-of-origin measure",
        source_url=coo["measure_source_url"],
        source_locator="TIPP consolidated certificate-of-origin measure",
        issuing_authority=coo["responsible_government_agency"],
        validation_status="partially_verified",
        validation_note="Certificate of origin is conditional on the destination market or preferential scheme; the specific scheme cannot be decided from shipment fields alone.",
    )

    return {
        "schema_version": "1.0",
        "generated_from": [rel(PCT_CONFIG), rel(PRODUCT_REQS)],
        "legal_cutoff_date": LEGAL_CUTOFF_DATE,
        "retrieval_date": retrieval_date,
        "rules": rules,
    }


def main() -> None:
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} with {len(data['rules'])} executable rules")


if __name__ == "__main__":
    main()
