from app.schemas.compliance import (
    ComplianceCheckResponse,
    ComplianceCheckResult,
    ComplianceCheckStatus,
    ShipmentComplianceInput,
)
from app.services.compliance.arithmetic_checks import (
    check_invoice_total,
    check_line_total,
    check_positive_quantity,
    check_positive_unit_price,
)
from app.services.compliance.document_checks import (
    check_general_documents,
    normalize_uploaded_documents,
)
from app.services.compliance.general_checks import (
    check_pct_support,
    check_required_fields,
    check_weights,
)
from app.services.compliance.executable_rule_checks import (
    evaluate_executable_rules,
)
from app.services.compliance.executable_rule_loader import load_executable_rules
from app.services.compliance.product_checks import check_product_requirements
from app.services.compliance.result_builder import overall_status
from app.services.compliance.rule_loader import (
    load_compliance_rules,
)
from app.services.compliance.rule_models import ComplianceRuleSet


class DeterministicComplianceRuleEngine:
    """Run explicit checks in a stable order without an LLM."""

    def __init__(self, rules: ComplianceRuleSet | None = None) -> None:
        self.rules = rules or load_compliance_rules()

    def check(self, shipment: ShipmentComplianceInput) -> ComplianceCheckResponse:
        pct_code = shipment.pct_code
        supported = pct_code in self.rules.supported_codes if pct_code else False
        uploaded_documents = normalize_uploaded_documents(
            shipment.uploaded_document_types
        )

        checks: list[ComplianceCheckResult] = [
            check_required_fields(shipment, pct_code),
            check_pct_support(pct_code, supported, self.rules),
            check_positive_quantity(shipment, pct_code),
            check_positive_unit_price(shipment, pct_code),
            check_line_total(shipment, pct_code),
            check_invoice_total(shipment, pct_code),
            check_weights(shipment, pct_code),
        ]
        checks.extend(
            check_general_documents(
                pct_code,
                uploaded_documents,
                self.rules,
            )
        )

        if supported and pct_code is not None:
            checks.extend(
                check_product_requirements(
                    shipment,
                    self.rules.product_requirements[pct_code],
                    uploaded_documents,
                    self.rules,
                )
            )

        final_status = overall_status(checks)

        executable_checks: list[ComplianceCheckResult] = []
        executable_status: ComplianceCheckStatus | None = None
        if supported and pct_code is not None:
            executable_checks = evaluate_executable_rules(
                shipment,
                uploaded_documents,
                pct_code,
                load_executable_rules(),
                self.rules.rule_data_version,
            )
            if executable_checks:
                executable_status = overall_status(executable_checks)

        return ComplianceCheckResponse(
            pct_code=pct_code,
            supported_product=supported,
            rule_data_version=self.rules.rule_data_version,
            overall_status=final_status,
            is_compliant=final_status == ComplianceCheckStatus.PASSED,
            checks=checks,
            executable_rule_checks=executable_checks,
            executable_overall_status=executable_status,
        )


def get_compliance_rule_engine() -> DeterministicComplianceRuleEngine:
    """Create a lightweight engine using the one cached regulatory rule set."""

    return DeterministicComplianceRuleEngine(load_compliance_rules())
