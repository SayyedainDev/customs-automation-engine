"""Generic deterministic evaluator for executable compliance rules.

One evaluator function handles every rule ``check_type`` for every product, so
adding a new textile rule is a data change, not a new Python branch. The LLM is
never involved. The fail-closed policy is enforced in two layers:

1. A rule whose validation status is not ``verified`` / ``partially_verified``
   can never drive a pass or a fail; it resolves to ``manual_review`` (we will
   not act on an unverified requirement in either direction).
2. ``build_result`` independently downgrades any pass/not-applicable that lacks
   complete legal provenance (e.g. a missing effective date) to
   ``manual_review``.
"""

from datetime import timedelta

from app.schemas.compliance import (
    ComplianceCheckResult,
    ComplianceCheckStatus,
    ShipmentComplianceInput,
)
from app.services.compliance.executable_rule_models import (
    ExecutableCheckType,
    ExecutableRule,
    ExecutableRuleSet,
)
from app.services.compliance.result_builder import build_result


_CHINA_ALIASES = {
    "china",
    "people's republic of china",
    "peoples republic of china",
    "prc",
}
_ABSENT_VALUES = {"none", "false", "no", "null", ""}


def _result(
    rule: ExecutableRule,
    *,
    pct_code: str | None,
    status: ComplianceCheckStatus,
    message: str,
    rule_data_version: str,
    government_rule: bool = True,
) -> ComplianceCheckResult:
    return build_result(
        check_id=rule.rule_id,
        check_name=rule.rule_name,
        status=status,
        message=message,
        pct_code=pct_code,
        required_document=rule.required_document,
        source_document=rule.source_document,
        sro_number=rule.sro_number,
        source_url=rule.source_url,
        source_page=rule.source_page,
        source_locator=rule.source_locator,
        issuing_authority=rule.issuing_authority,
        effective_date=rule.effective_date,
        legal_cutoff_date=rule.legal_cutoff_date,
        rule_data_version=rule_data_version,
        validation_status=rule.validation_status.value,
        government_rule=government_rule,
    )


def _destination_matches(shipment_destination: str | None, target: str | None) -> bool:
    if target is None:
        return False
    destination = (shipment_destination or "").casefold().strip()
    target_norm = target.casefold().strip()
    if target_norm == "china":
        return destination in _CHINA_ALIASES
    return destination == target_norm


def _evaluate_required_document(
    rule: ExecutableRule,
    uploaded_documents: set[str],
    pct_code: str | None,
    rule_data_version: str,
) -> ComplianceCheckResult:
    present = rule.required_document in uploaded_documents
    return _result(
        rule,
        pct_code=pct_code,
        status=ComplianceCheckStatus.PASSED if present else ComplianceCheckStatus.FAILED,
        message=(
            f"{rule.rule_name}: the required document '{rule.required_document}' is present."
            if present
            else f"{rule.rule_name}: the required document '{rule.required_document}' is missing."
        ),
        rule_data_version=rule_data_version,
    )


def _evaluate_conditional_document(
    rule: ExecutableRule,
    uploaded_documents: set[str],
    pct_code: str | None,
    rule_data_version: str,
) -> ComplianceCheckResult:
    present = rule.required_document in uploaded_documents
    if present:
        return _result(
            rule,
            pct_code=pct_code,
            status=ComplianceCheckStatus.PASSED,
            message=f"{rule.rule_name}: the conditionally required document is present.",
            rule_data_version=rule_data_version,
        )
    return _result(
        rule,
        pct_code=pct_code,
        status=ComplianceCheckStatus.MANUAL_REVIEW,
        message=(
            f"{rule.rule_name}: this requirement is conditional and cannot be decided "
            "from the shipment fields alone; manual review is required."
        ),
        rule_data_version=rule_data_version,
    )


