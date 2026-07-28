"""Build one business-readable audit report from the workflow state.

This is a pure formatter: it reads the values the pipeline already produced
(extraction result, matched items, deterministic checks, agent reports and
consensus) and returns a plain dict. It never runs a model, adds a rule or
changes a status. It is used both while the workflow is paused for human review
and after it completes, so the user always sees the real extracted shipment
instead of empty placeholders.
"""

from __future__ import annotations

from typing import Any

from app.services.customs_audit.state import utcnow_iso
from app.services.compliance.document_requirements import (
    collect_outstanding_documents,
    is_outstanding_document_check,
)
from app.services.multi_line.field_paths import InvalidFieldPathError, parse_field_path

# --------------------------------------------------------------------------- #
# Check categorisation. Every id below is an existing deterministic check.
# --------------------------------------------------------------------------- #
_DOCUMENT_PRESENCE_IDS = {
    "required_document_form_e",
    "required_document_commercial_invoice",
    "required_document_packing_list",
}
_ARITHMETIC_IDS = {
    "item_line_calculation",
    "invoice_line_calculation",
    "sum_line_totals_match_invoice_total",
    "invoice_total_consistency",
    "positive_quantity",
    "positive_unit_price",
    "weight_consistency",
}
_MISMATCH_IDS = {
    "item_quantity_match",
    "item_net_weight_match",
    "item_gross_weight_match",
    "item_pct_code_match",
    "duplicate_invoice_lines",
    "duplicate_packing_items",
    "invoice_net_weight_total",
    "invoice_gross_weight_total",
    "packing_net_weight_total",
    "packing_gross_weight_total",
}
_UNCERTAIN_FIELD_IDS = {"required_fields", "items_present_in_both_documents"}

_DOCUMENT_NAMES = {
    "form_e": "Form-E",
    "commercial_invoice": "Commercial invoice",
    "packing_list": "Packing list",
    "certificate_of_origin": "Certificate of origin",
    "phytosanitary_certificate": "Phytosanitary certificate",
    "product_certificate": "Product certificate",
    "product_licence": "Product licence",
    "product_permit": "Product permit",
    "product_approval": "Product approval",
    "import_permit": "Import permit",
    "sbp_deposit_proof": "Proof of SBP deposit",
    "sbp_confirmation": "SBP confirmation",
    "irrevocable_letter_of_credit": "Irrevocable letter of credit",
}

_FIELD_LABELS = {
    "item_net_weight_match": "net weight",
    "item_gross_weight_match": "gross weight",
    "item_quantity_match": "quantity",
    "item_pct_code_match": "PCT code",
}

_MATCH_METHODS = {
    "line_reference": "line reference",
    "pct_code": "PCT code",
    "product_name": "product name",
}


def _fv(field: Any) -> Any:
    """Return the value of an ExtractedField-shaped dict, else None."""
    if isinstance(field, dict):
        return field.get("value")
    return None


def _fpage(field: Any) -> Any:
    if isinstance(field, dict):
        return field.get("source_page")
    return None


def _doc_name(document_type: str | None) -> str:
    if not document_type:
        return "required document"
    return _DOCUMENT_NAMES.get(document_type, document_type.replace("_", " "))


def _missing_document_guidance(document_type: str | None) -> tuple[str, str]:
    """Return one plain-language problem and action for a missing document.

    Invoice and packing-list uploads are the inputs to this review. Other
    documents are supporting customs documents discovered by the rules, not
    extra files the user was expected to provide to start the review.
    """
    doc = _doc_name(document_type)
    if document_type in {"commercial_invoice", "packing_list"}:
        return (
            f"{doc} is missing, so the two-document review could not be completed.",
            f"Provide the {doc.lower()} and run the review again.",
        )
    return (
        (
            f"The invoice and packing list were processed, but {doc} was not "
            "provided as a supporting document."
        ),
        (
            f"Obtain {doc} from the body that issues it and file it with the "
            "shipment documents before customs submission."
        ),
    )


