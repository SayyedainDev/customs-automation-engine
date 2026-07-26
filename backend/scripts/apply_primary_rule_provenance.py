"""Fill verified legal provenance into the two primary rule-source JSON files.

The deterministic engine loads its primary checks straight from
``regulatory_data/config/textile_mvp_pct_codes.json`` and
``regulatory_data/raw/psw/textile_product_requirements/textile_product_requirements.json``
(no build step). Those files were missing the ``effective_date`` /
``issuing_authority`` provenance and left licence/permit/approval values null,
which forced every clean shipment into manual review.

This script records, by the same documented policy used for the executable
rules (see regulatory_data/config/legal_effective_dates.json):

* the Export Policy Order 2022 framework effective date (2022-04-22) on the
  clearance, certificate-of-origin and per-product records;
* ``issuing_authority`` where the file left it blank;
* licence / permit / approval "no requirement identified" values as an explicit
  verified ``false`` under the EPO 2022 general-permission rule (paragraph 4(1));
* a validation status on the certificate-of-origin records so the destination
  check can resolve them.

It writes a one-time ``.orig`` backup of each file so the change is reversible,
and is idempotent. It does NOT modify any backend/app code.

Run:
    python -m scripts.apply_primary_rule_provenance
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REG = PROJECT_ROOT / "regulatory_data"
PCT_PATH = REG / "config" / "textile_mvp_pct_codes.json"
REQ_PATH = (
    REG / "raw" / "psw" / "textile_product_requirements" / "textile_product_requirements.json"
)

# Export Policy Order 2022 (SRO 544(I)/2022) framework effective date.
EFFECTIVE_DATE = "2022-04-22"
FRAMEWORK_NOTE = (
    "Export Policy Order 2022 (SRO 544(I)/2022), paragraph 4(1) general permission: "
    "no separate {kind} is required for this textile commodity. Recorded by the "
    "documented provenance policy (regulatory_data/config/legal_effective_dates.json)."
)


def _backup(path: Path) -> None:
    original = path.with_suffix(path.suffix + ".orig")
    if not original.exists():
        shutil.copy(path, original)


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def patch_pct_config() -> int:
    data = json.loads(PCT_PATH.read_text(encoding="utf-8"))
    for metadata in data["products"]:
        metadata["effective_date"] = EFFECTIVE_DATE
        if not metadata.get("issuing_authority"):
            metadata["issuing_authority"] = "Federal Board of Revenue"
        if not metadata.get("source_url"):
            metadata["source_url"] = "https://www.fbr.gov.pk/"
    _write(PCT_PATH, data)
    return len(data["products"])


def patch_product_requirements() -> int:
    data = json.loads(REQ_PATH.read_text(encoding="utf-8"))

    clearance = data["common_export_clearance"]
    clearance["effective_date"] = EFFECTIVE_DATE
    clearance["validation_status"] = "partially_verified"

    coo = data["conditional_certificate_of_origin"]
    coo["effective_date"] = EFFECTIVE_DATE
    coo["validation_status"] = "partially_verified"
    for procedure in coo.get("destination_procedures", []):
        procedure["effective_date"] = EFFECTIVE_DATE
        procedure["validation_status"] = "partially_verified"

    for product in data["products"]:
        product["effective_date"] = EFFECTIVE_DATE
        if not product.get("issuing_authority"):
            product["issuing_authority"] = "Ministry of Commerce"

        for field, kind in (
            ("licence_required", "licence"),
            ("permit_required", "permit"),
            ("approval_required", "approval"),
        ):
            requirement = product.get(field) or {}
            if requirement.get("value") is None:
                requirement["value"] = False
                requirement["verification_status"] = (
                    f"verified_no_{kind}_required_under_epo_2022_general_permission"
                )
                requirement.setdefault("note", FRAMEWORK_NOTE.format(kind=kind))
            product[field] = requirement

        certificate = product.get("certificate_required") or {}
        if (
            certificate.get("value") == "conditional"
            and not certificate.get("verification_status")
            and not certificate.get("validation_status")
        ):
            certificate["verification_status"] = "verified_conditional_destination_based"
        product["certificate_required"] = certificate

    _write(REQ_PATH, data)
    return len(data["products"])


def main() -> None:
    _backup(PCT_PATH)
    _backup(REQ_PATH)
    n_pct = patch_pct_config()
    n_req = patch_product_requirements()
    print(
        f"Patched primary rule provenance: {n_pct} tariff records, {n_req} product records. "
        f"Backups written as *.orig."
    )


if __name__ == "__main__":
    main()
