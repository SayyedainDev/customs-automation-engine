import re

from app.schemas.compliance import ComplianceCheckResult, ComplianceCheckStatus
from app.services.compliance.result_builder import build_result
from app.services.compliance.rule_models import ComplianceRuleSet


DOCUMENT_ALIASES = {
    "invoice": "commercial_invoice",
    "commercial_invoice": "commercial_invoice",
    "packing_list": "packing_list",
    "form_e": "form_e",
    "forme": "form_e",
    "sbp_deposit_proof": "sbp_deposit_proof",
    "proof_of_sbp_deposit": "sbp_deposit_proof",
    "proof_of_1_sbp_deposit": "sbp_deposit_proof",
    "proof_of_1_percent_sbp_deposit": "sbp_deposit_proof",
    "sbp_confirmation": "sbp_confirmation",
    "sbp_confirmation_letter": "sbp_confirmation",
    "irrevocable_letter_of_credit": "irrevocable_letter_of_credit",
    "letter_of_credit": "irrevocable_letter_of_credit",
    "lc": "irrevocable_letter_of_credit",
    "phytosanitary_certificate": "phytosanitary_certificate",
    "phyto_certificate": "phytosanitary_certificate",
    "certificate_of_origin": "certificate_of_origin",
    "coo": "certificate_of_origin",
    "import_permit": "import_permit",
    "product_permit": "product_permit",
    "product_licence": "product_licence",
    "product_license": "product_licence",
    "product_certificate": "product_certificate",
    "product_approval": "product_approval",
}


def canonical_document_type(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return DOCUMENT_ALIASES.get(cleaned, cleaned)


def normalize_uploaded_documents(documents: list[str] | None) -> set[str]:
    return {
        canonical_document_type(document)
        for document in (documents or [])
        if document.strip()
    }


def check_general_documents(
    pct_code: str | None,
    uploaded_documents: set[str],
    rules: ComplianceRuleSet,
) -> list[ComplianceCheckResult]:
    clearance_rule = rules.common_export_clearance
    source_url = clearance_rule.source_url
    source_document = "TIPP Customs Clearance Procedure [Export] [Web-Based]"
    source_is_verified = bool(source_url)
    required_documents = (
        ("commercial_invoice", "Commercial invoice"),
        ("packing_list", "Packing list"),
        ("form_e", "Form-E"),
    )
    results: list[ComplianceCheckResult] = []
    for document_type, display_name in required_documents:
        present = document_type in uploaded_documents
        if not source_is_verified:
            status = ComplianceCheckStatus.MANUAL_REVIEW
            message = (
                f"The source for the {display_name} requirement is missing; "
                "the requirement must be manually verified."
            )
            validation_status = "missing_source_url"
        else:
            status = (
                ComplianceCheckStatus.PASSED
                if present
                else ComplianceCheckStatus.FAILED
            )
            message = (
                f"{display_name} is present."
                if present
                else f"Missing required document: {display_name}."
            )
            validation_status = (
                clearance_rule.validation_status
                or "missing_validation_status"
            )
        results.append(
            build_result(
                check_id=f"required_document_{document_type}",
                check_name=f"{display_name} is present",
                status=status,
                message=message,
                pct_code=pct_code,
                required_document=document_type,
                source_document=source_document,
                source_url=source_url,
                source_locator=(
                    clearance_rule.source_locator
                    or clearance_rule.procedure
                ),
                issuing_authority=(
                    clearance_rule.responsible_government_agency
                ),
                effective_date=clearance_rule.effective_date,
                rule_data_version=rules.rule_data_version,
                validation_status=validation_status,
                government_rule=True,
            )
        )
    return results
