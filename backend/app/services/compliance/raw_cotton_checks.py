from datetime import timedelta

from app.schemas.compliance import (
    ComplianceCheckResult,
    ComplianceCheckStatus,
    ShipmentComplianceInput,
)
from app.services.compliance.result_builder import build_result
from app.services.compliance.rule_models import (
    ComplianceRuleSet,
    ProductRequirementRule,
)


RAW_COTTON_PCT = "52010090"
RAW_COTTON_SRO = "2486(I)/2025"


def _source_url(product: ProductRequirementRule) -> str | None:
    return next(
        (
            url
            for url in product.source_urls
            if "commerce.gov.pk/sros" in url
        ),
        None,
    )


def check_raw_cotton_rules(
    shipment: ShipmentComplianceInput,
    uploaded_documents: set[str],
    product: ProductRequirementRule,
    rules: ComplianceRuleSet,
) -> list[ComplianceCheckResult]:
    pct_code = shipment.pct_code
    source_document = "SRO 2486(I)/2025 — amendment to Export Policy Order, 2022"
    source_url = _source_url(product)
    validation_status = (
        "manually_verified_from_official_sro"
        if source_url
        else "missing_official_sro_source_url"
    )

    document_checks = (
        (
            "raw_cotton_sbp_deposit_proof",
            "Proof of 1% SBP deposit",
            "sbp_deposit_proof",
            "Raw cotton requires proof of the 1% SBP security deposit.",
        ),
        (
            "raw_cotton_sbp_confirmation",
            "SBP confirmation",
            "sbp_confirmation",
            "Raw cotton requires SBP confirmation with the shipping documents.",
        ),
        (
            "raw_cotton_irrevocable_letter_of_credit",
            "Irrevocable letter of credit",
            "irrevocable_letter_of_credit",
            "Raw cotton requires an irrevocable letter of credit.",
        ),
    )
    results: list[ComplianceCheckResult] = []
    for check_id, check_name, document_type, missing_message in document_checks:
        present = document_type in uploaded_documents
        if not source_url:
            status = ComplianceCheckStatus.MANUAL_REVIEW
            message = (
                f"{check_name} could not be legally verified because the official "
                "SRO source URL is missing."
            )
        else:
            status = (
                ComplianceCheckStatus.PASSED
                if present
                else ComplianceCheckStatus.FAILED
            )
            message = f"{check_name} is present." if present else missing_message
        results.append(
            build_result(
                check_id=check_id,
                check_name=check_name,
                status=status,
                message=message,
                pct_code=pct_code,
                required_document=document_type,
                source_document=source_document,
                sro_number=RAW_COTTON_SRO,
                source_url=source_url,
                source_page=1,
                source_locator="page 1",
                issuing_authority="Government of Pakistan, Ministry of Commerce",
                effective_date=product.effective_date,
                rule_data_version=rules.rule_data_version,
                validation_status=validation_status,
                government_rule=True,
            )
        )

    if shipment.letter_of_credit_date is None or shipment.shipment_date is None:
        # Not knowing whether the window was met is not the same as knowing it
        # was breached. This branch returned FAILED, which reads as "this
        # shipment missed the 180-day deadline" when the truth is only that the
        # letter of credit has not been supplied yet, so there is no anchor date
        # to measure from. The executable rule covering the same requirement
        # already reports manual review for exactly this case; returning FAILED
        # here contradicted it in the same result and, being the stronger
        # status, decided the overall verdict.
        #
        # The requirement itself is unchanged: an outstanding letter of credit
        # still blocks submission - it is now reported as paperwork to obtain
        # rather than as a rule the uploaded documents broke.
        # Two different situations share this branch and must not share one
        # message. If the letter of credit was never supplied, the blocker is
        # paperwork to obtain. If it was supplied but its date could not be
        # read, the exporter already holds the document - telling them to go
        # and get it would be wrong - so only the date needs confirming.
        lc_date_unknown = shipment.letter_of_credit_date is None
        lc_held = "irrevocable_letter_of_credit" in uploaded_documents
        lc_not_supplied = lc_date_unknown and not lc_held
        results.append(
            build_result(
                check_id="raw_cotton_shipment_within_180_days",
                check_name="Raw cotton shipment within 180 days",
                status=ComplianceCheckStatus.MANUAL_REVIEW,
                message=(
                    "The 180-day shipment window could not be checked because "
                    + (
                        "the letter of credit has not been provided, so its "
                        "date is unknown."
                        if lc_not_supplied
                        else "the letter-of-credit date could not be read from "
                        "the documents provided."
                        if lc_date_unknown
                        else "the shipment date is unknown."
                    )
                ),
                pct_code=pct_code,
                # When the blocker is the absent letter of credit, name it as
                # the required document. That routes this check into the
                # "still to obtain" checklist instead of the findings panel
                # about the uploaded files - the invoice and packing list did
                # nothing wrong here - and merges it with the letter-of-credit
                # document check above, so one missing document is reported
                # once rather than as two separate problems.
                required_document=(
                    "irrevocable_letter_of_credit" if lc_not_supplied else None
                ),
                source_document=source_document,
                sro_number=RAW_COTTON_SRO,
                source_url=source_url,
                source_page=1,
                source_locator="page 1",
                issuing_authority="Government of Pakistan, Ministry of Commerce",
                effective_date=product.effective_date,
                rule_data_version=rules.rule_data_version,
                validation_status=validation_status,
                government_rule=True,
            )
        )
        return results

    elapsed = shipment.shipment_date - shipment.letter_of_credit_date
    passed = timedelta(0) <= elapsed <= timedelta(days=180)
    results.append(
        build_result(
            check_id="raw_cotton_shipment_within_180_days",
            check_name="Raw cotton shipment within 180 days",
            status=(
                ComplianceCheckStatus.PASSED
                if passed
                else ComplianceCheckStatus.FAILED
            ),
            message=(
                f"Shipment is {elapsed.days} day(s) after the letter-of-credit date."
                if passed
                else (
                    f"Shipment timing is invalid: {elapsed.days} day(s) from "
                    "the letter-of-credit date; the allowed window is 0–180 days."
                )
            ),
            pct_code=pct_code,
            source_document=source_document,
            sro_number=RAW_COTTON_SRO,
            source_url=source_url,
            source_page=1,
            source_locator="page 1",
            issuing_authority="Government of Pakistan, Ministry of Commerce",
            effective_date=product.effective_date,
            rule_data_version=rules.rule_data_version,
            validation_status=validation_status,
            government_rule=True,
        )
    )
    return results
