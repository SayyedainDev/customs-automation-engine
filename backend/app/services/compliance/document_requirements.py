"""Separate "the uploaded documents are defective" from "more paperwork is due".

Every rule stays exactly as it is and every failure is still reported. This
module only sorts already-computed checks into two questions that a reader
has to keep apart:

* Is anything wrong with the commercial invoice and packing list that were
  uploaded? That is a defect the exporter can correct in the documents.
* Which further customs documents does a rule say must accompany the shipment?
  Those are issued by outside bodies (PSW, chamber of commerce, a bank), cannot
  be derived from the invoice, and their absence says nothing about the quality
  of the two uploaded files.

``overall_status`` keeps its strict meaning - a shipment with an outstanding
Form-E is still not cleared for submission - so nothing downstream is weakened.
The split is additive: it lets the console show the strict verdict beside a
document checklist instead of collapsing both into one red badge.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.schemas.compliance import ComplianceCheckStatus
from app.schemas.multi_line_extraction import OutstandingDocument

#: Documents the review itself consumes. A rule failing because one of these is
#: absent is a genuine input defect, not outstanding paperwork.
INPUT_DOCUMENT_TYPES = frozenset({"commercial_invoice", "packing_list", "invoice"})

_DISPLAY_NAMES = {
    "form_e": "Form-E / PSW export declaration",
    "certificate_of_origin": "Certificate of origin",
    "sbp_deposit_proof": "Proof of SBP deposit",
    "sbp_confirmation": "SBP confirmation",
    "irrevocable_letter_of_credit": "Irrevocable letter of credit",
    "phytosanitary_certificate": "Phytosanitary certificate",
    "import_permit": "Import permit",
    "product_permit": "Product permit",
    "product_licence": "Product licence",
    "product_certificate": "Product certificate",
    "product_approval": "Product approval",
    "goods_declaration": "Goods declaration",
    "bill_of_lading": "Bill of lading",
    "export_contract": "Export contract",
}

_UNRESOLVED = (ComplianceCheckStatus.FAILED, ComplianceCheckStatus.MANUAL_REVIEW)


def display_name(document_type: str) -> str:
    return _DISPLAY_NAMES.get(document_type, document_type.replace("_", " ").capitalize())


def _attr(check: Any, name: str) -> Any:
    if isinstance(check, dict):
        return check.get(name)
    return getattr(check, name, None)


def required_document_type(check: Any) -> str | None:
    """Return the supporting-document type this check is about, if any."""
    value = _attr(check, "required_document")
    if not isinstance(value, str):
        return None
    document_type = value.strip().casefold()
    if not document_type or document_type in INPUT_DOCUMENT_TYPES:
        return None
    return document_type


def is_outstanding_document_check(check: Any) -> bool:
    """Whether this check is unresolved *only* because paperwork is not in hand.

    A required-document rule that passed, or a rule about a document that was
    actually uploaded and then failed its content cross-checks, is not counted:
    the second case is a real defect in a document the exporter does hold.
    """
    status = _attr(check, "status")
    status_value = getattr(status, "value", status)
    if status_value not in {member.value for member in _UNRESOLVED}:
        return False
    return required_document_type(check) is not None


def _source_label(check: Any) -> str | None:
    source = _attr(check, "source_document")
    if not source:
        return None
    parts = [str(source)]
    sro = _attr(check, "sro_number")
    if sro:
        parts.append(str(sro))
    page = _attr(check, "source_page")
    if page:
        parts.append(f"page {page}")
    return " · ".join(parts)


def collect_outstanding_documents(checks: Iterable[Any]) -> list[OutstandingDocument]:
    """One checklist entry per document type, however many rules named it.

    Several rule layers (legacy product rules, executable rules, destination
    rules) can each require the same certificate. The exporter still has to
    obtain exactly one document, so they are merged here and every rule's
    citation is kept against that single entry.
    """
    entries: dict[str, dict[str, Any]] = {}
    for check in checks:
        if not is_outstanding_document_check(check):
            continue
        document_type = required_document_type(check)
        assert document_type is not None  # guarded by is_outstanding_document_check
        status = _attr(check, "status")
        status_value = getattr(status, "value", status)
        entry = entries.setdefault(
            document_type,
            {
                "document_type": document_type,
                "display_name": display_name(document_type),
                "has_firm_requirement": False,
                "reasons": [],
                "sources": [],
            },
        )
        # One rule that firmly requires the document outranks any number of
        # rules that only flag it as conditional.
        if status_value == ComplianceCheckStatus.FAILED.value:
            entry["has_firm_requirement"] = True

        message = _attr(check, "message")
        if message and message not in entry["reasons"]:
            entry["reasons"].append(str(message))
        source = _source_label(check)
        if source and source not in entry["sources"]:
            entry["sources"].append(source)

    return [
        OutstandingDocument(
            document_type=entry["document_type"],
            display_name=entry["display_name"],
            requirement=("required" if entry["has_firm_requirement"] else "conditional"),
            reasons=entry["reasons"],
            sources=entry["sources"],
        )
        for entry in entries.values()
    ]


def uploaded_document_review_status(
    statuses: Iterable[ComplianceCheckStatus],
) -> ComplianceCheckStatus:
    """Aggregate the statuses that the two uploaded documents can be judged on."""
    collected = list(statuses)
    if ComplianceCheckStatus.FAILED in collected:
        return ComplianceCheckStatus.FAILED
    if ComplianceCheckStatus.MANUAL_REVIEW in collected:
        return ComplianceCheckStatus.MANUAL_REVIEW
    return ComplianceCheckStatus.PASSED
