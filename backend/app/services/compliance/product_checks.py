from app.schemas.compliance import (
    ComplianceCheckResult,
    ComplianceCheckStatus,
    ShipmentComplianceInput,
)
from app.services.compliance.raw_cotton_checks import (
    RAW_COTTON_PCT,
    check_raw_cotton_rules,
)
from app.services.compliance.result_builder import build_result
from app.services.compliance.rule_models import (
    ComplianceRuleSet,
    DestinationProcedureRule,
    ProductRequirementRule,
    RegulatoryRequirement,
)


UNCERTAIN_VALUE_MARKERS = {
    "",
    "missing",
    "null",
    "unclear",
    "unknown",
    "unverified",
    "not_verified",
    "not verified",
}
UNCERTAIN_STATUS_MARKERS = (
    "missing",
    "unclear",
    "unknown",
    "unverified",
    "not_verified",
    "not verified",
)
VERIFIED_STATUS_MARKERS = (
    "verified",
    "manually_verified",
    "ocr_verified",
)


def first_source_url(product: ProductRequirementRule) -> str | None:
    return product.source_urls[0] if product.source_urls else None


def source_url_containing(
    product: ProductRequirementRule,
    fragment: str,
) -> str | None:
    return next(
        (url for url in product.source_urls if fragment in url),
        None,
    )


def _validation_is_verified(validation_status: str | None) -> bool:
    if not validation_status:
        return False
    normalized = validation_status.casefold()
    if any(marker in normalized for marker in UNCERTAIN_STATUS_MARKERS):
        return False
    return any(marker in normalized for marker in VERIFIED_STATUS_MARKERS)


def _requirement_state(
    rule: RegulatoryRequirement | None,
    requirement_name: str,
) -> tuple[bool | str | None, str, bool, str]:
    """Return value, status, uncertainty, and a safe audit explanation."""

    if rule is None:
        return (
            None,
            "missing",
            True,
            f"Regulatory requirement '{requirement_name}' is missing.",
        )

    value = rule.value
    validation_status = rule.resolved_validation_status or "missing"
    value_is_uncertain = value is None or (
        isinstance(value, str)
        and value.casefold().strip() in UNCERTAIN_VALUE_MARKERS
    )
    validation_is_verified = _validation_is_verified(validation_status)
    uncertain = value_is_uncertain or not validation_is_verified

    if value_is_uncertain:
        reason = (
            f"Regulatory requirement '{requirement_name}' has a null, unknown, "
            f"or unclear value (validation status: {validation_status})."
        )
    elif not validation_is_verified:
        reason = (
            f"Regulatory requirement '{requirement_name}' is not verified "
            f"(validation status: {validation_status})."
        )
    else:
        reason = ""
    return value, validation_status, uncertain, reason


def _product_source_locator(product: ProductRequirementRule) -> str | None:
    if product.source_locator:
        return product.source_locator
    if product.tipp_commodity_code:
        return f"TIPP commodity {product.tipp_commodity_code}"
    return None


def _product_result(
    *,
    rules: ComplianceRuleSet,
    product: ProductRequirementRule,
    check_id: str,
    check_name: str,
    status: ComplianceCheckStatus,
    message: str,
    validation_status: str,
    required_document: str | None = None,
) -> ComplianceCheckResult:
    return build_result(
        check_id=check_id,
        check_name=check_name,
        status=status,
        message=message,
        pct_code=product.pct_code,
        required_document=required_document,
        source_document=rules.product_requirements_source,
        source_url=first_source_url(product),
        source_page=product.source_page,
        source_locator=_product_source_locator(product),
        issuing_authority=product.issuing_authority,
        effective_date=product.effective_date,
        rule_data_version=rules.rule_data_version,
        validation_status=validation_status,
        government_rule=True,
    )


def _uncertain_requirement_result(
    *,
    check_id: str,
    check_name: str,
    requirement_name: str,
    rule: RegulatoryRequirement | None,
    product: ProductRequirementRule,
    rules: ComplianceRuleSet,
    required_document: str | None = None,
) -> ComplianceCheckResult | None:
    _, validation_status, uncertain, reason = _requirement_state(
        rule,
        requirement_name,
    )
    if not uncertain:
        return None
    return _product_result(
        rules=rules,
        product=product,
        check_id=check_id,
        check_name=check_name,
        status=ComplianceCheckStatus.MANUAL_REVIEW,
        message=reason,
        required_document=required_document,
        validation_status=validation_status,
    )