def _shipment_summary(invoice: dict, packing: dict, state: dict) -> dict[str, Any]:
    packages = [
        _fv(item.get("package_count"))
        for item in packing.get("items", [])
        if _fv(item.get("package_count")) is not None
    ]
    total_packages = None
    if packages:
        try:
            total_packages = sum(int(value) for value in packages)
        except (TypeError, ValueError):
            total_packages = None
    return {
        "exporter": _fv(invoice.get("exporter_name")),
        "buyer": _fv(invoice.get("buyer_name")),
        "invoice_number": _fv(invoice.get("invoice_number")),
        "invoice_date": _fv(invoice.get("invoice_date")),
        "destination": _fv(invoice.get("destination_country")),
        "shipment_date": state.get("shipment_date"),
        "total_invoice_value": _fv(invoice.get("invoice_total")),
        "currency": _fv(invoice.get("currency")),
        "total_packages": total_packages,
        "declared_net_weight": _fv(invoice.get("declared_net_weight_total")),
        "declared_gross_weight": _fv(invoice.get("declared_gross_weight_total")),
    }


def _line_items(extraction: dict) -> list[dict[str, Any]]:
    invoice = extraction.get("invoice") or {}
    packing = extraction.get("packing_list") or {}
    results = extraction.get("items") or []
    packing_by_index = {
        item.get("item_index"): item for item in packing.get("items", [])
    }
    result_by_invoice_index = {
        result.get("invoice_item_index"): result
        for result in results
        if result.get("invoice_item_index") is not None
    }

    rows: list[dict[str, Any]] = []
    for line in invoice.get("line_items", []):
        index = line.get("item_index")
        result = result_by_invoice_index.get(index, {})
        packing_item = packing_by_index.get(result.get("packing_item_index"))
        match_strategy = result.get("match_strategy")
        rows.append(
            {
                "line_number": _fv(line.get("line_number")),
                "product_name": _fv(line.get("product_name")),
                "pct_code": _fv(line.get("pct_code")),
                "quantity": _fv(line.get("quantity")),
                "unit": _fv(line.get("unit")),
                "unit_price": _fv(line.get("unit_price")),
                "line_total": _fv(line.get("line_total")),
                "net_weight": _fv(line.get("net_weight")),
                "gross_weight": _fv(line.get("gross_weight")),
                "invoice_source_page": line.get("item_source_page"),
                "packing_list_source_page": (
                    packing_item.get("item_source_page") if packing_item else None
                ),
                "match_method": _MATCH_METHODS.get(match_strategy, match_strategy),
                "extraction_confidence": line.get("item_confidence"),
            }
        )
    return rows


def _categorise(check: dict, *, is_compliance: bool) -> tuple[str | None, str, str | None]:
    """Return (bucket, message, suggested_action) for one check.

    bucket is None for a passed check that only needs a brief mention.
    """
    check_id = check.get("check_id", "")
    status = check.get("status")
    message = check.get("message") or check_id
    name = check.get("check_name") or check_id.replace("_", " ")

    if status in ("passed", "not_applicable"):
        return None, name, None

    if status == "failed":
        if check_id in _DOCUMENT_PRESENCE_IDS:
            message, action = _missing_document_guidance(
                check.get("required_document")
            )
            return "missing_documents", message, action
        if check_id in _ARITHMETIC_IDS:
            return "calculation_errors", message, "Correct the figures so the totals add up."
        if check_id in _MISMATCH_IDS:
            field = _FIELD_LABELS.get(check_id)
            action = (
                f"Correct the {field} so the invoice and packing list agree."
                if field
                else "Reconcile the invoice and packing list."
            )
            return "document_mismatches", message, action
        if is_compliance and check.get("required_document"):
            message, action = _missing_document_guidance(
                check.get("required_document")
            )
            return "missing_documents", message, action
        return "regulatory_problems", message, "Send the case for regulatory review."

    # manual_review (and anything else uncertain)
    if is_compliance:
        note = (message + " " + str(check.get("validation_status") or "")).lower()
        if any(
            marker in note
            for marker in ("effective date", "provenance", "source page", "evidence", "verified")
        ):
            return (
                "evidence_limitations",
                message,
                "Send the case for regulatory review to confirm the legal basis.",
            )
        return (
            "missing_or_uncertain_fields",
            message,
            "Confirm the flagged regulatory requirement.",
        )

    field = _FIELD_LABELS.get(check_id)
    if field:
        return (
            "missing_or_uncertain_fields",
            f"The {field} could not be reliably determined for a line item.",
            f"Confirm the {field} from the invoice.",
        )
    return (
        "missing_or_uncertain_fields",
        message,
        "Confirm the flagged value from the source documents.",
    )