def _evaluate_destination_document(
    rule: ExecutableRule,
    shipment: ShipmentComplianceInput,
    uploaded_documents: set[str],
    pct_code: str | None,
    rule_data_version: str,
) -> ComplianceCheckResult:
    if not _destination_matches(shipment.destination_country, rule.destination_country):
        # Structural non-applicability (wrong destination), not a legal pass, so
        # it is not provenance-gated.
        return _result(
            rule,
            pct_code=pct_code,
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message=(
                f"{rule.rule_name}: applies only to destination "
                f"{rule.destination_country}; the shipment destination is "
                f"{shipment.destination_country or 'unspecified'}."
            ),
            rule_data_version=rule_data_version,
            government_rule=False,
        )
    present = rule.required_document in uploaded_documents
    return _result(
        rule,
        pct_code=pct_code,
        status=ComplianceCheckStatus.PASSED if present else ComplianceCheckStatus.FAILED,
        message=(
            f"{rule.rule_name}: the destination-specific document is present."
            if present
            else f"{rule.rule_name}: the destination-specific document is missing."
        ),
        rule_data_version=rule_data_version,
    )


def _evaluate_shipment_deadline(
    rule: ExecutableRule,
    shipment: ShipmentComplianceInput,
    pct_code: str | None,
    rule_data_version: str,
) -> ComplianceCheckResult:
    anchor_value = getattr(shipment, rule.deadline_anchor or "", None)
    if anchor_value is None or shipment.shipment_date is None or rule.deadline_days is None:
        return _result(
            rule,
            pct_code=pct_code,
            status=ComplianceCheckStatus.MANUAL_REVIEW,
            message=(
                f"{rule.rule_name}: the anchor date or shipment date is missing, so the "
                f"{rule.deadline_days}-day window cannot be verified."
            ),
            rule_data_version=rule_data_version,
        )
    elapsed = shipment.shipment_date - anchor_value
    within = timedelta(0) <= elapsed <= timedelta(days=rule.deadline_days)
    return _result(
        rule,
        pct_code=pct_code,
        status=ComplianceCheckStatus.PASSED if within else ComplianceCheckStatus.FAILED,
        message=(
            f"{rule.rule_name}: shipment is {elapsed.days} day(s) after the anchor date, "
            f"within the {rule.deadline_days}-day window."
            if within
            else (
                f"{rule.rule_name}: shipment is {elapsed.days} day(s) after the anchor date, "
                f"outside the allowed 0-{rule.deadline_days} day window."
            )
        ),
        rule_data_version=rule_data_version,
    )


def _evaluate_requirement_status(
    rule: ExecutableRule,
    shipment: ShipmentComplianceInput,
    pct_code: str | None,
    rule_data_version: str,
) -> ComplianceCheckResult:
    # A requirement that declares a destination applies only to that destination.
    # Every rule of this type used to leave destination_country null, meaning
    # "applies always", and that stayed the behaviour for all of them. The field
    # is honoured now because the Export Policy Order Schedule-III negative list
    # is scoped to exports to Afghanistan under the duty drawback scheme, and
    # reporting it against a shipment to China would be simply wrong.
    if rule.destination_country:
        declared = rule.destination_country.casefold().strip()
        actual = (shipment.destination_country or "").casefold().strip()
        if declared != actual:
            return _result(
                rule,
                pct_code=pct_code,
                status=ComplianceCheckStatus.NOT_APPLICABLE,
                message=(
                    f"{rule.rule_name}: applies only to exports to "
                    f"{rule.destination_country}; this shipment is destined for "
                    f"{shipment.destination_country or 'an unspecified country'}."
                ),
                rule_data_version=rule_data_version,
            )
    value = (rule.numeric_value or "").casefold().strip()
    if value in _ABSENT_VALUES:
        return _result(
            rule,
            pct_code=pct_code,
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message=f"{rule.rule_name}: verified that no such requirement applies.",
            rule_data_version=rule_data_version,
        )
    # A positive-but-non-conditional requirement without a document mapping is
    # informational; surface it for review rather than guessing a pass.
    return _result(
        rule,
        pct_code=pct_code,
        status=ComplianceCheckStatus.MANUAL_REVIEW,
        message=f"{rule.rule_name}: requirement value '{rule.numeric_value}' requires manual review.",
        rule_data_version=rule_data_version,
    )


