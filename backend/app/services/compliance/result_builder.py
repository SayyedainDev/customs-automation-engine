from datetime import date

from app.schemas.compliance import (
    ComplianceCheckResult,
    ComplianceCheckStatus,
)


LEGAL_CUTOFF_DATE = date(2026, 7, 22)
UNCERTAIN_VALIDATION_MARKERS = (
    "missing",
    "unclear",
    "unknown",
    "unverified",
    "not_verified",
    "not verified",
)


def _missing_legal_source_fields(
    *,
    source_document: str | None,
    source_url: str | None,
    source_page: int | None,
    source_locator: str | None,
    issuing_authority: str | None,
    effective_date: date | None,
    legal_cutoff_date: date | None,
    rule_data_version: str | None,
    validation_status: str | None,
) -> list[str]:
    missing: list[str] = []
    if not source_document:
        missing.append("source_document")
    if not source_url:
        missing.append("source_url")
    if source_page is None and not source_locator:
        missing.append("source_page_or_source_locator")
    if not issuing_authority:
        missing.append("issuing_authority")
    if effective_date is None:
        missing.append("effective_date")
    if legal_cutoff_date is None:
        missing.append("legal_cutoff_date")
    if not rule_data_version:
        missing.append("rule_data_version")
    if not validation_status:
        missing.append("validation_status")
    return missing


def _validation_is_uncertain(validation_status: str | None) -> bool:
    if not validation_status:
        return True
    normalized = validation_status.casefold()
    return any(marker in normalized for marker in UNCERTAIN_VALIDATION_MARKERS)


def build_result(
    *,
    check_id: str,
    check_name: str,
    status: ComplianceCheckStatus,
    message: str,
    pct_code: str | None,
    required_document: str | None = None,
    source_document: str | None = None,
    sro_number: str | None = None,
    source_url: str | None = None,
    source_page: int | None = None,
    source_locator: str | None = None,
    issuing_authority: str | None = None,
    effective_date: date | None = None,
    legal_cutoff_date: date | None = None,
    rule_data_version: str | None = None,
    validation_status: str | None = None,
    government_rule: bool = False,
) -> ComplianceCheckResult:
    """Build a result and prevent legal passes with incomplete provenance."""

    if government_rule and legal_cutoff_date is None:
        legal_cutoff_date = LEGAL_CUTOFF_DATE

    if government_rule:
        missing_fields = _missing_legal_source_fields(
            source_document=source_document,
            source_url=source_url,
            source_page=source_page,
            source_locator=source_locator,
            issuing_authority=issuing_authority,
            effective_date=effective_date,
            legal_cutoff_date=legal_cutoff_date,
            rule_data_version=rule_data_version,
            validation_status=validation_status,
        )
        uncertain_validation = _validation_is_uncertain(validation_status)
        if (
            status
            in {
                ComplianceCheckStatus.PASSED,
                ComplianceCheckStatus.NOT_APPLICABLE,
            }
            and (missing_fields or uncertain_validation)
        ):
            status = ComplianceCheckStatus.MANUAL_REVIEW
            reasons: list[str] = []
            if missing_fields:
                reasons.append(
                    "missing legal source field(s): " + ", ".join(missing_fields)
                )
            if uncertain_validation:
                reasons.append(
                    "validation status is absent or uncertain"
                )
            message = f"{message} Manual review required because " + " and ".join(
                reasons
            ) + "."

    return ComplianceCheckResult(
        check_id=check_id,
        check_name=check_name,
        status=status,
        message=message,
        pct_code=pct_code,
        required_document=required_document,
        source_document=source_document,
        sro_number=sro_number,
        source_url=source_url,
        source_page=source_page,
        source_locator=source_locator,
        issuing_authority=issuing_authority,
        effective_date=effective_date,
        legal_cutoff_date=legal_cutoff_date,
        rule_data_version=rule_data_version,
        validation_status=validation_status,
    )


def overall_status(
    checks: list[ComplianceCheckResult],
) -> ComplianceCheckStatus:
    statuses = {check.status for check in checks}
    if ComplianceCheckStatus.FAILED in statuses:
        return ComplianceCheckStatus.FAILED
    if ComplianceCheckStatus.MANUAL_REVIEW in statuses:
        return ComplianceCheckStatus.MANUAL_REVIEW
    return ComplianceCheckStatus.PASSED
