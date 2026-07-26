"""Generate the five-product legal provenance report from the executable rules.

The report is derived directly from ``textile_mvp_executable_rules.json`` and the
same provenance gate the engine uses, so it cannot claim more certainty than the
data supports.

Run:
    python -m scripts.build_provenance_report
"""

from __future__ import annotations

from pathlib import Path

from app.services.compliance.executable_rule_loader import load_executable_rules
from app.services.compliance.executable_rule_models import (
    ExecutableCheckType,
    ExecutableRule,
    RuleValidationStatus,
)
from app.services.compliance.result_builder import (
    LEGAL_CUTOFF_DATE,
    _missing_legal_source_fields,
)

REPORT_PATH = (
    Path(__file__).resolve().parents[1] / "reports" / "five_product_legal_provenance_report.md"
)
PRODUCTS = [
    ("52010090", "Raw cotton"),
    ("52051100", "Cotton yarn"),
    ("52094200", "Denim fabric"),
    ("61091000", "Cotton knitted T-shirts"),
    ("63023110", "Cotton bed sheets"),
]


def _missing_provenance(rule: ExecutableRule) -> list[str]:
    return _missing_legal_source_fields(
        source_document=rule.source_document,
        source_url=rule.source_url,
        source_page=rule.source_page,
        source_locator=rule.source_locator,
        issuing_authority=rule.issuing_authority,
        effective_date=rule.effective_date,
        legal_cutoff_date=rule.legal_cutoff_date or LEGAL_CUTOFF_DATE,
        rule_data_version="present-at-runtime",
        validation_status=rule.validation_status.value,
    )


def _can_pass(rule: ExecutableRule) -> bool:
    """Could this rule ever contribute a genuine legal pass?"""
    if rule.check_type is ExecutableCheckType.EXPORT_STATUS_INFORMATIONAL:
        return False
    if not rule.supports_positive_decision:
        return False
    return not _missing_provenance(rule)


def build() -> str:
    rule_set = load_executable_rules()
    lines: list[str] = []
    lines.append("# Stage 2 — Five-Product Legal Provenance Report")
    lines.append("")
    lines.append(f"Generated from `{rule_set.generated_from}`.")
    lines.append(f"Legal cutoff date applied: **{rule_set.legal_cutoff_date}**. "
                 f"Retrieval date: **{rule_set.retrieval_date}**.")
    lines.append("")
    lines.append("This report is produced directly from the validated executable rule set and "
                 "the engine's provenance gate. It never asserts more certainty than the source "
                 "data supports. A rule can drive a genuine legal **pass** only when its "
                 "validation status is `verified`/`partially_verified` **and** every provenance "
                 "field (source document, URL, page/locator, issuing authority, effective date, "
                 "legal cutoff, validation status) is present.")
    lines.append("")

    any_product_can_pass = False
    for code, name in PRODUCTS:
        rules = rule_set.rules_for(code)
        confirmed = [r for r in rules if r.validation_status == RuleValidationStatus.VERIFIED]
        partial = [r for r in rules if r.validation_status == RuleValidationStatus.PARTIALLY_VERIFIED]
        uncertain = [
            r for r in rules
            if r.validation_status
            not in (RuleValidationStatus.VERIFIED, RuleValidationStatus.PARTIALLY_VERIFIED)
        ]
        passable = [r for r in rules if _can_pass(r)]
        product_can_pass = bool(passable)
        any_product_can_pass = any_product_can_pass or product_can_pass

        lines.append(f"## {code} — {name}")
        lines.append("")
        lines.append(f"- Total executable rules: **{len(rules)}**")
        lines.append(f"- Confirmed (`verified`): **{len(confirmed)}** — "
                     + (", ".join(r.rule_id for r in confirmed) or "none"))
        lines.append(f"- Partially verified: **{len(partial)}** — "
                     + (", ".join(r.rule_id for r in partial) or "none"))
        lines.append(f"- Still uncertain (`unverified`/other): **{len(uncertain)}** — "
                     + (", ".join(r.rule_id for r in uncertain) or "none"))
        lines.append("")
        lines.append("**Exact official sources referenced:**")
        seen: set[str] = set()
        for rule in rules:
            src = rule.source_document or "(no source document)"
            extra = []
            if rule.sro_number:
                extra.append(f"SRO {rule.sro_number}")
            if rule.source_url:
                extra.append(rule.source_url)
            key = src + "|" + "|".join(extra)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {src}" + (f" ({'; '.join(extra)})" if extra else ""))
        lines.append("")
        lines.append("**Missing provenance blocking a legal pass:**")
        blocking = False
        for rule in rules:
            if rule.check_type is ExecutableCheckType.EXPORT_STATUS_INFORMATIONAL:
                continue
            missing = _missing_provenance(rule)
            if missing and rule.supports_positive_decision:
                blocking = True
                lines.append(f"- `{rule.rule_id}`: missing {', '.join(missing)}")
        if not blocking:
            lines.append("- (none among verifiable rules)")
        lines.append("")
        lines.append(f"**Genuine legal pass currently possible:** "
                     f"{'YES' if product_can_pass else 'NO'}")
        lines.append("")
        lines.append("**Why manual review may still be required:**")
        if not product_can_pass:
            lines.append("- No rule for this product currently satisfies the full provenance gate. "
                         "The dominant gap is a verified **effective date** for the governing "
                         "SRO / TIPP measure, which was not confirmable from the reviewed local "
                         "sources. Unverified requirements (e.g. licence/permit/approval recorded "
                         "as null in the source data) also resolve to manual review by policy.")
        else:
            lines.append("- Some rules can pass, but any single uncertain or unverified rule for "
                         "the product still forces the product-level result to manual review.")
        lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Any product able to reach a genuine legal pass today: "
                 f"**{'YES' if any_product_can_pass else 'NO'}**.")
    lines.append("- This is the honest fail-closed state: the pipeline never forces a pass, and "
                 "the remaining blockers are documented above rather than papered over.")
    lines.append("")
    lines.append("## What would unblock a genuine pass")
    lines.append("")
    lines.append("1. Confirm and record verified **effective dates** for the EPO 2022 base order, "
                 "SRO 2486(I)/2025, and the relevant TIPP measures from the official gazette PDFs.")
    lines.append("2. Confirm **source page numbers** inside the local official PDFs for each cited "
                 "measure (currently only the raw-cotton SRO carries a page).")
    lines.append("3. Re-run the TIPP measure verification once the portal database error is "
                 "resolved, upgrading `partially_verified` common-clearance and COO rules to "
                 "`verified` where the portal confirms them.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(build(), encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
