"""The one Groq explanation call a finalized shipment may receive.

Python has already produced the deterministic verdict by the time this module
runs (`deterministic_compliance` freezes it; `build_final_report` assembles
it). This module only explains an already-final result - it never computes
one. Groq receives a small, bounded findings payload (status, failed checks,
missing fields, human-review status), never raw documents, never the full
compliance rule set, and its system prompt forbids changing the status or
inventing findings. A deterministic template produces the same information
when Groq is unavailable, so the audit's success never depends on the
explanation provider.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.exceptions import StructuredExtractionProviderUnavailableError
from app.services.customs_audit.agents import NarratorFn, narrate_with_source

EXPLANATION_PROMPT_VERSION = "v1"


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
    """The compact, bounded payload sent to Groq - never raw documents."""
    deterministic = state.get("deterministic_compliance_result") or {}
    failed_checks = sorted(
        {
            str(item.get("item_reference"))
            for item in deterministic.get("item_statuses", [])
            if item.get("status") == "failed"
        }
    )
    missing_fields = sorted(set(state.get("manual_review_reasons") or []))
    return {
        "status": final_report.get("deterministic_compliance_status"),
        "failed_checks": failed_checks,
        "missing_fields": missing_fields,
        "human_review_status": (
            "required_and_resolved" if state.get("human_review_decision") else "not_required"
        ),
    }


def _default_explanation(role: str, findings: dict[str, Any]) -> str:
    """Deterministic fallback: uses only the same verified findings Groq
    would have received, never inventing or omitting one."""
    status = findings.get("status", "unknown")
    failed = findings.get("failed_checks") or []
    missing = findings.get("missing_fields") or []
    lines = [f"Status: {status}", ""]
    if not failed and not missing:
        lines.append("The shipment passed every configured compliance check.")
    else:
        lines.append("The shipment did not pass all configured compliance checks.")
        lines.append("")
        lines.append("Issues found:")
        counter = 1
        for reference in failed:
            lines.append(f"{counter}. Compliance check failed for {reference}.")
            counter += 1
        for field in missing:
            lines.append(f"{counter}. {field}")
            counter += 1
        lines.append("")
        lines.append(
            "Required action: correct the listed fields or submit the shipment "
            "for supervisor review."
        )
    if findings.get("human_review_status") == "required_and_resolved":
        lines.append("")
        lines.append("Human review was required and has been completed.")
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
