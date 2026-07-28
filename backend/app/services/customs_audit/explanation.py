"""The one Groq explanation call a finalized shipment may receive.

Python has already produced the deterministic verdict by the time this module
runs (`deterministic_compliance` freezes it; `build_final_report` assembles
it). This module only explains an already-final result - it never computes
one. Groq receives a small, bounded findings payload assembled from the
business-readable report (decision, problems, passed checks, regulatory
evidence and required actions), never raw documents, never the full
compliance rule set, and its system prompt forbids changing the status or
inventing findings. A detailed deterministic template produces the same
information in plain language when Groq is unavailable or its answer fails
validation, so the audit's success never depends on the explanation provider,
and a reader with no customs, programming or AI background can follow it.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.core.exceptions import StructuredExtractionProviderUnavailableError
from app.services.customs_audit.agents import NarratorFn, narrate_with_source
from app.services.customs_audit.report import build_audit_report

EXPLANATION_PROMPT_VERSION = "v4"

#: A narrator answer below this bar is not usable by the person who has to
#: present the result, so the detailed deterministic template is used instead.
#: The provider occasionally returns a two-sentence summary despite the prompt
#: (or gets cut off mid-answer), and a terse answer is worse than no answer:
#: the reader cannot tell a missing supporting document apart from a defective
#: uploaded one.
MIN_EXPLANATION_WORDS = 70

#: Every one of these three sections (or a plain-English synonym heading) must
#: be present - unlike the old "any 2 of 3" bar, each is checked individually
#: because the task this answers each fails a different way: a reader who
#: cannot find the decision, the reason, or the next action has not been
#: served, even if the other two are excellent.
_REQUIRED_SECTION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "decision": ("decision",),
    "why this decision": ("why this decision", "why the decision"),
    "what to do next": ("what to do next", "next steps"),
}

#: CACE runs a pre-submission document audit. It has no authority to clear a
#: shipment for customs, authenticate a document externally, or guarantee
#: entry into any destination - only a real customs authority can make those
#: claims. A narrator answer containing any of these is rejected outright,
#: regardless of how well-written it otherwise is, and the deterministic
#: template is used instead. Patterns, not literal phrases, so this is not
#: tied to one destination country or one wording of the same claim.
_PROHIBITED_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bcustoms[\s\-]?clear(?:ed|ance)\b",
        r"\bcleared\s+for\s+(?:customs|entry|export|import)\b",
        r"\bcleared\s+(?:to|for)\s+(?:proceed|enter|ship)\b",
        r"\bapproved\s+by\s+customs\b",
        r"\bofficial(?:ly)?\s+compliant\b",
        r"\bguarantee(?:d)?\s+compliant\b",
        r"\bguarantee(?:s|d)?\s+(?:entry|approval|clearance)\b",
        r"\bcan\s+proceed\s+without\s+any\s+(?:other|further|additional)\s+documents?\b",
        r"\bno\s+(?:additional|further|other)\s+documentation\s+(?:can|will|is)\s+(?:be\s+)?required\b",
        r"\bpermission\s+to\s+enter\b",
        r"\bauthoriz(?:ed|ation)\s+(?:by|from)\s+customs\b",
    )
)


#: A negation word anywhere earlier in the same sentence as a matched claim
#: means the sentence is disclaiming the claim, not making it - e.g. the
#: required disclaimer itself ("this is *not* official customs clearance,
#: external document authentication, or permission to enter China") negates
#: a whole list of claims from one "not", so the negation cue can sit well
#: before the specific item being checked. A fixed character window missed
#: exactly this list-of-three shape; scanning back to the last sentence
#: boundary instead covers any negated list without weakening detection of a
#: real claim, since an unrelated *previous* sentence's "not" never counts.
_NEGATION_WORDS = re.compile(
    r"\b(?:not|never|no|isn't|is not|without|cannot|can't|does not|doesn't)\b",
    re.IGNORECASE,
)
_SENTENCE_BOUNDARY_CHARS = (".", "!", "?", "\n")


def explanation_has_prohibited_claims(text: str) -> str | None:
    """Return the first prohibited claim pattern found, or None if clean.

    CACE's explanation may describe the deterministic result in plain
    language, but it may never claim an authority this software does not
    have - the frozen status is the only compliance decision, and the
    explanation only narrates it. A negated occurrence ("is *not* cleared
    for customs") is the required disclaimer, not a violation, so the text
    from the start of the containing sentence up to each match is checked
    for a negation cue before the match is treated as a real claim.
    """
    if not isinstance(text, str):
        return None
    for pattern in _PROHIBITED_CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            sentence_start = (
                max(text.rfind(char, 0, match.start()) for char in _SENTENCE_BOUNDARY_CHARS)
                + 1
            )
            preceding = text[sentence_start : match.start()]
            if _NEGATION_WORDS.search(preceding):
                continue
            return pattern.pattern
    return None


#: A reader is never shown the machine's internal names for things. Ordinary
#: English prose does not join words with underscores, so this general
#: pattern (not a list of specific ids) catches any raw check_id/status
#: constant that leaked into a narrator answer - "item_quantity_match",
#: "xr_coo_china", "evidence_verified", any future check_id alike.
_SNAKE_CASE_IDENTIFIER = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def explanation_exposes_raw_identifiers(text: str) -> str | None:
    """Return the first raw snake_case identifier found, or None if clean."""
    if not isinstance(text, str):
        return None
    match = _SNAKE_CASE_IDENTIFIER.search(text)
    return match.group(0) if match else None


#: Terms a non-technical reader cannot be expected to already know. Allowed
#: only when explained in the same breath (see ``_EXPLAINED_CUE``) - this is
#: deliberately not "reject any of these words", which would make necessary
#: terminology impossible to use at all.
_JARGON_TERMS = (
    "deterministic",
    "provenance",
    "schema",
    "canonical",
    "embeddings",
    "embedding",
    "reranking",
    "rerank",
    "retrieval",
    "reconciliation",
    "interrupt",
    "checkpoint",
    "consensus",
    "hallucination",
    "bm25",
    "reciprocal rank fusion",
    "cross-encoder",
    "cross encoder",
)
_EXPLAINED_CUE = re.compile(r"\b(?:meaning|means)\b", re.IGNORECASE)
_EXPLAIN_WINDOW_CHARS = 60


def explanation_has_unexplained_jargon(text: str) -> str | None:
    """Return the first jargon term used without an inline explanation.

    A term is "explained" when the word "meaning" or "means" appears within
    a short window right after it - the same shape as every allowed example
    in the writing rules ("Deterministic check, meaning a fixed Python rule
    that gives the same result every time.").
    """
    if not isinstance(text, str):
        return None
    lowered = text.casefold()
    for term in _JARGON_TERMS:
        start = 0
        while True:
            index = lowered.find(term, start)
            if index == -1:
                break
            window_end = min(len(text), index + len(term) + _EXPLAIN_WINDOW_CHARS)
            if not _EXPLAINED_CUE.search(text[index:window_end]):
                return term
            start = index + len(term)
    return None


def explanation_missing_limitation(text: str) -> bool:
    """True unless the mandatory prototype-scope disclaimer is present."""
    if not isinstance(text, str):
        return True
    lowered = text.casefold()
    return not (
        "official customs clearance" in lowered
        and ("permission to enter" in lowered or "entry into" in lowered)
    )


def explanation_is_vague_for_passed(text: str, findings: dict[str, Any]) -> bool:
    """True when a PASSED explanation names no concrete extracted value.

    "Every check passed" is true but tells a reader nothing they could not
    already see in the status word. When line items exist, at least one of
    the first item's real values (PCT code, quantity, price, weight) must
    appear in the text - otherwise the explanation is rejected as too vague
    to be the detailed report this task asks for.
    """
    if findings.get("status") != "passed" or not isinstance(text, str):
        return False
    line_items = findings.get("line_items") or []
    if not line_items:
        return False
    item = line_items[0]
    text_digits = re.sub(r"[^\d]", "", text)
    for key in ("pct_code", "quantity", "unit_price", "line_total", "net_weight", "gross_weight"):
        value_digits = re.sub(r"[^\d]", "", str(item.get(key) or ""))
        if value_digits and value_digits in text_digits:
            return False
    return True


#: Money and PCT-code figures are the highest-stakes concrete facts a reader
#: acts on, so any such figure in the narrator's text must trace back to the
#: findings payload it was given - never a number the model produced on its
#: own. Digit-only substring comparison tolerates reformatting ($, commas,
#: "kg") without needing to know every way a model might restate a number.
_MONEY_TOKEN = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?")
_PCT_CODE_TOKEN = re.compile(r"\b\d{4}\.?\d{4}\b")


def explanation_has_ungrounded_facts(text: str, findings: dict[str, Any]) -> str | None:
    """Return the first money amount or PCT code not present in ``findings``."""
    if not isinstance(text, str):
        return None
    findings_digits = re.sub(r"[^\d]", "", json.dumps(findings, default=str))
    for pattern in (_MONEY_TOKEN, _PCT_CODE_TOKEN):
        for match in pattern.finditer(text):
            digits = re.sub(r"[^\d]", "", match.group(0))
            if len(digits) >= 2 and digits not in findings_digits:
                return match.group(0)
    return None


#: Same grounding principle applied to SRO citations specifically - a reader
#: may act on "check SRO 2486(I)/2025", so that identifier must be one the
#: evidence layer actually accepted, never one the model recalled or invented.
_SRO_TOKEN = re.compile(r"\bSRO\s+(\d{2,4}\s*\(\s*[IVXivx]+\s*\)\s*/\s*\d{4})\b", re.IGNORECASE)


def explanation_has_ungrounded_sro(text: str, findings: dict[str, Any]) -> str | None:
    """Return the first cited SRO number not present in the findings' evidence."""
    if not isinstance(text, str):
        return None
    grounded: set[str] = set()
    for row in findings.get("regulatory_evidence") or []:
        for citation in row.get("citations") or []:
            sro = citation.get("sro_number")
            if sro:
                grounded.add(re.sub(r"\s+", "", str(sro)).casefold())
    for match in _SRO_TOKEN.finditer(text):
        cited = re.sub(r"\s+", "", match.group(1)).casefold()
        if cited not in grounded:
            return match.group(0)
    return None


def explanation_validation_failure(text: str, findings: dict[str, Any]) -> str | None:
    """Return the first reason ``text`` may not be shown to a user, else None.

    Every check here has a corresponding regression test; this is the single
    place all of them are combined so ``generate_explanation_entry`` has one
    gate instead of a growing chain of ad-hoc conditions.
    """
    if not isinstance(text, str) or not text.strip():
        return "empty"
    if explanation_has_prohibited_claims(text):
        return "prohibited_claim"
    if explanation_exposes_raw_identifiers(text):
        return "raw_identifier_exposed"
    if explanation_has_unexplained_jargon(text):
        return "unexplained_jargon"
    if not explanation_meets_bar(text):
        return "missing_required_structure_or_too_short"
    if explanation_missing_limitation(text):
        return "missing_limitation"
    if explanation_is_vague_for_passed(text, findings):
        return "vague_passed_explanation"
    if explanation_has_ungrounded_facts(text, findings):
        return "ungrounded_fact"
    if explanation_has_ungrounded_sro(text, findings):
        return "ungrounded_citation"
    return None


_MAX_ISSUES = 8
_MAX_ACTIONS = 6
_MAX_PASSED_CHECKS = 6
_MAX_TEXT_LENGTH = 280
_MAX_LINE_ITEMS = 5
_MAX_REGULATORY_ITEMS = 6
_MAX_CITATIONS_PER_REQUIREMENT = 2
_MAX_DOCUMENTS_CHECKED = 8
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


def _bounded_line_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    for item in items[:_MAX_LINE_ITEMS]:
        bounded.append(
            {
                "product_name": _bounded_scalar(item.get("product_name")),
                "pct_code": _bounded_scalar(item.get("pct_code")),
                "quantity": _bounded_scalar(item.get("quantity")),
                "unit": _bounded_scalar(item.get("unit")),
                "unit_price": _bounded_scalar(item.get("unit_price")),
                "line_total": _bounded_scalar(item.get("line_total")),
                "net_weight": _bounded_scalar(item.get("net_weight")),
                "gross_weight": _bounded_scalar(item.get("gross_weight")),
            }
        )
    return bounded


def _bounded_regulatory_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    for row in rows[:_MAX_REGULATORY_ITEMS]:
        citations = []
        for citation in (row.get("citations") or [])[:_MAX_CITATIONS_PER_REQUIREMENT]:
            citations.append(
                {
                    "source": _bounded_text(citation.get("source_title")),
                    "page": citation.get("page_number"),
                    "section": _bounded_text(citation.get("section")),
                    "excerpt": _bounded_text(citation.get("snippet")),
                    "sro_number": citation.get("sro_number"),
                }
            )
        bounded.append(
            {
                "requirement": _bounded_text(row.get("requirement")),
                "evidence_status": row.get("evidence_status"),
                "citations": citations,
            }
        )
    return bounded


def _bounded_system_scope(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    for row in rows[:_MAX_REGULATORY_ITEMS]:
        bounded.append(
            {
                "requirement": _bounded_text(row.get("requirement")),
                "statement": _bounded_text(row.get("statement")),
            }
        )
    return bounded


def build_explanation_findings(
    state: dict[str, Any], final_report: dict[str, Any]
) -> dict[str, Any]:
    """Return a compact, bounded, business-readable payload for Groq.

    Only allow-listed fields from :func:`build_audit_report` are copied. The
    extraction result, uploaded document text, retrieved evidence chunks and
    full rule set are never serialized into this payload. Requirement names
    come from each check's human ``check_name``/``requirement`` label, never
    its ``check_id`` - the raw identifier is never copied into this payload
    in the first place, so it cannot leak into a narrator answer from here.
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
    line_items = _bounded_line_items(report.get("line_items") or [])
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
    documents_checked = _bounded_unique(
        [
            "Commercial invoice",
            "Packing list",
            *[
                row.get("required_document_type")
                for row in report.get("supporting_documents") or []
                if str(row.get("uploaded") or "") == "Yes"
            ],
        ],
        limit=_MAX_DOCUMENTS_CHECKED,
    )

    return {
        "status": str(status),
        "uploaded_document_result": _bounded_text(
            report.get("uploaded_document_result")
        ),
        "documents_to_obtain": documents_to_obtain,
        "documents_checked": documents_checked,
        "decision_summary": _bounded_text(report.get("overall_reason")),
        "shipment_context": shipment_context,
        "line_items": line_items,
        "passed_checks": passed_checks,
        "issues": issues,
        "required_actions": required_actions,
        "human_review_status": human_review_status,
        "regulatory_evidence": _bounded_regulatory_evidence(
            report.get("regulatory_evidence") or []
        ),
        "system_scope": _bounded_system_scope(report.get("system_scope") or []),
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


def _join_list(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return ", ".join(values[:-1]) + f" and {values[-1]}"


_STANDARD_FIELDS_CHECKED = (
    "exporter, buyer, invoice number, product, PCT code (the tariff code that "
    "identifies the product), quantity, weights, destination and invoice value"
)


def _decision_section(status: str, findings: dict[str, Any]) -> list[str]:
    documents_checked = findings.get("documents_checked") or []
    if status == "passed":
        count = len(documents_checked) or 2
        return [
            "The CACE pre-submission document audit passed.",
            (
                f"The {count} uploaded documents matched, and no required "
                "document was missing under the rules configured for this "
                "test case."
            ),
        ]
    if status == "failed":
        issues = findings.get("issues") or []
        if issues:
            detail = issues[0]["detail"]
            return [f"The document audit failed. {detail}"]
        return ["The document audit failed a required check."]
    if status == "manual_review":
        issues = findings.get("issues") or []
        lines = ["The system could not complete the audit automatically."]
        if issues:
            lines.append(f"A person must confirm this: {issues[0]['detail']}")
        else:
            lines.append("A person must review the flagged items before submission.")
        return lines
    return [
        "The workflow produced the status shown above.",
        "Review the details below before taking the next operational step.",
    ]


def _what_the_system_checked_section(findings: dict[str, Any]) -> list[str]:
    documents_checked = findings.get("documents_checked") or []
    lines = []
    if documents_checked:
        lines.append(f"The system checked the {_join_list(documents_checked)}.")
    else:
        lines.append(
            "The system checked the commercial invoice and packing list that "
            "were uploaded."
        )
    lines.append(f"It compared the {_STANDARD_FIELDS_CHECKED}.")
    return lines


def _why_this_decision_section(status: str, findings: dict[str, Any]) -> list[str]:
    issues = findings.get("issues") or []
    if status == "passed":
        lines: list[str] = []
        for item in findings.get("line_items") or []:
            product = item.get("product_name") or "the product"
            quantity = item.get("quantity")
            pct_code = item.get("pct_code")
            net = item.get("net_weight")
            gross = item.get("gross_weight")
            unit_price = item.get("unit_price")
            line_total = item.get("line_total")
            if quantity is not None:
                lines.append(
                    f"The invoice and packing list both describe {quantity} "
                    f"units of {product}."
                )
            if pct_code:
                lines.append(
                    f"The PCT code (the tariff code that identifies the "
                    f"product) is {pct_code} on both documents."
                )
            if net is not None:
                lines.append(f"The net weight is {net} kg in both documents.")
            if gross is not None:
                lines.append(f"The gross weight is {gross} kg in both documents.")
            if quantity is not None and unit_price is not None and line_total is not None:
                lines.append(
                    "The invoice calculation is correct: "
                    f"{quantity} pieces × ${unit_price} = ${line_total}."
                )
        if not lines:
            lines.append("The shipment passed every configured compliance check.")
        return lines

    if not issues:
        return ["The shipment passed every configured compliance check."]
    lines = []
    for issue in issues:
        lines.append(f"{issue.get('category', 'Finding')}: {issue.get('detail', '')}")
    missing_document_count = sum(
        1 for issue in issues if issue.get("category") == "Missing supporting document"
    )
    if missing_document_count and str(findings.get("uploaded_document_result") or "") == "PASSED":
        lines.append(
            "The commercial invoice and packing list were read successfully "
            "and passed every check that can be made on them. The documents "
            "listed above are issued by an outside body, so they still need "
            "to be obtained and are not a defect in the files that were "
            "uploaded."
        )
    return lines


_EVIDENCE_STATUS_SENTENCES = {
    "evidence_verified": "The system found relevant evidence for the {requirement} requirement{source}.",
    "evidence_partial": "The system found evidence for the {requirement} requirement{source}, but it only partly confirms the rule.",
    "evidence_conflicting": (
        "The regulatory sources found for the {requirement} requirement do "
        "not agree with each other, so a person should confirm the rule directly."
    ),
    "evidence_unavailable": (
        "The indexed regulatory documents did not contain a sufficiently "
        "relevant passage for the {requirement} requirement."
    ),
}


def _regulatory_evidence_section(findings: dict[str, Any]) -> list[str]:
    regulatory_evidence = findings.get("regulatory_evidence") or []
    system_scope = findings.get("system_scope") or []
    if not regulatory_evidence and not system_scope:
        return [
            "This shipment did not have any requirements that needed a "
            "citation to a regulatory source."
        ]
    lines: list[str] = []
    for row in regulatory_evidence:
        requirement = row.get("requirement") or "this requirement"
        status = str(row.get("evidence_status") or "evidence_unavailable")
        citations = row.get("citations") or []
        source = ""
        if citations:
            first = citations[0]
            page = f", page {first.get('page')}" if first.get("page") else ""
            source = f", from {first.get('source')}{page}" if first.get("source") else ""
        template = _EVIDENCE_STATUS_SENTENCES.get(
            status, _EVIDENCE_STATUS_SENTENCES["evidence_unavailable"]
        )
        lines.append(template.format(requirement=requirement, source=source))
        if citations:
            lines.append(
                "Each accepted citation includes the source document, page "
                "and relevant passage."
            )
    for row in system_scope:
        statement = row.get("statement")
        if statement:
            lines.append(statement)
    if regulatory_evidence:
        lines.append(
            "Unrelated passages were rejected and were not shown as evidence."
        )
    return lines


def _default_explanation(role: str, findings: dict[str, Any]) -> str:
    """Write a detailed, plain-language explanation from the frozen findings.

    Six sections, always in this order: Decision, What the system checked,
    Why this decision, Regulatory evidence, What to do next, Limitations. No
    section is dropped even when its content is short, so the structure a
    reader learns to expect never disappears.
    """
    status = str(findings.get("status") or "unknown")
    actions = findings.get("required_actions") or []

    lines: list[str] = ["Decision", *_decision_section(status, findings)]

    lines.extend(["", "What the system checked", *_what_the_system_checked_section(findings)])

    lines.extend(["", "Why this decision", *_why_this_decision_section(status, findings)])

    lines.extend(["", "Regulatory evidence", *_regulatory_evidence_section(findings)])

    lines.extend(["", "What to do next"])
    if actions:
        for index, action in enumerate(actions, start=1):
            lines.append(f"{index}. {action}")
    elif status == "passed":
        lines.extend(
            [
                "1. Keep the verified invoice and packing list with the "
                "shipment record.",
                "2. Continue with the normal official submission process.",
                (
                    "3. Ask a qualified customs professional to review any "
                    "requirement outside this prototype's configured scope."
                ),
            ]
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

    destination = findings.get("shipment_context", {}).get("destination")
    entry_clause = f"entry into {destination}" if destination else "entry into the destination country"
    lines.extend(
        [
            "",
            "Limitations",
            (
                "This is a prototype pre-submission document audit. It is "
                f"not official customs clearance, external document "
                f"authentication or permission to enter - it does not grant "
                f"{entry_clause}."
            ),
        ]
    )
    return "\n".join(lines)


def explanation_meets_bar(text: str) -> bool:
    """Return whether a narrator answer is long and structured enough to show.

    Every one of Decision / Why this decision / What to do next (or its
    "Next steps" synonym) must be present - the deterministic verdict is
    already fixed, so what is being checked is whether this prose gives a
    reader everything the required structure promises.
    """
    if not isinstance(text, str):
        return False
    normalized = text.strip()
    if len(normalized.split()) < MIN_EXPLANATION_WORDS:
        return False
    lowered = normalized.casefold()
    for synonyms in _REQUIRED_SECTION_SYNONYMS.values():
        if not any(synonym in lowered for synonym in synonyms):
            return False
    return True


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

    rejection_reason: str | None = None
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
        rejection_reason = "provider_unavailable"

    if source == "llm":
        rejection_reason = explanation_validation_failure(text, findings)
        if rejection_reason:
            text = _default_explanation("Explanation", findings)
            source = "template_fallback"

    return {
        "explanation": text,
        "explanation_source": source,
        "fingerprint": fingerprint,
        "cache_hit": False,
        "explanation_rejection_reason": rejection_reason,
    }