def check_licence_requirement(
    product: ProductRequirementRule,
    uploaded_documents: set[str],
    rules: ComplianceRuleSet,
) -> ComplianceCheckResult:
    uncertain = _uncertain_requirement_result(
        check_id="product_licence_requirement",
        check_name="Product licence requirement",
        requirement_name="licence_required",
        rule=product.licence_required,
        product=product,
        rules=rules,
        required_document="product_licence",
    )
    if uncertain:
        return uncertain

    value, validation_status, _, _ = _requirement_state(
        product.licence_required,
        "licence_required",
    )
    if value is False:
        return _product_result(
            rules=rules,
            product=product,
            check_id="product_licence_requirement",
            check_name="Product licence requirement",
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message="The verified rule data states that no product licence is required.",
            validation_status=validation_status,
        )

    present = "product_licence" in uploaded_documents
    status = ComplianceCheckStatus.PASSED if present else ComplianceCheckStatus.FAILED
    if value == "conditional" and not present:
        status = ComplianceCheckStatus.MANUAL_REVIEW
    return _product_result(
        rules=rules,
        product=product,
        check_id="product_licence_requirement",
        check_name="Product licence requirement",
        status=status,
        message=(
            "Required product licence is present."
            if present
            else "Product licence applicability or presence requires review."
        ),
        required_document="product_licence",
        validation_status=validation_status,
    )


def check_permit_requirement(
    product: ProductRequirementRule,
    uploaded_documents: set[str],
    rules: ComplianceRuleSet,
) -> ComplianceCheckResult:
    required_document = (
        "import_permit" if product.pct_code == RAW_COTTON_PCT else "product_permit"
    )
    uncertain = _uncertain_requirement_result(
        check_id="product_permit_requirement",
        check_name="Product permit requirement",
        requirement_name="permit_required",
        rule=product.permit_required,
        product=product,
        rules=rules,
        required_document=required_document,
    )
    if uncertain:
        return uncertain

    value, validation_status, _, _ = _requirement_state(
        product.permit_required,
        "permit_required",
    )
    if value is False:
        return _product_result(
            rules=rules,
            product=product,
            check_id="product_permit_requirement",
            check_name="Product permit requirement",
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message="The verified rule data states that no product permit is required.",
            validation_status=validation_status,
        )

    present = required_document in uploaded_documents
    if present:
        status = ComplianceCheckStatus.PASSED
        message = "The conditionally required permit is present."
    elif value == "conditional":
        status = ComplianceCheckStatus.MANUAL_REVIEW
        message = (
            "Permit requirement is destination-dependent and cannot be decided "
            "from the current shipment fields alone."
        )
    else:
        status = ComplianceCheckStatus.FAILED
        message = "A required product permit is missing."

    return _product_result(
        rules=rules,
        product=product,
        check_id="product_permit_requirement",
        check_name="Product permit requirement",
        status=status,
        message=message,
        required_document=required_document,
        validation_status=validation_status,
    )