def _evaluate_export_status(
    rule: ExecutableRule,
    pct_code: str | None,
    rule_data_version: str,
) -> ComplianceCheckResult:
    status_value = (rule.export_status or "").casefold().strip()
    if status_value in {"prohibited", "banned"}:
        deterministic = ComplianceCheckStatus.FAILED
    elif status_value in {"restricted"}:
        deterministic = ComplianceCheckStatus.MANUAL_REVIEW
    else:
        deterministic = ComplianceCheckStatus.NOT_APPLICABLE
    # Informational context, not a legal pass/fail gate, so it is not provenance
    # gated as a government decision.
    return _result(
        rule,
        pct_code=pct_code,
        status=deterministic,
        message=f"{rule.rule_name}: export status recorded as '{rule.export_status}'.",
        rule_data_version=rule_data_version,
        government_rule=False,
    )


def evaluate_rule(
    rule: ExecutableRule,
    shipment: ShipmentComplianceInput,
    uploaded_documents: set[str],
    pct_code: str | None,
    rule_data_version: str,
) -> ComplianceCheckResult:
    # Layer 1: never act (pass or fail) on an unverified requirement.
    if not rule.supports_positive_decision and (
        rule.check_type is not ExecutableCheckType.EXPORT_STATUS_INFORMATIONAL
    ):
        return _result(
            rule,
            pct_code=pct_code,
            status=ComplianceCheckStatus.MANUAL_REVIEW,
            message=(
                f"{rule.rule_name}: {rule.validation_note} "
                f"(validation status '{rule.validation_status.value}'; manual review required)."
            ),
            rule_data_version=rule_data_version,
        )

    if rule.check_type is ExecutableCheckType.REQUIRED_DOCUMENT:
        return _evaluate_required_document(rule, uploaded_documents, pct_code, rule_data_version)
    if rule.check_type is ExecutableCheckType.CONDITIONAL_DOCUMENT:
        return _evaluate_conditional_document(rule, uploaded_documents, pct_code, rule_data_version)
    if rule.check_type is ExecutableCheckType.DESTINATION_DOCUMENT:
        return _evaluate_destination_document(
            rule, shipment, uploaded_documents, pct_code, rule_data_version
        )
    if rule.check_type is ExecutableCheckType.SHIPMENT_DEADLINE:
        return _evaluate_shipment_deadline(rule, shipment, pct_code, rule_data_version)
    if rule.check_type is ExecutableCheckType.REQUIREMENT_STATUS:
        return _evaluate_requirement_status(rule, shipment, pct_code, rule_data_version)
    return _evaluate_export_status(rule, pct_code, rule_data_version)


def evaluate_executable_rules(
    shipment: ShipmentComplianceInput,
    uploaded_documents: set[str],
    pct_code: str,
    rule_set: ExecutableRuleSet,
    rule_data_version: str,
) -> list[ComplianceCheckResult]:
    """Evaluate every executable rule that applies to this PCT code."""
    applicable_rules = rule_set.rules_for(pct_code)
    results: list[ComplianceCheckResult] = []

    for rule in applicable_rules:
        if rule.check_type is ExecutableCheckType.CONDITIONAL_DOCUMENT:
            destination_rule = next(
                (
                    candidate
                    for candidate in applicable_rules
                    if (
                        candidate.check_type
                        is ExecutableCheckType.DESTINATION_DOCUMENT
                        and candidate.required_document == rule.required_document
                        and _destination_matches(
                            shipment.destination_country,
                            candidate.destination_country,
                        )
                    )
                ),
                None,
            )
            if destination_rule is not None:
                # Keep the generic rule in the raw audit trail, but mark it as
                # structurally irrelevant when a destination-specific rule
                # covers the same document. For example, the CPFTA China rule
                # is authoritative for a China shipment, so the conditional
                # rule for "other destinations" must not create a second,
                # contradictory manual-review finding.
                results.append(
                    _result(
                        rule,
                        pct_code=pct_code,
                        status=ComplianceCheckStatus.NOT_APPLICABLE,
                        message=(
                            f"{rule.rule_name}: does not apply because "
                            f"'{destination_rule.rule_name}' covers destination "
                            f"{shipment.destination_country or 'unspecified'}."
                        ),
                        rule_data_version=rule_data_version,
                        government_rule=False,
                    )
                )
                continue

        results.append(
            evaluate_rule(
                rule,
                shipment,
                uploaded_documents,
                pct_code,
                rule_data_version,
            )
        )

    return results
