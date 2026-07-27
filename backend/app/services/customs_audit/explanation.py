"""The one Groq explanation call a finalized shipment may receive.

Python has already produced the deterministic verdict by the time this module
runs (`deterministic_compliance` freezes it; `build_final_report` assembles
it). This module only explains an already-final result - it never computes
one. Groq receives a small, bounded findings payload assembled from the
business-readable report (decision, problems, passed checks and required
actions), never raw documents, never the full compliance rule set, and its
system prompt forbids changing the status or inventing findings. A detailed
deterministic template produces the same information when Groq is unavailable,
so the audit's success never depends on the explanation provider.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.exceptions import StructuredExtractionProviderUnavailableError
from app.services.customs_audit.agents import NarratorFn, narrate_with_source
from app.services.customs_audit.report import build_audit_report

EXPLANATION_PROMPT_VERSION = "v2"

_MAX_ISSUES = 8
_MAX_ACTIONS = 6
_MAX_PASSED_CHECKS = 6
_MAX_TEXT_LENGTH = 280
_PROBLEM_LABELS = {
    "missing_documents": "Missing supporting document",
    "missing_or_uncertain_fields": "Information requiring confirmation",
    "document_mismatches": "Invoice and packing-list mismatch",
    "calculation_errors": "Calculation error",
    "regulatory_problems": "Regulatory requirement",
    "evidence_limitations": "Regulatory evidence limitation",
}


def explanation_fingerprint(findings: dict[str, Any], *, model: str) -> str:
    """Stable identity for a (findings, model, prompt version) triple.

    ``findings`` already contains only stable, already-computed fields, so an
    unchanged deterministic result always produces the same fingerprint -
    this is the explanation cache key.
    """
    payload = {
        "findings": findings,
        "model": model,
        "prompt_version": EXPLANATION_PROMPT_VERSION,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest[:32]}"


def build_explanation_findings(
    state: dict[str, Any], final_report: dict[str, Any]
) -> dict[str, Any]:
    """Return a compact, bounded, business-readable payload for Groq.

    Only allow-listed fields from :func:`build_audit_report` are copied. The
    extraction result, uploaded document text, retrieved evidence chunks and
    full rule set are never serialized into this payload.
    """
    report = build_audit_report(state)
    problems = report.get("problems") or {}

    issues: list[dict[str, str]] = []
    issue_keys: set[str] = set()
    for bucket, label in _PROBLEM_LABELS.items():
        for value in problems.get(bucket) or []:
            detail = _bounded_text(value)
            if not detail:
                continue
            key = detail.casefold()
            if key in issue_keys:
                continue
            issue_keys.add(key)
            issues.append({"category": label, "detail": detail})
            if len(issues) >= _MAX_ISSUES:
                break
        if len(issues) >= _MAX_ISSUES:
            break

    passed_checks = _bounded_unique(
        report.get("checks_passed") or [], limit=_MAX_PASSED_CHECKS
    )
    required_actions = _bounded_unique(
        report.get("required_actions") or [], limit=_MAX_ACTIONS
    )
    has_review_reasons = bool(state.get("manual_review_reasons"))

    summary = report.get("shipment_summary") or {}
    shipment_context = {
        key: _bounded_scalar(summary.get(key))
        for key in ("invoice_number", "destination", "shipment_date")
        if _bounded_scalar(summary.get(key)) is not None
    }
    item_count = len(report.get("line_items") or [])
    if item_count:
        shipment_context["item_count"] = item_count

    status = final_report.get("deterministic_compliance_status")
    if status is None:
        status = (
            (state.get("deterministic_compliance_result") or {}).get(
                "overall_status"
            )
            or "unknown"
        )
    if state.get("human_review_decision"):
        human_review_status = "required_and_resolved"
    elif has_review_reasons or status == "manual_review":
        human_review_status = "required"
    else:
        human_review_status = "not_required"

    return {
        "status": str(status),
        "decision_summary": _bounded_text(report.get("overall_reason")),
        "shipment_context": shipment_context,
        "passed_checks": passed_checks,
        "issues": issues,
        "required_actions": required_actions,
        "human_review_status": human_review_status,
    }


def _bounded_text(value: Any) -> str:
    """Normalize one display string and enforce the explanation payload bound."""
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if len(text) <= _MAX_TEXT_LENGTH:
        return text
    return text[: _MAX_TEXT_LENGTH - 1].rstrip() + "…"


def _bounded_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = _bounded_text(value)
    return text or None


def _bounded_unique(values: list[Any], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _bounded_text(value)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _decision_text(status: str) -> str:
    if status == "passed":
        return (
            "The shipment passed the compliance checks configured for this "
            "capstone and is ready for the next submission step."
        )
    if status == "failed":
        return (
            "The shipment is not ready for customs submission because at least "
            "one required check failed."
        )
    if status == "manual_review":
        return (
            "The system could not safely make a final decision from the "
            "available information, so a person must review the flagged items."
        )
    return (
        "The workflow produced the status shown above. Review the details below "
        "before taking the next operational step."
    )


def _default_explanation(role: str, findings: dict[str, Any]) -> str:
    """Write a detailed explanation using only the bounded verified findings."""
    status = str(findings.get("status") or "unknown")
    issues = findings.get("issues") or []
    actions = findings.get("required_actions") or []
    passed = findings.get("passed_checks") or []

    lines = [
        f"Status: {status}",
        "",
        "Decision",
        _decision_text(status),
    ]
    decision_summary = _bounded_text(findings.get("decision_summary"))
    if decision_summary:
        lines.append(decision_summary)

    lines.extend(["", "What was checked"])
    if passed:
        lines.append(
            "The engine extracted the invoice and packing-list data, compared "
            "the shipment records, and confirmed these checks:"
        )
        for value in passed:
            lines.append(f"- {value}.")
    else:
        lines.append(
            "The engine extracted and compared the available invoice and "
            "packing-list data before applying the configured rules."
        )

    lines.extend(["", "Why this decision"])
    if issues:
        for index, issue in enumerate(issues, start=1):
            lines.append(
                f"{index}. {issue.get('category', 'Finding')}: "
                f"{issue.get('detail', '')}"
            )
    else:
        lines.append("The shipment passed every configured compliance check.")

    if any(
        issue.get("category") == "Missing supporting document" for issue in issues
    ):
        lines.extend(
            [
                "",
                "What this means",
                (
                    "The commercial invoice and packing list were enough to "
                    "start and complete this review. The additional document "
                    "listed above was identified by a rule as supporting "
                    "evidence needed before submission; it was not required to "
                    "start the audit."
                ),
            ]
        )

    lines.extend(["", "Next steps"])
    if actions:
        for index, action in enumerate(actions, start=1):
            lines.append(f"{index}. {action}")
    elif status == "passed":
        lines.append(
            "1. Keep the verified invoice and packing list with the shipment "
            "record and continue the normal submission process."
        )
    else:
        lines.append(
            "1. Review the listed finding against the source documents and "
            "have an authorized person confirm the correction."
        )

    review_status = findings.get("human_review_status")
    if review_status == "required_and_resolved":
        lines.extend(
            ["", "Human review", "A required human review has been completed."]
        )
    elif review_status == "required":
        lines.extend(
            [
                "",
                "Human review",
                "A person must confirm the unresolved points before submission.",
            ]
        )
    return "\n".join(lines)


def generate_explanation_entry(
    *,
    state: dict[str, Any],
    final_report: dict[str, Any],
    narrator: NarratorFn | None,
    model_label: str,
) -> dict[str, Any]:
    """Return a cached or freshly generated explanation entry.

    Groq is called at most once per finalized shipment: if a prior entry in
    ``state["explanation_results"]`` already carries the current fingerprint
    (the deterministic result has not changed - e.g. across a retry that
    reached the same verdict), it is reused and no call is made.
    """
    findings = build_explanation_findings(state, final_report)
    fingerprint = explanation_fingerprint(findings, model=model_label)
    for cached in reversed(state.get("explanation_results") or []):
        if cached.get("fingerprint") == fingerprint:
            return {**cached, "cache_hit": True}

    try:
        text, source = narrate_with_source(
            narrator, "Explanation", findings, default=_default_explanation
        )
    except StructuredExtractionProviderUnavailableError:
        # Unlike Broker/Auditor narration, this call happens after the
        # verdict is already finalized and checkpointed. Propagating here
        # would strand an already-correct, already-checkpointed result in
        # RUNNING with no retry path (retry() only accepts FAILED workflows)
        # - worse than falling back to the template, so it is swallowed here
        # rather than re-raised.
        text = _default_explanation("Explanation", findings)
        source = "template_fallback"

    return {
        "explanation": text,
        "explanation_source": source,
        "fingerprint": fingerprint,
        "cache_hit": False,
    }
