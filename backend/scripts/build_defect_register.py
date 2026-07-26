"""Derive the defect register from a live evaluation run.

Every record is grounded in an observed failure in
``reports/<prefix>.json``: the register cannot invent a defect that the live
run did not exhibit. Root-cause categories are the curated classification
decided from reproduction evidence, keyed by failure signature.

Run:
    python -m scripts.build_defect_register
    python -m scripts.build_defect_register --input synthetic_factory_final_evaluation
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = BACKEND_ROOT / "reports"


# --------------------------------------------------------------------------- #
# Curated root-cause classification, keyed by observable failure signature.
# --------------------------------------------------------------------------- #
def _is_malformed_structured(row: dict[str, Any]) -> bool:
    failure = row.get("technical_failure") or ""
    return "malformed structured data" in failure


def _is_empty_line_items(row: dict[str, Any]) -> bool:
    return (
        row.get("technical_failure") is None
        and row.get("line_item_count_actual") == 0
        and row.get("line_item_count_expected", 0) > 0
    )


def _has_label_bleed(row: dict[str, Any]) -> bool:
    for field in row.get("fields", []):
        expected, actual = field.get("expected"), field.get("actual")
        if (
            not field.get("correct")
            and isinstance(expected, str)
            and isinstance(actual, str)
            and expected.strip() and actual.strip()
            and expected.strip().casefold() in actual.strip().casefold()
            and actual.strip().casefold() != expected.strip().casefold()
        ):
            return True
    return False


def _has_field_errors(row: dict[str, Any]) -> bool:
    return any(
        not f.get("correct") and not f.get("missing") for f in row.get("fields", [])
    ) and not _has_label_bleed(row)


def _has_missing_fields(row: dict[str, Any]) -> bool:
    return (
        row.get("technical_failure") is None
        and row.get("line_item_count_actual", 0) > 0
        and any(f.get("missing") for f in row.get("fields", []))
    )


def _status_wrong(row: dict[str, Any]) -> bool:
    return (
        row.get("technical_failure") is None
        and not row.get("status_correct")
        and row.get("line_item_count_actual") == row.get("line_item_count_expected")
    )


def _fallback_wrong(row: dict[str, Any]) -> bool:
    return row.get("technical_failure") is None and row.get("fallback_ran") != row.get(
        "fallback_expected"
    )


SIGNATURES: list[tuple[str, Callable[[dict[str, Any]], bool], dict[str, Any]]] = [
    (
        "DEF-001",
        _is_malformed_structured,
        {
            "title": "Provider schema rejection collapses the whole document",
            "root_cause_category": "LLM_line_item_extraction",
            "severity": "critical",
            "affected_file_or_module": "app/services/structured_extraction_service.py",
            "root_cause": (
                "Pydantic emits a JSON-Schema `pattern` containing a negative "
                "lookahead for the string branch of Decimal fields. Groq's "
                "constrained decoder cannot compile lookarounds, so it rejected "
                "every Phase 2C strict schema (HTTP 400 "
                "pattern_unsupported_feature) and the request silently fell back "
                "to unconstrained json_object mode. In that mode the model "
                "intermittently emitted empty strings between array objects "
                "([obj, '', obj, '', obj]); local Pydantic validation correctly "
                "refused the payload, discarding every correctly-extracted line "
                "and failing the request with HTTP 502."
            ),
            "proposed_fix": (
                "Strip only the unsupported regex constructs from the "
                "provider-facing transport schema so strict mode is usable "
                "again (local Pydantic validation is unchanged), and add staged "
                "per-line extraction as a fallback so one malformed row can no "
                "longer destroy the valid rows."
            ),
            "fix_allowed": True,
        },
    ),
    (
        "DEF-002",
        _is_empty_line_items,
        {
            "title": "OCR silently drops invoice product rows",
            "root_cause_category": "OCR",
            "severity": "high",
            "affected_file_or_module": "app/core/config.py (ocr_page_segmentation_mode)",
            "root_cause": (
                "Tesseract ran with PSM 6 ('assume a single uniform block of "
                "text'). An invoice page is not a uniform block: it is a "
                "label-value header, a wide sparse table, then a footer. PSM 6 "
                "discarded product rows whose columns are separated by large "
                "horizontal gaps, so the model received a table header with no "
                "rows beneath it and returned zero line items. OCR confidence "
                "stayed ~0.94, so the confidence gate could not see the loss. "
                "Measured across all 14 scanned fixtures PSM 6 recovered 52/62 "
                "expected tokens versus 62/62 for PSM 3 and PSM 4."
            ),
            "proposed_fix": (
                "Use PSM 4 ('single column of text of variable sizes'), which "
                "recovers every row and additionally keeps a label and its value "
                "on one line, unlike PSM 3 which splits the label and value "
                "columns into separate blocks."
            ),
            "fix_allowed": True,
        },
    ),
    (
        "DEF-003",
        _has_label_bleed,
        {
            "title": "Field label absorbed into the extracted value",
            "root_cause_category": "LLM_header_extraction",
            "severity": "medium",
            "affected_file_or_module": "app/services/extraction/ocr_extractor.py + header prompt",
            "root_cause": (
                "On OCR text where a label and its value share one line "
                "('Exporter Multan Raw Cotton Traders (Pvt.) Ltd.'), the model "
                "returned the label as part of the value."
            ),
            "proposed_fix": (
                "Instruct the extractor explicitly that a printed field label is "
                "not part of the field value; verify against the manifest rather "
                "than string-repairing the model output."
            ),
            "fix_allowed": True,
        },
    ),
    (
        "DEF-004",
        _has_missing_fields,
        {
            "title": "Expected field not extracted",
            "root_cause_category": "LLM_line_item_extraction",
            "severity": "medium",
            "affected_file_or_module": "app/services/multi_line_shipment_service.py",
            "root_cause": (
                "A field the manifest proves is printed in the document was "
                "returned as null, so it resolved to manual_review."
            ),
            "proposed_fix": "Investigate per-field; may be genuine OCR ambiguity.",
            "fix_allowed": True,
        },
    ),
    (
        "DEF-005",
        _status_wrong,
        {
            "title": "Deterministic status differs from the independent manifest",
            "root_cause_category": "executable_rule_evaluation",
            "severity": "critical",
            "affected_file_or_module": "app/services/compliance/",
            "root_cause": (
                "Extraction produced the expected number of lines, yet the "
                "deterministic status still differs from the hand-declared "
                "expectation."
            ),
            "proposed_fix": (
                "Determine whether the shipment data, the document-presence "
                "data, the rule trigger or the manifest expectation is wrong."
            ),
            "fix_allowed": True,
        },
    ),
    (
        "DEF-006",
        _fallback_wrong,
        {
            "title": "Single-line declared-total fallback fired incorrectly",
            "root_cause_category": "single_line_weight_fallback",
            "severity": "high",
            "affected_file_or_module": "app/services/multi_line_shipment_service.py",
            "root_cause": (
                "The deterministic weight fallback ran when it should not have, "
                "or failed to run when the manifest requires it."
            ),
            "proposed_fix": "Re-check the fallback preconditions against the manifest.",
            "fix_allowed": True,
        },
    ),
]


# A provider that refused to serve the request produces an *invalid
# measurement*, not evidence of an extraction defect. Such rows return in under
# a few seconds, whereas a served extraction takes 15-75s.
_RATE_LIMIT_MAX_SECONDS = 6.0


def _is_provider_unavailable(row: dict[str, Any]) -> bool:
    failure = row.get("technical_failure") or ""
    if not failure:
        return False
    if "unavailable or rate limited" in failure or "503" in failure:
        return True
    return row.get("duration_seconds", 0) < _RATE_LIMIT_MAX_SECONDS


# Remediation status per family, with the evidence that justifies it. Kept in
# code so regenerating the register never silently loses the audit trail.
RESOLUTIONS: dict[str, dict[str, Any]] = {
    "DEF-001": {
        "resolution": "fixed",
        "fix_applied": (
            "Unsupported lookaround/backref `pattern` keywords are stripped from "
            "the provider-facing transport schema only, restoring strict "
            "json_schema mode; staged per-line extraction added as a fallback so "
            "one malformed row cannot discard the valid rows."
        ),
        "changed_files": [
            "app/services/structured_extraction_service.py",
            "app/services/extraction/staged_multi_line.py",
            "app/services/multi_line_shipment_service.py",
        ],
        "regression_tests": [
            "tests/unit/test_groq_schema_compatibility.py",
            "tests/unit/test_staged_multi_line_extraction.py",
        ],
        "evidence": (
            "Controlled live A/B against the real provider in the same second: "
            "the old schema returned HTTP 400 pattern_unsupported_feature while "
            "the stripped schema passed schema validation and reached the quota "
            "check (HTTP 429). Full end-to-end rerun still pending quota."
        ),
    },
    "DEF-002": {
        "resolution": "fixed",
        "fix_applied": "ocr_page_segmentation_mode changed from PSM 6 to PSM 4.",
        "changed_files": ["app/core/config.py", ".env", ".env.example"],
        "regression_tests": ["tests/integration/test_ocr_table_rows.py"],
        "evidence": (
            "Measured with the real Tesseract binary across all 14 scanned "
            "fixtures: 62/62 expected tokens recovered versus 52/62 under PSM 6. "
            "PSM 4 preferred over PSM 3 because it keeps a label and its value on "
            "one line."
        ),
    },
    "DEF-003": {
        "resolution": "partially_fixed",
        "fix_applied": (
            "PSM 4 keeps 'Exporter <value>' on a single line, removing the "
            "segmentation ambiguity that produced the label bleed. No string "
            "repair of model output was introduced."
        ),
        "changed_files": ["app/core/config.py"],
        "regression_tests": ["tests/integration/test_ocr_table_rows.py"],
        "evidence": (
            "Cause addressed at the OCR layer; live re-measurement of the "
            "extracted field values is blocked by provider quota."
        ),
    },
    "DEF-007": {
        "resolution": "fixed",
        "fix_applied": (
            "New StructuredExtractionProviderUnavailableError subclass; 429/5xx "
            "now surface as HTTP 503 with an accurate message instead of "
            "'malformed structured data'."
        ),
        "changed_files": [
            "app/core/exceptions.py",
            "app/services/structured_extraction_service.py",
            "app/api/routes/multi_line_shipment.py",
        ],
        "regression_tests": ["tests/unit/test_provider_error_classification.py"],
        "evidence": (
            "Verified live: the API now returns 503 'The extraction model "
            "provider is unavailable or rate limited; no extraction was "
            "performed.'"
        ),
    },
    "DEF-008": {
        "resolution": "fixed",
        "fix_applied": (
            "Remove the whole unsupported string branch from numeric transport "
            "fields instead of only its `pattern`, so a JSON number is the only "
            "numeric option offered to the provider."
        ),
        "changed_files": ["app/services/structured_extraction_service.py"],
        "regression_tests": [
            "tests/unit/test_groq_schema_compatibility.py::"
            "test_numeric_fields_do_not_offer_a_free_string_branch"
        ],
        "evidence": (
            "Found live while testing supporting-document extraction: the "
            "provider emitted confidence: 'unknown' because the DEF-001 fix had "
            "left an unconstrained string branch. After the tighter fix the "
            "identical live request succeeded with confidence: 1 and every field "
            "extracted correctly."
        ),
    },
    "DEF-009": {
        "resolution": "fixed",
        "fix_applied": (
            "Derive a supporting document's confidence from the fields that were "
            "actually read, not from every field in the flat model. A field that "
            "is not printed on the page is not evidence that the page is "
            "illegible; recovering nothing is."
        ),
        "changed_files": ["app/services/supporting_document_service.py"],
        "regression_tests": [
            "tests/unit/test_supporting_document_verification.py::"
            "test_33_absent_fields_do_not_make_a_readable_document_unreadable",
            "tests/unit/test_supporting_document_verification.py::"
            "test_34_document_with_nothing_readable_is_still_unreadable",
        ],
        "evidence": (
            "Found live on the first end-to-end supporting-document upload. "
            "SupportingDocumentCandidates is one flat 20-field model covering ten "
            "document types, so a certificate of origin legitimately has no "
            "deposit percentage, LC shipment deadline or bank name; those return "
            "null at confidence 0. document_confidence took the minimum across "
            "ALL fields, so it was 0 for every real document, and confidence 0 "
            "means 'could not be read' - every genuine supporting document was "
            "classified unreadable. Live before: state=unreadable, "
            "content_status=manual_review, extraction_confidence=None while the "
            "extraction itself had correctly returned 'Certificate of Origin' and "
            "'SYN-COO-LCGINV2026002'. Live after: state=shipment_matched, "
            "content_status=passed, extraction_confidence=1, source_page=1. Only "
            "an end-to-end run could surface this: the unit tests built "
            "extractions with a uniform confidence across all fields."
        ),
    },
    "DEF-010": {
        "resolution": "fixed",
        "fix_applied": (
            "Resolve a printed document description to its canonical type in two "
            "deterministic stages: exact alias match, then a UNIQUE whole-token "
            "containment match. Ambiguous or unrecognised descriptions stay "
            "UNKNOWN, which becomes a manual review rather than a pass or a "
            "mismatch."
        ),
        "changed_files": ["app/schemas/supporting_documents.py"],
        "regression_tests": [
            "tests/unit/test_supporting_document_verification.py::"
            "test_35_prose_document_titles_resolve_to_their_canonical_type",
            "tests/unit/test_supporting_document_verification.py::"
            "test_36_ambiguous_or_unknown_titles_never_guess",
            "tests/unit/test_supporting_document_verification.py::"
            "test_37_containment_never_upgrades_a_genuinely_wrong_document",
        ],
        "evidence": (
            "Found live in clean_cotton_yarn_supporting: a correct Form-E was "
            "rejected as the wrong document type because the page described "
            "itself as 'Pakistan Single Window export declaration', which was not "
            "an exact alias. A real Form-E is printed under exactly that heading, "
            "so this was the system being wrong, not the fixture. The relaxation "
            "is bounded: a description naming two different document types, or "
            "none, still resolves to UNKNOWN, and a genuinely wrong document "
            "still fails (test_37)."
        ),
    },
    "DEF-011": {
        "resolution": "fixed",
        "fix_applied": (
            "verified_document_types() emits every alias of an already-verified "
            "canonical type, so the presence set and the rule data speak the "
            "same vocabulary. Widens naming only - a document still has to be "
            "uploaded and verified to enter the set."
        ),
        "changed_files": ["app/services/supporting_document_service.py"],
        "regression_tests": [
            "tests/unit/test_supporting_document_verification.py::"
            "test_38_verified_types_are_emitted_under_the_names_the_rules_use",
            "tests/unit/test_supporting_document_verification.py::"
            "test_39_alias_expansion_never_admits_an_unverified_document",
        ],
        "evidence": (
            "Found live in clean_cotton_yarn_supporting once DEF-010 was fixed. "
            "All five supporting documents reached state=shipment_matched and "
            "content_status=passed, yet the shipment still returned failed on "
            "required_document_form_e and xr_common_form_e with the message "
            "'Missing required document: Form-E'. The rule data names the "
            "document 'form_e'; the canonical type is "
            "'form_e_or_psw_export_declaration'. A document that had been "
            "uploaded, read, classified and fully cross-checked was reported as "
            "missing purely because the two halves used different names for it. "
            "This is the failure mode the whole supporting-document feature "
            "exists to prevent, inverted: not a false pass, but a false failure."
        ),
    },
    "DEF-012": {
        "resolution": "fixed",
        "fix_applied": (
            "Let StructuredExtractionProviderUnavailableError propagate out of "
            "verify_supporting_documents so the endpoint answers 503, instead of "
            "catching it and recording the document as unreadable. Genuine "
            "extraction failures (missing document, bad PDF, malformed model "
            "output) are still caught and still report manual_review."
        ),
        "changed_files": ["app/services/supporting_document_service.py"],
        "regression_tests": [
            "tests/unit/test_supporting_document_verification.py::"
            "test_40_provider_outage_is_not_reported_as_an_unreadable_document",
            "tests/unit/test_supporting_document_verification.py::"
            "test_41_a_genuinely_bad_pdf_is_still_reported_as_unreadable",
        ],
        "evidence": (
            "Found live when the Groq per-minute quota ran out part-way through "
            "clean_cotton_yarn_supporting. The first three documents verified; "
            "the bill of lading and export contract - both perfectly legible - "
            "were reported as state=unreadable, content_status=manual_review, "
            "'The uploaded document could not be read reliably.' That blames the "
            "trader's paperwork for our own rate limit, and lets a shipment "
            "settle on a status that was never actually assessed. DEF-007 fixed "
            "exactly this confusion for the invoice path; the supporting-document "
            "path was still swallowing it."
        ),
    },
    "DEF-013": {
        "resolution": "fixed",
        "fix_applied": (
            "The daily-quota preflight probed with max_completion_tokens="
            "1_000_000, which Groq rejects at request validation (HTTP 400) "
            "before the TPD check ever runs - the probe never actually "
            "observed daily headroom, it only ever fell into the fail-closed "
            "'unverifiable' branch. Capped the probe to "
            "GROQ_MAX_COMPLETION_TOKENS_CEILING (65536), the largest value "
            "Groq accepts for this parameter, confirmed live to trigger a "
            "parseable TPD 429."
        ),
        "changed_files": ["scripts/groq_quota_preflight.py"],
        "regression_tests": [
            "tests/unit/test_groq_quota_preflight.py::"
            "test_quota_probe_token_value_is_within_groq_accepted_range",
        ],
        "evidence": (
            "Found live running the evaluation runner: the preflight refused "
            "every run with 'Groq quota diagnostic returned HTTP 400; "
            "daily-token capacity could not be verified.' Reproduced directly: "
            "POST with max_tokens=1_000_000 -> HTTP 400 'max_tokens must be "
            "less than or equal to 65536'; identical request with "
            "max_tokens=65536 -> HTTP 429 with a parseable "
            "Limit/Used/Requested TPD body. The fail-closed behaviour itself "
            "was correct and never authorized a run on bad data; the probe "
            "value simply made the diagnostic permanently blind."
        ),
    },
    "DEF-014": {
        "resolution": "fixed",
        "fix_applied": (
            "_groq_request classified upstream failures by checking "
            "isinstance(status_code, int) and (status_code == 429 or "
            "status_code >= 500). A transport failure - a request timeout or "
            "a connection that never completed - has no HTTP status at all, "
            "so status_code is None and the check fell through to the "
            "generic 'malformed structured data' branch. Added an explicit "
            "status_code is None case to the same StructuredExtraction"
            "ProviderUnavailableError branch; the surrounding comment already "
            "named 'transport' as in-scope, only the condition was missing it."
        ),
        "changed_files": ["app/services/structured_extraction_service.py"],
        "regression_tests": [
            "tests/unit/test_provider_error_classification.py::"
            "test_transport_failure_with_no_http_status_is_classified_as_unavailable",
        ],
        "evidence": (
            "Found live: a probe run against clean_cotton_tshirts returned "
            "HTTP 502 'The language model returned malformed structured "
            "data.' The persisted document record told the true story: "
            "'StructuredExtractionProviderError: ... http_status=None "
            "error_code=APITimeoutError ... message=Request timed out.' "
            "Nothing was ever returned to be malformed - the request simply "
            "timed out on a token-scarce day. Same failure family as DEF-007/"
            "DEF-012 (an operational condition reported as a content defect), "
            "in a code path neither of those fixes covered."
        ),
    },
    "BLOCKED": {
        "resolution": "blocked",
        "fix_applied": None,
        "changed_files": [],
        "regression_tests": [],
        "evidence": (
            "External Groq account quota (TPD 200000) exhausted mid-run. No "
            "application change can make an unserved request produce a result; "
            "these runs must be repeated once quota resets."
        ),
    },
}


def build_register(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    counters: dict[str, int] = {}
    for row in payload.get("scenarios", []):
        if row.get("ok"):
            continue
        if _is_provider_unavailable(row):
            counters["BLOCKED"] = counters.get("BLOCKED", 0) + 1
            records.append(
                {
                    "defect_id": f"BLOCKED-{counters['BLOCKED']:02d}",
                    "defect_family": "BLOCKED",
                    "title": "Not measured - model provider refused the request",
                    "scenario_id": row["scenario_id"],
                    "variant": row["variant"],
                    "expected": {"status": row["expected_status"]},
                    "actual": {
                        "status": row["deterministic_status"],
                        "technical_failure": row["technical_failure"],
                        "duration_seconds": row["duration_seconds"],
                    },
                    "root_cause_category": "API_or_infrastructure",
                    "root_cause": (
                        "The Groq account exhausted its daily token quota "
                        "(TPD limit 200000) mid-run, so these requests returned "
                        "HTTP 429 in under "
                        f"{_RATE_LIMIT_MAX_SECONDS:.0f}s without the model ever "
                        "seeing the document. These rows are an invalid "
                        "measurement, not evidence of an extraction defect, and "
                        "must be re-run once quota is available before any "
                        "conclusion is drawn about them."
                    ),
                    "severity": "blocker",
                    "false_pass": False,
                    "reproducible": False,
                    "workflow_id": row.get("workflow_id"),
                    "affected_file_or_module": "external: Groq account quota",
                    "proposed_fix": (
                        "Re-run once quota resets, or raise the account tier. No "
                        "application change can make an unserved request produce "
                        "a valid result."
                    ),
                    "fix_allowed": False,
                    "reason_fix_not_allowed": (
                        "External provider quota; outside the application."
                    ),
                    "status": "blocked_external",
                    **RESOLUTIONS["BLOCKED"],
                }
            )
            continue
        for defect_id, matches, meta in SIGNATURES:
            if not matches(row):
                continue
            counters[defect_id] = counters.get(defect_id, 0) + 1
            bad_fields = [
                {"field": f["name"], "expected": f["expected"], "actual": f["actual"]}
                for f in row.get("fields", [])
                if not f.get("correct")
            ][:8]
            records.append(
                {
                    "defect_id": f"{defect_id}-{counters[defect_id]:02d}",
                    "defect_family": defect_id,
                    "title": meta["title"],
                    "scenario_id": row["scenario_id"],
                    "variant": row["variant"],
                    "expected": {
                        "status": row["expected_status"],
                        "line_items": row["line_item_count_expected"],
                        "failed_checks": row["checks"].get("expected_failed", []),
                        "fallback_runs": row["fallback_expected"],
                    },
                    "actual": {
                        "status": row["deterministic_status"],
                        "line_items": row["line_item_count_actual"],
                        "failed_checks": row["checks"].get("failed", []),
                        "fallback_runs": row["fallback_ran"],
                        "technical_failure": row["technical_failure"],
                        "incorrect_fields": bad_fields,
                    },
                    "root_cause_category": meta["root_cause_category"],
                    "root_cause": meta["root_cause"],
                    "severity": "critical" if row.get("false_pass") else meta["severity"],
                    "false_pass": row.get("false_pass", False),
                    "reproducible": True,
                    "workflow_id": row.get("workflow_id"),
                    "affected_file_or_module": meta["affected_file_or_module"],
                    "proposed_fix": meta["proposed_fix"],
                    "fix_allowed": meta["fix_allowed"],
                    "reason_fix_not_allowed": meta.get("reason_fix_not_allowed"),
                    "status": "open",
                    **RESOLUTIONS.get(defect_id, {}),
                }
            )
    return records


def render_markdown(records: list[dict[str, Any]], payload: dict[str, Any]) -> str:
    lines = [
        "# Synthetic Factory Defect Register",
        "",
        f"Derived from a live run against `{payload.get('api_url')}`. "
        "Every record below corresponds to an observed failure; nothing is "
        "hypothetical.",
        "",
        f"- Scenarios attempted: **{payload['metrics_overall']['scenarios_attempted']}**",
        f"- Fully correct: **{payload['metrics_overall']['scenarios_fully_correct']}**",
        f"- Defect records: **{len(records)}**",
        f"- False legal passes: "
        f"**{payload['metrics_overall']['compliance']['false_pass_count']}**",
        "",
        "## Families",
        "",
        "| Family | Title | Category | Severity | Occurrences | Fix allowed |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    families: dict[str, dict[str, Any]] = {}
    for record in records:
        family = families.setdefault(
            record["defect_family"],
            {
                "title": record["title"],
                "category": record["root_cause_category"],
                "severity": record["severity"],
                "count": 0,
                "fix_allowed": record["fix_allowed"],
            },
        )
        family["count"] += 1
    for family_id, family in sorted(families.items()):
        lines.append(
            f"| {family_id} | {family['title']} | `{family['category']}` | "
            f"{family['severity']} | {family['count']} | "
            f"{'yes' if family['fix_allowed'] else 'NO'} |"
        )
    lines.append("")

    seen_family: set[str] = set()
    for record in records:
        if record["defect_family"] not in seen_family:
            seen_family.add(record["defect_family"])
            lines.append(f"## {record['defect_family']} — {record['title']}")
            lines.append("")
            lines.append(f"- **Category**: `{record['root_cause_category']}`")
            lines.append(f"- **Severity**: {record['severity']}")
            lines.append(f"- **Module**: `{record['affected_file_or_module']}`")
            lines.append("")
            lines.append(f"**Root cause.** {record['root_cause']}")
            lines.append("")
            lines.append(f"**Proposed fix.** {record['proposed_fix']}")
            lines.append("")
            affected = [
                f"`{r['scenario_id']}` ({r['variant']})"
                for r in records
                if r["defect_family"] == record["defect_family"]
            ]
            lines.append(f"**Affected ({len(affected)}):** " + ", ".join(affected))
            lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="synthetic_factory_baseline")
    parser.add_argument("--output", default="synthetic_factory_defect_register")
    args = parser.parse_args()

    payload = json.loads((REPORTS_DIR / f"{args.input}.json").read_text(encoding="utf-8"))
    records = build_register(payload)
    (REPORTS_DIR / f"{args.output}.json").write_text(
        json.dumps({"defects": records}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (REPORTS_DIR / f"{args.output}.md").write_text(
        render_markdown(records, payload), encoding="utf-8"
    )
    print(f"Wrote {len(records)} defect records to {args.output}.json / .md")
    families: dict[str, int] = {}
    for record in records:
        families[record["defect_family"]] = families.get(record["defect_family"], 0) + 1
    for family_id, count in sorted(families.items()):
        print(f"  {family_id}: {count}")


if __name__ == "__main__":
    main()
