"""Apply verified legal effective dates to the textile-MVP executable rules.

This is a data-preparation step, not part of the backend API or the workflow
engine. It reads the already-generated executable rule set and the auditable
overlay in ``regulatory_data/config/legal_effective_dates.json`` and writes the
same rules back with:

* ``effective_date`` / ``issue_date`` filled in from the overlay (raw-cotton SRO
  2486(I)/2025 rules get the SRO date; every other rule gets the governing
  Export Policy Order 2022 framework date);
* ``requirement_status`` rules that were ``unverified`` upgraded to
  ``partially_verified`` per the documented general-permission rationale;
* a provenance sentence appended to ``validation_note`` so the attribution stays
  visible and auditable.

The script is idempotent: running it twice produces the same file. It never
adds a requirement, agency, page or URL that is not already in the rule.

Run (after ``python -m scripts.build_executable_rules``):
    python -m scripts.apply_legal_provenance_overlay
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = (
    PROJECT_ROOT
    / "regulatory_data"
    / "processed"
    / "compliance"
    / "textile_mvp_executable_rules.json"
)
OVERLAY_PATH = (
    PROJECT_ROOT / "regulatory_data" / "config" / "legal_effective_dates.json"
)

_MARKER = "[Provenance overlay]"


def _note_for(source: dict) -> str:
    return f"{_MARKER} Effective date {source['effective_date']} attributed from {source['citation']}"


def apply_overlay() -> dict:
    overlay = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))

    sources = overlay["sources"]
    sro_map = overlay["sro_number_to_source"]
    default_source_key = overlay["default_source"]
    upgrade = overlay["validation_upgrade"]

    dated = 0
    upgraded = 0
    for rule in data["rules"]:
        source_key = sro_map.get(rule.get("sro_number"), default_source_key)
        source = sources[source_key]

        if rule.get("effective_date") != source["effective_date"]:
            dated += 1
        rule["effective_date"] = source["effective_date"]
        rule["issue_date"] = source["issue_date"]

        if (
            rule.get("check_type") in upgrade["applies_to_check_types"]
            and rule.get("validation_status") == upgrade["from"]
        ):
            rule["validation_status"] = upgrade["to"]
            upgraded += 1
            reason = f" {_MARKER} {upgrade['rationale']}"
            if reason.strip() not in rule.get("validation_note", ""):
                rule["validation_note"] = rule.get("validation_note", "") + reason

        note = _note_for(source)
        if _MARKER not in rule.get("validation_note", ""):
            rule["validation_note"] = (
                rule.get("validation_note", "").rstrip() + " " + note
            )

    # Note: the ExecutableRuleSet schema forbids extra top-level keys, so the
    # overlay context is not written here; it lives in the config overlay file
    # and in each rule's validation_note.
    RULES_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {"rules": len(data["rules"]), "dated": dated, "upgraded": upgraded}


def main() -> None:
    result = apply_overlay()
    print(
        f"Applied provenance overlay to {result['rules']} rules "
        f"({result['dated']} dated, {result['upgraded']} status-upgraded) -> {RULES_PATH}"
    )


if __name__ == "__main__":
    main()
