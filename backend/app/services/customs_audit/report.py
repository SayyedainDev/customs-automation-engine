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
            f"Obtain {doc} and include it with the shipment documents before "
            "customs submission."
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
    return {
        "overall_result": result_label,
        "overall_reason": reason,
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