def check_certificate_requirement(
    product: ProductRequirementRule,
    uploaded_documents: set[str],
    rules: ComplianceRuleSet,
) -> ComplianceCheckResult:
    raw_cotton = product.pct_code == RAW_COTTON_PCT
    required_document = (
        "phytosanitary_certificate" if raw_cotton else "product_certificate"
    )
    uncertain = _uncertain_requirement_result(
        check_id=(
            "raw_cotton_phytosanitary_certificate"
            if raw_cotton
            else "product_certificate_requirement"
        ),
        check_name=(
            "Raw cotton phytosanitary certificate"
            if raw_cotton
            else "Product certificate requirement"
        ),
        requirement_name="certificate_required",
        rule=product.certificate_required,
        product=product,
        rules=rules,
        required_document=required_document,
    )
    if uncertain:
        return uncertain

    value, validation_status, _, _ = _requirement_state(
        product.certificate_required,
        "certificate_required",
    )
    if raw_cotton:
        source_url = source_url_containing(product, "id=2&page=80")
        present = required_document in uploaded_documents
        return build_result(
            check_id="raw_cotton_phytosanitary_certificate",
            check_name="Raw cotton phytosanitary certificate",
            status=(
                ComplianceCheckStatus.PASSED
                if present
                else ComplianceCheckStatus.FAILED
            ),
            message=(
                "Phytosanitary certificate is present."
                if present
                else "Raw cotton requires a phytosanitary certificate."
            ),
            pct_code=product.pct_code,
            required_document=required_document,
            source_document="Pakistan Plant Quarantine Rules, 2019",
            source_url=source_url,
            source_locator="TIPP measure id 2, page 80",
            issuing_authority="Department of Plant Protection",
            effective_date=product.effective_date,
            rule_data_version=rules.rule_data_version,
            validation_status=validation_status,
            government_rule=True,
        )

    if value == "conditional":
        return _product_result(
            rules=rules,
            product=product,
            check_id="product_certificate_requirement",
            check_name="Product certificate requirement",
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message=(
                "The verified certificate requirement is destination-based and is "
                "evaluated by the certificate-of-origin check."
            ),
            validation_status=validation_status,
        )

    if value is False:
        return _product_result(
            rules=rules,
            product=product,
            check_id="product_certificate_requirement",
            check_name="Product certificate requirement",
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message="The verified rule data states that no certificate is required.",
            validation_status=validation_status,
        )

    present = "product_certificate" in uploaded_documents
    return _product_result(
        rules=rules,
        product=product,
        check_id="product_certificate_requirement",
        check_name="Product certificate requirement",
        status=(
            ComplianceCheckStatus.PASSED
            if present
            else ComplianceCheckStatus.FAILED
        ),
        message=(
            "Required product certificate is present."
            if present
            else "A required product certificate is missing."
        ),
        required_document="product_certificate",
        validation_status=validation_status,
    )


def check_approval_requirement(
    product: ProductRequirementRule,
    uploaded_documents: set[str],
    rules: ComplianceRuleSet,
) -> ComplianceCheckResult:
    uncertain = _uncertain_requirement_result(
        check_id="product_approval_requirement",
        check_name="Product approval requirement",
        requirement_name="approval_required",
        rule=product.approval_required,
        product=product,
        rules=rules,
        required_document="product_approval",
    )
    if uncertain:
        return uncertain

    value, validation_status, _, _ = _requirement_state(
        product.approval_required,
        "approval_required",
    )
    if value is False:
        return _product_result(
            rules=rules,
            product=product,
            check_id="product_approval_requirement",
            check_name="Product approval requirement",
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message="The verified rule data states that no approval is required.",
            validation_status=validation_status,
        )

    present = "product_approval" in uploaded_documents
    if present:
        status = ComplianceCheckStatus.PASSED
        message = "Required product approval is present."
    elif value == "conditional":
        status = ComplianceCheckStatus.MANUAL_REVIEW
        message = "Product approval is conditional and requires manual review."
    else:
        status = ComplianceCheckStatus.FAILED
        message = "A required product approval is missing."
    return _product_result(
        rules=rules,
        product=product,
        check_id="product_approval_requirement",
        check_name="Product approval requirement",
        status=status,
        message=message,
        required_document="product_approval",
        validation_status=validation_status,
    )


def _coo_procedure(
    rules: ComplianceRuleSet,
    destination_fragment: str,
) -> DestinationProcedureRule | None:
    return next(
        (
            procedure
            for procedure in (
                rules.conditional_certificate_of_origin.destination_procedures
            )
            if destination_fragment.casefold()
            in procedure.destination_or_scheme.casefold()
        ),
        None,
    )


def _common_coo_result(
    *,
    product: ProductRequirementRule,
    rules: ComplianceRuleSet,
    status: ComplianceCheckStatus,
    message: str,
    required_document: str | None = None,
    validation_status: str | None = None,
) -> ComplianceCheckResult:
    certificate_rule = rules.conditional_certificate_of_origin
    return build_result(
        check_id="destination_certificate_of_origin",
        check_name="Destination-based certificate of origin",
        status=status,
        message=message,
        pct_code=product.pct_code,
        required_document=required_document,
        source_document="TIPP consolidated certificate-of-origin measure",
        source_url=certificate_rule.measure_source_url,
        source_locator=(
            certificate_rule.source_locator
            or "TIPP consolidated certificate-of-origin measure"
        ),
        issuing_authority=certificate_rule.responsible_government_agency,
        effective_date=certificate_rule.effective_date,
        rule_data_version=rules.rule_data_version,
        validation_status=(
            validation_status
            or certificate_rule.validation_status
            or "missing_validation_status"
        ),
        government_rule=True,
    )


