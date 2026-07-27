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

EXPLANATION_PROMPT_VERSION = "v3"

#: A narrator answer below this bar is not usable by the person who has to
#: present the result, so the detailed deterministic template is used instead.
#: The provider occasionally returns a two-sentence summary despite the prompt
#: (or gets cut off mid-answer), and a terse answer is worse than no answer:
#: the reader cannot tell a missing supporting document apart from a defective
#: uploaded one.
MIN_EXPLANATION_WORDS = 70
_REQUIRED_SECTIONS = ("decision", "why this decision", "next steps")
_MIN_REQUIRED_SECTIONS = 2

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

    documents_to_obtain = _bounded_unique(
        [
            f"{document.get('document')} ({str(document.get('requirement') or '').lower()})"
            for document in report.get("documents_to_obtain") or []
        ],
        limit=_MAX_ISSUES,
    )

    return {
        "status": str(status),
        "uploaded_document_result": _bounded_text(
            report.get("uploaded_document_result")
        ),
        "documents_to_obtain": documents_to_obtain,
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


def _status_headline(status: str) -> str:
    """Say what the raw status word means, next to the status word itself."""
    return {
        "passed": "ready for the next submission step",
        "failed": "not ready for customs submission",
        "manual_review": "a person must review it before submission",
    }.get(status, "see the decision below")


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
        f"Status: {status} ({_status_headline(status)})",
        "",
        "Decision",
        _decision_text(status),
    ]
    decision_summary = _bounded_text(findings.get("decision_summary"))
    if decision_summary:
        lines.append(decision_summary)

    documents_to_obtain = findings.get("documents_to_obtain") or []
    uploaded_result = str(findings.get("uploaded_document_result") or "")
    if uploaded_result == "PASSED" and status != "passed" and documents_to_obtain:
        lines.append(
            "The commercial invoice and the packing list that were uploaded "
            "passed every check that can be made on them. The shipment is held "
            "only by customs documents that are not in hand yet, listed below."
        )

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

    missing_document_count = sum(
        1 for issue in issues if issue.get("category") == "Missing supporting document"
    )
    if missing_document_count:
        if missing_document_count > 1:
            identified = (
                "The additional documents listed above were identified by the "
                "rules as supporting evidence needed before submission. They "
                "were not required to start the audit"
            )
        else:
            identified = (
                "The additional document listed above was identified by a "
                "rule as supporting evidence needed before submission. It was "
                "not required to start the audit"
            )
        lines.extend(
            [
                "",
                "What this means",
                (
                    "The commercial invoice and packing list were enough to "
                    "start and complete this review, and both were read "
                    f"successfully. {identified}, and nothing is wrong with "
                    "the two files that were uploaded."
                ),
                (
                    "Documents of this kind are issued by an outside body "
                    "rather than produced by this system, so each one has to "
                    "be obtained and filed with the shipment. They cannot be "
                    "derived from the invoice or the packing list."
                ),
                *(
                    ["Documents to obtain:"]
                    + [f"- {document}" for document in documents_to_obtain]
                    if documents_to_obtain
                    else []
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


def explanation_meets_bar(text: str) -> bool:
    """Return whether a narrator answer is detailed enough to show a reader.

    The check is deliberately about shape, not content: the deterministic
    verdict is already fixed, so the only question is whether this prose is
    long enough and organized enough to be read out to a supervisor.
    """
    if not isinstance(text, str):
        return False
    normalized = text.strip()
    if len(normalized.split()) < MIN_EXPLANATION_WORDS:
        return False
    lowered = normalized.casefold()
    present = sum(1 for section in _REQUIRED_SECTIONS if section in lowered)
    return present >= _MIN_REQUIRED_SECTIONS


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

    if source == "llm" and not explanation_meets_bar(text):
        # The provider answered, but too thinly to be presented. Keep the
        # detailed template and record the fallback honestly.
        text = _default_explanation("Explanation", findings)
        source = "template_fallback"

    return {
        "explanation": text,
        "explanation_source": source,
        "fingerprint": fingerprint,
        "cache_hit": False,
    }