def _collect_checks(extraction: dict) -> tuple[list[dict], list[dict]]:
    cross_document: list[dict] = list(extraction.get("shipment_level_checks") or [])
    compliance: list[dict] = []
    for item in extraction.get("items") or []:
        cross_document.extend(item.get("item_checks") or [])
        result = item.get("compliance") or {}
        compliance.extend(result.get("checks") or [])
        for exe in result.get("executable_rule_checks") or []:
            compliance.append(exe)
    return cross_document, compliance


def _compliance_evidence(compliance: list[dict]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    evidence: list[dict[str, Any]] = []
    for check in compliance:
        source = check.get("source_document")
        authority = check.get("issuing_authority")
        if not source and not authority:
            continue
        key = (check.get("check_name"), source, check.get("sro_number"))
        if key in seen:
            continue
        seen.add(key)
        evidence.append(
            {
                "rule_name": check.get("check_name"),
                "source_document": source,
                "sro_number": check.get("sro_number"),
                "page": check.get("source_page"),
                "issuing_authority": authority,
                "validation_status": check.get("validation_status"),
            }
        )
    return evidence


_RESULT_LABELS = {
    "passed": "PASSED",
    "failed": "FAILED",
    "manual_review": "NEEDS HUMAN REVIEW",
    "rejected": "REJECTED",
    "technical_failure": "TECHNICAL FAILURE",
}


def _overall(state: dict, extraction: dict) -> str:
    workflow_status = state.get("workflow_status")
    if workflow_status == "rejected":
        return "rejected"
    deterministic = state.get("deterministic_compliance_result") or {}
    status = deterministic.get("overall_status") or extraction.get("overall_status")
    if status:
        return str(status)
    # No deterministic result at all: the documents could not be processed.
    if workflow_status == "failed" or not extraction:
        return "technical_failure"
    return "manual_review"


def _reason(result: str, problems: dict[str, list[str]]) -> str:
    if result == "passed":
        return "Every required check passed and no problems were found."
    if result == "rejected":
        return "A human reviewer rejected this submission."
    if result == "technical_failure":
        return "The audit could not finish because the documents could not be processed."
    if result == "failed":
        for bucket in ("missing_documents", "calculation_errors", "document_mismatches", "regulatory_problems"):
            if problems.get(bucket):
                return "The shipment failed: " + problems[bucket][0]
        return "The shipment failed a required check."
    return (
        "Some information is uncertain and needs a person to confirm it before "
        "the shipment can be cleared."
    )


_SUPPORTING_RESULT_LABEL = {
    "passed": "Passed",
    "failed": "Failed",
    "manual_review": "Needs human review",
}


def _match_label(checks: list[dict], fragment: str) -> str:
    for check in checks:
        if fragment in (check.get("check_id") or ""):
            return {
                "passed": "Yes",
                "failed": "No",
                "manual_review": "Could not confirm",
            }.get(str(check.get("status") or ""), "Not applicable")
    return "Not applicable"


def _extracted_value(document: dict, field_name: str) -> Any:
    """Read one extracted field off a supporting document, or None.

    Never falls back to a value from elsewhere: a date that was not printed on
    the page stays absent in the report rather than being filled in from the
    shipment.
    """
    extraction = document.get("extraction") or {}
    field = extraction.get(field_name)
    if not isinstance(field, dict):
        return None
    return field.get("value")


def _supporting_documents(extraction: dict) -> list[dict[str, Any]]:
    """Business-readable view of each required supporting document."""
    rows: list[dict[str, Any]] = []
    for document in extraction.get("supporting_documents") or []:
        checks = document.get("checks") or []
        confidence = document.get("extraction_confidence")
        ocr = document.get("ocr_confidence")
        rows.append(
            {
                "required_document_type": _doc_name(
                    str(document.get("canonical_document_type") or "")
                ),
                # What the caller said it was, kept beside what the page says it
                # is, so a reader can see the claim and the evidence separately.
                "claimed_document_type": _doc_name(
                    str(document.get("claimed_document_type") or "")
                ),
                "uploaded": "Yes" if document.get("uploaded") else "No",
                "detected_document_type": document.get("detected_document_type"),
                "document_number": document.get("document_number"),
                "issue_date": _extracted_value(document, "issue_date"),
                "expiry_date": _extracted_value(document, "expiry_date"),
                "invoice_reference_match": _match_label(checks, "invoice_reference_match"),
                "exporter_match": _match_label(checks, "exporter_match"),
                "destination_match": _match_label(checks, "destination_match"),
                "pct_match": _match_label(checks, "pct_match"),
                "extraction_confidence": (
                    f"{float(confidence) * 100:.0f}%" if confidence is not None else None
                ),
                "ocr_confidence": (
                    f"{float(ocr) * 100:.0f}%" if ocr is not None else None
                ),
                "verification_state": document.get("state"),
                "content_result": _SUPPORTING_RESULT_LABEL.get(
                    document.get("content_status"), document.get("content_status")
                ),
                "source_page": document.get("source_page"),
                "external_authenticity": "Not externally verified",
                "required_action": document.get("required_action"),
            }
        )
    return rows


def _uploaded_document_result(
    extraction: dict, checks: list[dict], *, overall_status: str
) -> str:
    """Label the verdict on the invoice and packing list that were uploaded.

    Rules that are unresolved only because further customs paperwork is still
    outstanding are excluded: they say nothing about the two uploaded files.
    ``overall_status`` keeps its strict meaning and is reported separately.
    """
    if not (extraction.get("items") or []):
        return _RESULT_LABELS["manual_review"]
    if overall_status in ("rejected", "technical_failure"):
        return _RESULT_LABELS.get(overall_status, overall_status.upper())
    statuses = {
        str(check.get("status") or "")
        for check in checks
        if not is_outstanding_document_check(check)
    }
    if "failed" in statuses:
        return _RESULT_LABELS["failed"]
    if "manual_review" in statuses:
        return _RESULT_LABELS["manual_review"]
    return _RESULT_LABELS["passed"]


def _documents_to_obtain(checks: list[dict]) -> list[dict[str, Any]]:
    """The pre-submission document checklist, in business-readable form."""
    return [
        {
            "document": document.display_name,
            "requirement": (
                "Required before submission"
                if document.requirement == "required"
                else "Confirm whether it applies"
            ),
            "reasons": document.reasons,
            "sources": document.sources,
        }
        for document in collect_outstanding_documents(checks)
    ]


#: Plain-language, non-LLM description of the retrieval pipeline, shown by
#: the frontend inside a collapsed "How the evidence search worked" section -
#: never in the main explanation text (see explanation.py's jargon rules).
#: Each idea is stated in plain words first, with the technical name in
#: parentheses second, so the section reads even if the reader skips every
#: parenthetical.
EVIDENCE_SEARCH_EXPLANATION = (
    "The system searched the regulatory documents in two ways. First, it "
    "looked for exact words, such as the PCT code or SRO number (this "
    "exact-word search method is called BM25). Second, it looked for "
    "passages with a similar meaning, even when the exact words were "
    "different (this meaning-based search is called embedding search). The "
    "results from both searches were combined into one ranked list (this "
    "combining step is called Reciprocal Rank Fusion). The system then "
    "re-checked the best candidates one more time and rejected any passage "
    "that did not directly support the requirement (this final check is "
    "called cross-encoder reranking). Only passages that passed every step "
    "were shown as evidence; the underlying scores are available in each "
    "citation's own technical details."
)

_STATUS_LABELS = {"passed": "PASSED", "failed": "FAILED", "manual_review": "MANUAL REVIEW", "not_applicable": "NOT APPLICABLE"}


def _status_label(check: dict[str, Any]) -> str:
    status = str(check.get("status") or "")
    return _STATUS_LABELS.get(status, status)


def _unique_checks_by_id(checks: list[dict]) -> list[dict]:
    """First-seen check per check_id, in encounter order.

    Several rule layers can each emit a check under the same id (see the
    document-checklist consolidation above); the evidence tables need exactly
    one row per id, matching how evidence was gathered upstream.
    """
    seen: dict[str, dict] = {}
    for check in checks:
        check_id = str(check.get("check_id") or "")
        if check_id and check_id not in seen:
            seen[check_id] = check
    return list(seen.values())


def _regulatory_evidence_rows(
    checks: list[dict],
    evidence_by_check: dict[str, list[dict[str, Any]]],
    status_by_check: dict[str, str],
) -> list[dict[str, Any]]:
    """One row per regulatory check, citing what retrieval actually found.

    A check absent from ``evidence_by_check`` was never a regulatory check
    (see ``is_regulatory_check``) and is skipped here - it belongs in
    ``document_evidence`` instead. A check present with an empty list is
    reported as ``evidence_unavailable``, never a fabricated citation.
    """
    rows: list[dict[str, Any]] = []
    for check in _unique_checks_by_id(checks):
        check_id = str(check.get("check_id") or "")
        if check_id not in evidence_by_check and check_id not in status_by_check:
            continue
        citations = evidence_by_check.get(check_id, [])
        rows.append(
            {
                "check_id": check_id,
                "requirement": check.get("check_name") or check_id.replace("_", " "),
                "status": _status_label(check),
                "evidence_status": status_by_check.get(check_id, "evidence_unavailable"),
                "citations": [c for c in citations if c.get("display_primary", True)],
                "additional_citations": [
                    c for c in citations if not c.get("display_primary", True)
                ],
            }
        )
    return rows


def _system_scope_rows(
    checks: list[dict], statements_by_check: dict[str, str]
) -> list[dict[str, Any]]:
    """One row per system-scope check - a software-coverage statement, never
    a regulatory citation (see ``is_system_scope_check``)."""
    rows: list[dict[str, Any]] = []
    for check in _unique_checks_by_id(checks):
        check_id = str(check.get("check_id") or "")
        if check_id not in statements_by_check:
            continue
        rows.append(
            {
                "check_id": check_id,
                "requirement": check.get("check_name") or check_id.replace("_", " "),
                "status": _status_label(check),
                "statement": statements_by_check[check_id],
            }
        )
    return rows


def _document_evidence_rows(
    checks: list[dict], evidence_by_check: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """One row per document-comparison check, citing the extracted values."""
    rows: list[dict[str, Any]] = []
    for check in _unique_checks_by_id(checks):
        check_id = str(check.get("check_id") or "")
        if check_id not in evidence_by_check:
            continue
        rows.append(
            {
                "check_id": check_id,
                "check_name": check.get("check_name") or check_id.replace("_", " "),
                "status": _status_label(check),
                "message": check.get("message"),
                "evidence": evidence_by_check.get(check_id, []),
            }
        )
    return rows


def _executive_summary(all_checks: list[dict], result_label: str, reason: str) -> dict[str, Any]:
    unique = _unique_checks_by_id(all_checks)
    counts = {"passed": 0, "failed": 0, "manual_review": 0}
    for check in unique:
        status = check.get("status")
        if status in counts:
            counts[status] += 1
    return {
        "overall_status": result_label,
        "passed_checks": counts["passed"],
        "failed_checks": counts["failed"],
        "manual_review_checks": counts["manual_review"],
        "explanation": reason,
    }


def _audit_metadata(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_id": state.get("workflow_id"),
        "generated_at": utcnow_iso(),
        "rule_data_version": state.get("rule_data_version"),
        "vector_index_version": state.get("vector_index_version"),
    }


#: Plain labels for a corrected field - never the raw field_path or check_id.
_CORRECTION_FIELD_LABELS = {
    "quantity": "quantity",
    "unit_price": "unit price",
    "line_total": "line total",
    "net_weight": "net weight",
    "gross_weight": "gross weight",
    "pct_code": "PCT code",
    "product_name": "product name",
    "destination_country": "destination",
    "exporter_name": "exporter",
    "invoice_total": "invoice total",
    "declared_net_weight_total": "declared net weight",
    "declared_gross_weight_total": "declared gross weight",
}


def _correction_field_label(field_path: str) -> str:
    try:
        parsed = parse_field_path(field_path)
    except InvalidFieldPathError:
        return field_path
    document_label = "Invoice" if parsed.document == "invoice" else "Packing list"
    field_label = _CORRECTION_FIELD_LABELS.get(parsed.field, parsed.field.replace("_", " "))
    return f"{document_label} {field_label}"


def _human_review_rows(corrections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One plain-language row per human correction - what was unclear, the
    original value, the reviewed value, and why - never the raw field_path,
    check_id, or internal correction_id."""
    rows: list[dict[str, Any]] = []
    for correction in corrections:
        original = correction.get("original_value")
        corrected = correction.get("corrected_value")
        rows.append(
            {
                "field_label": _correction_field_label(str(correction.get("field_path") or "")),
                "original_value": original,
                "corrected_value": corrected,
                "reason": correction.get("reason"),
                "reviewer_reference": correction.get("reviewer_reference"),
                "was_confirmation": (
                    original is not None and str(original) == str(corrected)
                ),
                "affected_check_count": len(correction.get("affected_check_ids") or []),
            }
        )
    return rows


_REVISION_STATUS_LABELS = {
    "passed": "Passed",
    "failed": "Failed",
    "manual_review": "Manual review",
}


def _audit_revision_rows(revisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per frozen revision - revision 1 is never edited, a
    correction only ever appends a later entry (see AuditRevision)."""
    rows: list[dict[str, Any]] = []
    for revision in revisions:
        deterministic = revision.get("deterministic_result") or {}
        status = str(deterministic.get("overall_status") or "")
        rows.append(
            {
                "revision_number": revision.get("revision_number"),
                "status_label": _REVISION_STATUS_LABELS.get(status, status.title() or "Unknown"),
                "triggered_by": revision.get("triggered_by"),
                "frozen_at": revision.get("frozen_at"),
            }
        )
    return rows


def build_audit_report(state: dict[str, Any]) -> dict[str, Any]:
    """Assemble the full, business-readable audit report dict from state."""
    extraction = state.get("extraction_result") or {}
    invoice = extraction.get("invoice") or {}
    packing = extraction.get("packing_list") or {}

    cross_document, compliance = _collect_checks(extraction)

    problems: dict[str, list[str]] = {
        "missing_documents": [],
        "missing_or_uncertain_fields": [],
        "document_mismatches": [],
        "calculation_errors": [],
        "regulatory_problems": [],
        "evidence_limitations": [],
    }
    checks_passed: list[str] = []
    actions: list[str] = []
    missing_document_keys: set[str] = set()

    def ingest(check: dict, *, is_compliance: bool) -> None:
        bucket, message, action = _categorise(check, is_compliance=is_compliance)
        if bucket is None:
            if check.get("status") == "passed" and message not in checks_passed:
                checks_passed.append(message)
            return
        if bucket == "missing_documents":
            # Legacy and executable rule layers deliberately remain in the raw
            # audit record. Consolidate them only in this business-facing view
            # so one absent supporting document produces one clear problem and
            # one action, regardless of how many rules identified it.
            document_key = str(check.get("required_document") or "").strip().casefold()
            if document_key and document_key in missing_document_keys:
                return
            if document_key:
                missing_document_keys.add(document_key)
        if message not in problems[bucket]:
            problems[bucket].append(message)
        if action and action not in actions:
            actions.append(action)

    for check in cross_document:
        ingest(check, is_compliance=False)
    for check in compliance:
        ingest(check, is_compliance=True)

    result_status = _overall(state, extraction)
    result_label = _RESULT_LABELS.get(result_status, result_status.upper())
    reason = _reason(result_status, problems)

    consensus = state.get("consensus_result") or {}
    needs_review = bool(consensus.get("requires_human_review")) or (
        result_status not in ("passed",)
    )
    workflow_summary: list[str] = []
    if state.get("broker_report"):
        workflow_summary.append("Broker completed extraction.")
    if state.get("deterministic_compliance_result"):
        workflow_summary.append("Deterministic checks completed.")
    if state.get("auditor_report"):
        workflow_summary.append("Auditor reviewed the findings.")
    workflow_summary.append(
        "Human review required." if needs_review else "No human review was required."
    )

    final_report = state.get("final_report") or {}
    all_checks = [*cross_document, *compliance]
    regulatory_evidence_by_check = state.get("regulatory_evidence_by_check") or {}
    regulatory_evidence_status_by_check = state.get("regulatory_evidence_status_by_check") or {}
    document_evidence_by_check = state.get("document_evidence_by_check") or {}
    system_scope_statements_by_check = state.get("system_scope_statements_by_check") or {}
    return {
        "overall_result": result_label,
        "overall_reason": reason,
        "executive_summary": _executive_summary(all_checks, result_label, reason),
        "regulatory_evidence": _regulatory_evidence_rows(
            all_checks, regulatory_evidence_by_check, regulatory_evidence_status_by_check
        ),
        "document_evidence": _document_evidence_rows(all_checks, document_evidence_by_check),
        "system_scope": _system_scope_rows(all_checks, system_scope_statements_by_check),
        "evidence_search_explanation": EVIDENCE_SEARCH_EXPLANATION,
        "human_review_summary": _human_review_rows(state.get("human_correction_history") or []),
        "audit_revision_history": _audit_revision_rows(state.get("audit_revisions") or []),
        "audit_metadata": _audit_metadata(state),
        # The two questions kept apart: were the uploaded documents sound, and
        # what paperwork is still owed before submission.
        "uploaded_document_result": _uploaded_document_result(
            extraction, all_checks, overall_status=result_status
        ),
        "documents_to_obtain": _documents_to_obtain(all_checks),
        "supporting_documents": _supporting_documents(extraction),
        "shipment_summary": _shipment_summary(invoice, packing, state),
        "line_items": _line_items(extraction),
        "checks_passed": checks_passed,
        "problems": problems,
        "required_actions": actions,
        "compliance_evidence": _compliance_evidence(compliance),
        "workflow_summary": workflow_summary,
        # Populated by the generate_explanation node; None until the workflow
        # has reached a finalized verdict (e.g. still paused for human review).
        "explanation": final_report.get("explanation"),
        "explanation_source": final_report.get("explanation_source"),
    }
