from app.schemas.compliance import (
    ComplianceCheckResult,
    ComplianceCheckStatus,
    ShipmentComplianceInput,
)
from app.services.compliance.result_builder import build_result
from app.services.compliance.rule_models import ComplianceRuleSet


BASE_REQUIRED_FIELDS = (
    "product_name",
    "pct_code",
    "quantity",
    "unit_price",
    "invoice_line_total",
    "invoice_total",
    "net_weight",
    "gross_weight",
    "destination_country",
    "shipment_date",
    "uploaded_document_types",
)


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def check_required_fields(
    shipment: ShipmentComplianceInput,
    pct_code: str | None,
) -> ComplianceCheckResult:
    missing_fields = [
        field
        for field in BASE_REQUIRED_FIELDS
        if _is_missing(getattr(shipment, field))
    ]
    if missing_fields:
        # A field that could not be extracted is uncertainty, not a proven
        # violation, so it resolves to manual_review rather than a hard failure.
        # A positively-required *document* being absent is handled separately by
        # the document checks and still fails.
        return build_result(
            check_id="required_fields",
            check_name="Required shipment fields",
            status=ComplianceCheckStatus.MANUAL_REVIEW,
            message=(
                "Could not confirm these shipment field(s) from the documents: "
                + ", ".join(missing_fields)
                + ". They need manual confirmation."
            ),
            pct_code=pct_code,
            source_document="Phase 1 shipment input schema",
            validation_status="manual_review_missing_extraction",
        )
    return build_result(
        check_id="required_fields",
        check_name="Required shipment fields",
        status=ComplianceCheckStatus.PASSED,
        message="All required Phase 1 shipment fields are present.",
        pct_code=pct_code,
        source_document="Phase 1 shipment input schema",
        validation_status="validated_from_request_schema",
    )


def check_pct_support(
    pct_code: str | None,
    supported: bool,
    rules: ComplianceRuleSet,
) -> ComplianceCheckResult:
    metadata = rules.product_metadata.get(pct_code or "")
    if supported:
        if metadata is None:
            return build_result(
                check_id="mvp_pct_support",
                check_name="PCT code supported by textile MVP",
                status=ComplianceCheckStatus.MANUAL_REVIEW,
                message=(
                    f"PCT code {pct_code} is configured, but its typed tariff "
                    "metadata is missing."
                ),
                pct_code=pct_code,
                source_document=rules.pct_config_source,
                source_locator=rules.pct_config_source,
                rule_data_version=rules.rule_data_version,
                validation_status="missing_tariff_metadata",
                government_rule=True,
            )
        validation_status = metadata.validation_status
        if not validation_status:
            return build_result(
                check_id="mvp_pct_support",
                check_name="PCT code supported by textile MVP",
                status=ComplianceCheckStatus.MANUAL_REVIEW,
                message=(
                    f"PCT code {pct_code} is configured, but its tariff validation "
                    "status is missing and requires manual review."
                ),
                pct_code=pct_code,
                source_document=metadata.source_document or rules.pct_config_source,
                source_url=metadata.source_url,
                source_page=metadata.tariff_source_page,
                source_locator=(
                    f"tariff page {metadata.tariff_source_page}"
                    if metadata.tariff_source_page
                    else None
                ),
                issuing_authority=metadata.issuing_authority,
                effective_date=metadata.effective_date,
                rule_data_version=rules.rule_data_version,
                validation_status="missing",
                government_rule=True,
            )
        return build_result(
            check_id="mvp_pct_support",
            check_name="PCT code supported by textile MVP",
            status=ComplianceCheckStatus.PASSED,
            message=f"PCT code {pct_code} is supported by the textile MVP.",
            pct_code=pct_code,
            source_document=metadata.source_document or rules.pct_config_source,
            source_url=metadata.source_url,
            source_page=metadata.tariff_source_page,
            source_locator=(
                f"tariff page {metadata.tariff_source_page}"
                if metadata.tariff_source_page
                else None
            ),
            issuing_authority=metadata.issuing_authority,
            effective_date=metadata.effective_date,
            rule_data_version=rules.rule_data_version,
            validation_status=validation_status,
            government_rule=True,
        )
    return build_result(
        check_id="mvp_pct_support",
        check_name="PCT code supported by textile MVP",
        status=ComplianceCheckStatus.MANUAL_REVIEW,
        message="product is outside the current textile MVP scope",
        pct_code=pct_code,
        source_document=rules.pct_config_source,
        source_locator=rules.pct_config_source,
        rule_data_version=rules.rule_data_version,
        validation_status="outside_validated_mvp_scope",
        government_rule=True,
    )


def check_weights(
    shipment: ShipmentComplianceInput,
    pct_code: str | None,
) -> ComplianceCheckResult:
    if shipment.net_weight is None or shipment.gross_weight is None:
        return build_result(
            check_id="weight_consistency",
            check_name="Gross weight is at least net weight",
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message="Weight check could not run because a weight is missing.",
            pct_code=pct_code,
            source_document="Shipment weight validation",
            validation_status="input_missing",
        )

    passed = shipment.gross_weight >= shipment.net_weight
    return build_result(
        check_id="weight_consistency",
        check_name="Gross weight is at least net weight",
        status=(
            ComplianceCheckStatus.PASSED
            if passed
            else ComplianceCheckStatus.FAILED
        ),
        message=(
            "Gross weight is greater than or equal to net weight."
            if passed
            else "Gross weight cannot be lower than net weight."
        ),
        pct_code=pct_code,
        source_document="Shipment weight validation",
        validation_status="calculated_from_shipment_input",
    )
