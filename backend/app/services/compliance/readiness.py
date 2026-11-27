"""Deterministic Pre-Clearance Readiness, Port Hold-Risk & Remediation Engine.

Why this module exists (Product Layer):
A binary ``passed`` / ``failed`` compliance status treats every failure identically,
even though exporters face two completely different operational realities:

1. **In-house document defects** (invoice vs. packing-list quantity/weight mismatch,
   line-total math error, or mismatched supporting certificate): filing this on
   Pakistan Single Window (PSW) risks a physical container hold and port demurrage,
   and the exporter can fix their own PDF in minutes.
2. **Pending external paperwork** (Form-E from an authorized dealer bank, Certificate
   of Origin from TDAP/Chamber of Commerce, SBP deposit proof): the uploaded invoice
   and packing list are already sound, and the exporter simply needs to obtain the
   statutory document before filing.

Every score, risk level, and remediation step here is 100% deterministic Python
derived strictly from frozen compliance checks — never an LLM estimate — and cannot
override ``overall_status``.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.schemas.compliance import ComplianceCheckStatus
from app.schemas.multi_line_extraction import (
    ActionOwner,
    BlockerCategory,
    ClearanceReadinessSummary,
    PortHoldRisk,
    ReadinessBand,
    RemediationStep,
)
from app.services.compliance.document_requirements import (
    display_name as document_display_name,
    is_outstanding_document_check,
    required_document_type,
)

# Transparent deterministic penalty rubric (out of 100).
PENALTY_IN_HOUSE_DEFECT = 35
PENALTY_REGULATORY_BLOCKER = 25
PENALTY_REQUIRED_EXTERNAL_DOC = 15
PENALTY_CONDITIONAL_EXTERNAL_DOC = 5
PENALTY_MANUAL_CONFIRMATION = 10

_ARITHMETIC_CHECK_IDS = frozenset(
    {
        "item_line_calculation",
        "invoice_line_calculation",
        "sum_line_totals_match_invoice_total",
        "invoice_total_consistency",
        "positive_quantity",
        "positive_unit_price",
        "weight_consistency",
    }
)

_MISMATCH_CHECK_IDS = frozenset(
    {
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
)

_BANK_OR_PSW_DOCS = frozenset(
    {
        "form_e",
        "form_e_or_psw_export_declaration",
        "sbp_deposit_proof",
        "sbp_confirmation",
        "irrevocable_letter_of_credit",
        "goods_declaration",
    }
)


def _attr(obj: object, name: str, default: object = None) -> object:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _status_str(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _external_owner(doc_type: str) -> ActionOwner:
    normalized = doc_type.strip().casefold()
    if normalized in _BANK_OR_PSW_DOCS:
        return "bank_or_psw"
    return "issuing_authority"


def _in_house_remediation(check_id: str, check_name: str, message: str) -> tuple[BlockerCategory, str, str]:
    if check_id in _ARITHMETIC_CHECK_IDS:
        return (
            "arithmetic_error",
            check_name or "Invoice or weight calculation error",
            (
                f"{message} Recalculate the declared figures on your commercial "
                "invoice or packing list and re-upload before PSW filing."
            ),
        )
    return (
        "document_discrepancy",
        check_name or "Cross-document discrepancy",
        (
            f"{message} Reconcile the commercial invoice and packing list so both "
            "documents state identical shipment figures before PSW filing."
        ),
    )


def compute_clearance_readiness(
    *,
    overall_status: ComplianceCheckStatus | str,
    document_review_status: ComplianceCheckStatus | str,
    checks: Iterable[object],
    outstanding_documents: Iterable[object] = (),
    supporting_documents: Iterable[object] = (),
    fields_requiring_manual_review: Iterable[str] = (),
) -> ClearanceReadinessSummary:
    """Compute a deterministic 0-100 readiness score, hold risk, and fix plan."""
    overall = _status_str(overall_status)
    doc_review = _status_str(document_review_status)

    steps: list[RemediationStep] = []
    seen_keys: set[str] = set()
    in_house_count = 0
    external_count = 0
    manual_count = 0
    penalty = 0

    # 1. Supporting documents that WERE uploaded by the exporter but failed matching.
    for supp in supporting_documents:
        uploaded = bool(_attr(supp, "uploaded", False))
        presence = str(_attr(supp, "presence_status") or "").strip().lower()
        doc_type = str(
            _attr(supp, "canonical_document_type")
            or _attr(supp, "claimed_document_type")
            or "supporting_document"
        )
        doc_type_val = getattr(doc_type, "value", doc_type)
        label = document_display_name(str(doc_type_val))
        if uploaded and presence in {"shipment_mismatched", "invalid"}:
            dedup = f"supp_defect:{doc_type_val}"
            if dedup not in seen_keys:
                seen_keys.add(dedup)
                in_house_count += 1
                penalty += PENALTY_IN_HOUSE_DEFECT
                req_action = _attr(supp, "required_action") or (
                    f"Replace the uploaded {label} so its shipment details match the invoice."
                )
                steps.append(
                    RemediationStep(
                        priority=1,
                        owner="exporter_in_house",
                        category="document_discrepancy",
                        title=f"Uploaded {label} does not match shipment",
                        action=str(req_action),
                        score_impact=PENALTY_IN_HOUSE_DEFECT,
                    )
                )

    # 2. Evaluate all shipment, per-item, and statutory compliance checks.
    for check in checks:
        status = _status_str(_attr(check, "status"))
        if status in {"passed", "not_applicable", ""}:
            continue
        if is_outstanding_document_check(check):
            # Handled cleanly in consolidated outstanding_documents below.
            continue

        check_id = str(_attr(check, "check_id") or "")
        check_name = str(_attr(check, "check_name") or check_id.replace("_", " ").capitalize())
        message = str(_attr(check, "message") or check_name)

        # Skip supporting-doc sub-checks already captured above.
        req_doc = required_document_type(check)
        if req_doc and f"supp_defect:{req_doc}" in seen_keys:
            continue

        dedup_key = f"check:{check_id}:{status}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        if status == ComplianceCheckStatus.FAILED.value:
            if check_id in _ARITHMETIC_CHECK_IDS or check_id in _MISMATCH_CHECK_IDS:
                cat, title, action = _in_house_remediation(check_id, check_name, message)
                in_house_count += 1
                penalty += PENALTY_IN_HOUSE_DEFECT
                steps.append(
                    RemediationStep(
                        priority=1,
                        owner="exporter_in_house",
                        category=cat,
                        title=title,
                        action=action,
                        score_impact=PENALTY_IN_HOUSE_DEFECT,
                    )
                )
            else:
                in_house_count += 1
                penalty += PENALTY_REGULATORY_BLOCKER
                steps.append(
                    RemediationStep(
                        priority=1,
                        owner="exporter_in_house",
                        category="regulatory_blocker",
                        title=check_name,
                        action=f"{message} Resolve this statutory requirement before PSW filing.",
                        score_impact=PENALTY_REGULATORY_BLOCKER,
                    )
                )
        elif status == ComplianceCheckStatus.MANUAL_REVIEW.value:
            manual_count += 1
            penalty += PENALTY_MANUAL_CONFIRMATION
            steps.append(
                RemediationStep(
                    priority=3,
                    owner="compliance_reviewer",
                    category="manual_confirmation",
                    title=check_name,
                    action=f"{message} Have a licensed customs reviewer confirm before submission.",
                    score_impact=PENALTY_MANUAL_CONFIRMATION,
                )
            )

    # 3. Consolidated external documents still to obtain (Form-E, COO, SBP, etc.).
    for doc in outstanding_documents:
        doc_type = str(_attr(doc, "document_type") or "").strip()
        if not doc_type:
            continue
        dedup_key = f"ext_doc:{doc_type.casefold()}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        disp = str(_attr(doc, "display_name") or document_display_name(doc_type))
        requirement = str(_attr(doc, "requirement") or "required").strip().lower()
        owner = _external_owner(doc_type)
        doc_penalty = (
            PENALTY_REQUIRED_EXTERNAL_DOC
            if requirement == "required"
            else PENALTY_CONDITIONAL_EXTERNAL_DOC
        )
        external_count += 1
        penalty += doc_penalty
        authority_label = (
            "your authorized dealer bank / PSW portal"
            if owner == "bank_or_psw"
            else "TDAP / Chamber of Commerce or issuing authority"
        )
        steps.append(
            RemediationStep(
                priority=2,
                owner=owner,
                category="external_document",
                title=(
                    f"Obtain {disp}"
                    if requirement == "required"
                    else f"Confirm whether {disp} applies"
                ),
                action=(
                    f"Request and attach {disp} via {authority_label} before filing the Goods Declaration."
                ),
                score_impact=doc_penalty,
            )
        )

    # 4. Extracted fields flagged for manual confirmation that had no separate check row.
    manual_field_list = [f for f in fields_requiring_manual_review if str(f).strip()]
    if manual_field_list and manual_count == 0:
        manual_count += len(manual_field_list)
        field_penalty = min(30, len(manual_field_list) * PENALTY_MANUAL_CONFIRMATION)
        penalty += field_penalty
        readable_fields = ", ".join(
            str(f).replace("_", " ") for f in manual_field_list[:4]
        )
        steps.append(
            RemediationStep(
                priority=3,
                owner="compliance_reviewer",
                category="manual_confirmation",
                title="Confirm low-confidence extracted fields",
                action=f"Verify {readable_fields} against the source PDF pages.",
                score_impact=field_penalty,
            )
        )

    steps.sort(key=lambda step: (step.priority, -step.score_impact, step.title))

    raw_score = max(0, 100 - penalty)
    if overall == ComplianceCheckStatus.PASSED.value:
        score = 100
        band: ReadinessBand = "ready_for_filing"
        hold_risk: PortHoldRisk = "low"
        headline = (
            "Ready for Pakistan Single Window (PSW) filing — uploaded documents, "
            "arithmetic, and statutory textile checks all passed."
        )
    elif in_house_count > 0:
        score = min(raw_score, 85)
        band = "blocked_document_defect"
        hold_risk = "high"
        defect_word = "discrepancy" if in_house_count == 1 else "discrepancies"
        headline = (
            f"High port-hold risk — fix {in_house_count} in-house document "
            f"{defect_word} before filing on PSW to avoid customs detention."
        )
    elif doc_review == ComplianceCheckStatus.PASSED.value and external_count > 0:
        score = min(max(raw_score, 50), 95)
        band = "awaiting_external_documents"
        hold_risk = "medium"
        doc_word = "document" if external_count == 1 else "documents"
        headline = (
            f"Uploaded invoice & packing list are sound — obtain {external_count} "
            f"pending external {doc_word} before PSW submission."
        )
    else:
        score = min(max(raw_score, 40), 90)
        band = "needs_human_confirmation"
        hold_risk = "medium"
        headline = (
            "Human confirmation required — verify flagged fields or conditional "
            "requirements before PSW submission."
        )

    return ClearanceReadinessSummary(
        readiness_score=score,
        readiness_band=band,
        port_hold_risk=hold_risk,
        headline=headline,
        in_house_fixes_count=in_house_count,
        external_documents_count=external_count,
        manual_confirmations_count=manual_count,
        remediation_steps=steps,
    )