def check_certificate_of_origin(
    shipment: ShipmentComplianceInput,
    product: ProductRequirementRule,
    uploaded_documents: set[str],
    rules: ComplianceRuleSet,
) -> ComplianceCheckResult:
    certificate_rule = rules.conditional_certificate_of_origin
    applicable_codes = set(certificate_rule.applies_to_selected_codes)
    measure_url = certificate_rule.measure_source_url

    if not measure_url or not applicable_codes:
        return _common_coo_result(
            product=product,
            rules=rules,
            status=ComplianceCheckStatus.MANUAL_REVIEW,
            message=(
                "Certificate-of-origin applicability could not be verified because "
                "the regulatory measure URL or applicable-code list is missing."
            ),
            required_document="certificate_of_origin",
        )

    if product.pct_code not in applicable_codes:
        return _common_coo_result(
            product=product,
            rules=rules,
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message=(
                "The loaded TIPP certificate-of-origin measure does not list "
                "this selected commodity."
            ),
        )

    destination = (shipment.destination_country or "").casefold().strip()
    china_destinations = {
        "china",
        "people's republic of china",
        "peoples republic of china",
        "prc",
    }
    present = "certificate_of_origin" in uploaded_documents

    if destination in china_destinations:
        procedure = _coo_procedure(rules, "China")
        if not procedure or not procedure.source_url:
            return _common_coo_result(
                product=product,
                rules=rules,
                status=ComplianceCheckStatus.MANUAL_REVIEW,
                message="The China certificate-of-origin procedure source is missing.",
                required_document="certificate_of_origin",
            )
        return build_result(
            check_id="destination_certificate_of_origin",
            check_name="Destination-based certificate of origin",
            status=(
                ComplianceCheckStatus.PASSED
                if present
                else ComplianceCheckStatus.FAILED
            ),
            message=(
                "Certificate of origin for China is present."
                if present
                else "Export to China requires a certificate of origin under CPFTA."
            ),
            pct_code=product.pct_code,
            required_document="certificate_of_origin",
            source_document="TIPP Certificate of Origin to China under CPFTA",
            source_url=procedure.source_url,
            source_locator=(
                procedure.source_locator or procedure.destination_or_scheme
            ),
            issuing_authority=(
                procedure.issuing_authority
                or certificate_rule.responsible_government_agency
            ),
            effective_date=procedure.effective_date,
            rule_data_version=rules.rule_data_version,
            validation_status=(
                procedure.validation_status
                or "missing_validation_status"
            ),
            government_rule=True,
        )

    if present:
        return _common_coo_result(
            product=product,
            rules=rules,
            status=ComplianceCheckStatus.PASSED,
            message="A certificate of origin is present for the shipment.",
            required_document="certificate_of_origin",
        )

    return _common_coo_result(
        product=product,
        rules=rules,
        status=ComplianceCheckStatus.MANUAL_REVIEW,
        message=(
            "Certificate-of-origin need depends on the destination and "
            "preferential scheme; the current input does not identify the scheme."
        ),
        required_document="certificate_of_origin",
        validation_status="destination_scheme_not_verified",
    )


def check_product_requirements(
    shipment: ShipmentComplianceInput,
    product: ProductRequirementRule,
    uploaded_documents: set[str],
    rules: ComplianceRuleSet,
) -> list[ComplianceCheckResult]:
    results = [
        check_licence_requirement(product, uploaded_documents, rules),
        check_permit_requirement(product, uploaded_documents, rules),
        check_certificate_requirement(product, uploaded_documents, rules),
        check_approval_requirement(product, uploaded_documents, rules),
        check_certificate_of_origin(shipment, product, uploaded_documents, rules),
    ]
    if shipment.pct_code == RAW_COTTON_PCT:
        results.extend(
            check_raw_cotton_rules(
                shipment,
                uploaded_documents,
                product,
                rules,
            )
        )
    return results
