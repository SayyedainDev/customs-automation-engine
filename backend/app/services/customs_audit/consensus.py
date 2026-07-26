"""Deterministic structured consensus between the Broker and Auditor reports.

Consensus is computed by comparing structured fields, never by asking an LLM
"do you agree?". Consensus controls **workflow routing only** — it never changes
the deterministic legal status. A deterministic ``failed`` or ``manual_review``
can never be converted to ``passed`` by agent agreement.
"""

from __future__ import annotations

from app.services.customs_audit.state import (
    AuditorRecommendation,
    AuditorReport,
    BrokerReport,
    ConsensusResult,
)

_UNCERTAIN_EVIDENCE = {"not_found", "conflicting", "partial"}
_ESCALATING_ACTIONS = {
    AuditorRecommendation.HUMAN_REVIEW.value,
    AuditorRecommendation.EVIDENCE_MISSING.value,
    AuditorRecommendation.RERUN_EXTRACTION.value,
    AuditorRecommendation.TECHNICAL_FAILURE.value,
}


def compute_consensus(
    broker: BrokerReport,
    auditor: AuditorReport,
    deterministic_status: str,
    *,
    human_review_required: bool = True,
) -> ConsensusResult:
    agreed: list[str] = []
    disagreed: list[str] = []
    unresolved: list[str] = []

    # 1. Both agents observed the same authoritative deterministic status.
    if broker.observed_deterministic_status == deterministic_status == auditor.observed_deterministic_status:
        agreed.append("deterministic_status")
    else:
        disagreed.append("deterministic_status")

    # 2. The Auditor confirms the deterministic status stands (never changes it).
    if auditor.compliance_status_confirmed:
        agreed.append("compliance_status_confirmed")
    else:
        disagreed.append("compliance_status_confirmed")

    # 3. Detected discrepancies: did the Auditor find checks the Broker omitted?
    broker_omissions = [
        issue for issue in auditor.newly_detected_issues if issue.startswith("broker omitted")
    ]
    if broker_omissions:
        disagreed.append("detected_discrepancies")
        unresolved.extend(broker_omissions)
    else:
        agreed.append("detected_discrepancies")

    # 4. Independent arithmetic/weight anomalies raised by the Auditor.
    arithmetic_issues = [
        issue
        for issue in auditor.newly_detected_issues
        if "arithmetic disagreement" in issue or "gross weight is below" in issue
    ]
    if arithmetic_issues:
        disagreed.append("arithmetic_and_weights")
        unresolved.extend(arithmetic_issues)
    else:
        agreed.append("arithmetic_and_weights")

    # 5. Supporting documents: the Broker reports what the deterministic
    # verifier concluded, the Auditor re-derives it from the extracted values.
    # Any document the Auditor challenges, or whose reading it could not
    # reproduce, is a disagreement - never silently resolved in either
    # direction, always sent to a person.
    if auditor.challenged_supporting_documents:
        disagreed.append("supporting_documents")
        unresolved.extend(auditor.challenged_supporting_documents)
    elif broker.verified_supporting_documents or auditor.confirmed_supporting_documents:
        agreed.append("supporting_documents")

    broker_verified_but_auditor_disputes = [
        document
        for document in broker.verified_supporting_documents
        if any(
            document.replace("_", " ") in entry
            for entry in (
                *auditor.document_type_disagreements,
                *auditor.field_mismatches,
            )
        )
    ]
    if broker_verified_but_auditor_disputes:
        disagreed.append("supporting_document_verification")
        unresolved.extend(
            f"the Broker reports {document} as verified but the Auditor found a "
            "disagreement on re-check"
            for document in broker_verified_but_auditor_disputes
        )

    unresolved.extend(auditor.low_confidence_documents)

    # 6. Evidence availability.
    if auditor.evidence_support_status in _UNCERTAIN_EVIDENCE:
        unresolved.append(f"evidence_{auditor.evidence_support_status}")
    else:
        agreed.append("evidence_availability")

    # 6. Provenance / critical anomalies.
    unresolved.extend(auditor.missing_provenance)
    unresolved.extend(auditor.critical_anomalies)
    if broker.unsupported_products:
        unresolved.append("unsupported_product_present")

    consensus_reached = not disagreed
    escalate = auditor.recommended_workflow_action.value in _ESCALATING_ACTIONS

    # Critical anomalies always require human review regardless of configuration.
    critical: list[str] = []
    if not consensus_reached:
        critical.append("agent_disagreement")
    if broker.unsupported_products:
        critical.append("unsupported_product")
    if auditor.conflicting_evidence:
        critical.append("conflicting_evidence")
    if escalate:
        critical.append(f"auditor_{auditor.recommended_workflow_action.value}")
    critical.extend(a for a in auditor.critical_anomalies if "conflict" in a or "anomaly" in a)

    # Uncertainty triggers review only when human review is enabled. A definite,
    # agreed failure is already a deterministic terminal answer; routing every
    # failure to a person mislabeled certainty as uncertainty and prevented clean
    # rejection of wrong or absent supporting documents.
    uncertainty = (
        deterministic_status == "manual_review"
        or auditor.evidence_support_status in _UNCERTAIN_EVIDENCE
        or bool(auditor.missing_provenance)
    )
    requires_human_review = bool(critical) or (uncertainty and human_review_required)

    if critical:
        reason = "critical anomaly requires human review: " + ", ".join(dict.fromkeys(critical))
    elif deterministic_status == "manual_review":
        reason = (
            f"deterministic status is {deterministic_status} "
            "(uncertainty requires human confirmation)"
        )
    elif uncertainty:
        reason = "unresolved issues: " + "; ".join(unresolved[:5])
    else:
        reason = "broker and auditor agree; deterministic result is clear"

    return ConsensusResult(
        consensus_reached=consensus_reached,
        agreed_fields=agreed,
        disagreed_fields=disagreed,
        unresolved_issues=unresolved,
        requires_human_review=requires_human_review,
        reason=reason,
        deterministic_status=deterministic_status,
        broker_recommendation="continue",
        auditor_recommendation=auditor.recommended_workflow_action.value,
    )
