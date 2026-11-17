import asyncio
from copy import deepcopy
from datetime import date
from decimal import Decimal
import shutil

import httpx
import pytest
from pydantic import ValidationError

from fastapi.routing import APIRoute

from app.main import app
from app.schemas.compliance import ShipmentComplianceInput
from app.services.compliance.rule_engine import DeterministicComplianceRuleEngine
from app.services.compliance.rule_loader import (
    load_compliance_rules,
    reload_compliance_rules,
)
from app.services.compliance import rule_loader
from app.services.compliance.rule_models import RegulatoryRequirement


def valid_tshirt_payload() -> dict:
    return {
        "product_name": "Cotton knitted T-shirts",
        "pct_code": "6109.1000",
        "quantity": "100",
        "unit_price": "5.50",
        "invoice_line_total": "550.00",
        "invoice_total": "550.00",
        "net_weight": "75",
        "gross_weight": "80",
        "destination_country": "China",
        "shipment_date": "2026-07-20",
        "letter_of_credit_date": None,
        "uploaded_document_types": [
            "commercial_invoice",
            "packing_list",
            "form_e",
            "certificate_of_origin",
        ],
    }


def valid_raw_cotton_payload() -> dict:
    return {
        "product_name": "Raw cotton, other",
        "pct_code": "52010090",
        "quantity": "1000",
        "unit_price": "2.00",
        "invoice_line_total": "2000.00",
        "invoice_total": "2000.00",
        "net_weight": "1000",
        "gross_weight": "1025",
        "destination_country": "United Arab Emirates",
        "shipment_date": "2026-06-01",
        "letter_of_credit_date": "2026-01-01",
        "uploaded_document_types": [
            "commercial_invoice",
            "packing_list",
            "form_e",
            "sbp_deposit_proof",
            "sbp_confirmation",
            "irrevocable_letter_of_credit",
            "phytosanitary_certificate",
            "import_permit",
        ],
    }


def result_by_id(response_json: dict, check_id: str) -> dict:
    return next(
        check for check in response_json["checks"] if check["check_id"] == check_id
    )


def run_check(payload: dict) -> dict:
    request = ShipmentComplianceInput.model_validate(payload)
    return (
        DeterministicComplianceRuleEngine()
        .check(request)
        .model_dump(mode="json")
    )


def test_complete_cotton_tshirt_passes_with_full_provenance() -> None:
    # With verified effective dates and the EPO-2022 general-permission policy
    # recorded in the rule data, a complete, arithmetically sound T-shirt
    # shipment to China with every required document present is a genuine pass.
    body = run_check(valid_tshirt_payload())
    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/v1/compliance/check"
    )

    assert "POST" in route.methods
    assert body["pct_code"] == "61091000"
    assert body["supported_product"] is True
    assert body["overall_status"] == "passed"
    assert body["is_compliant"] is True
    assert (
        result_by_id(body, "destination_certificate_of_origin")["status"]
        == "passed"
    )
    assert result_by_id(body, "product_licence_requirement")["status"] == "not_applicable"


def test_invoice_calculation_mismatch() -> None:
    payload = valid_tshirt_payload()
    payload["invoice_line_total"] = "560.00"
    payload["invoice_total"] = "560.00"

    body = run_check(payload)
    check = result_by_id(body, "invoice_line_calculation")
    assert check["status"] == "failed"
    assert body["overall_status"] == "failed"
    assert body["is_compliant"] is False


def test_gross_weight_lower_than_net_weight() -> None:
    payload = valid_tshirt_payload()
    payload["net_weight"] = "80"
    payload["gross_weight"] = "79"

    body = run_check(payload)
    check = result_by_id(body, "weight_consistency")
    assert check["status"] == "failed"
    assert body["overall_status"] == "failed"


def test_raw_cotton_missing_sbp_deposit_proof() -> None:
    payload = valid_raw_cotton_payload()
    payload["uploaded_document_types"].remove("sbp_deposit_proof")

    body = run_check(payload)
    check = result_by_id(body, "raw_cotton_sbp_deposit_proof")
    assert check["status"] == "failed"
    assert check["required_document"] == "sbp_deposit_proof"
    assert check["source_page"] == 1
    assert body["overall_status"] == "failed"


def test_raw_cotton_missing_phytosanitary_certificate() -> None:
    payload = valid_raw_cotton_payload()
    payload["uploaded_document_types"].remove("phytosanitary_certificate")

    body = run_check(payload)
    check = result_by_id(body, "raw_cotton_phytosanitary_certificate")
    assert check["status"] == "failed"
    assert check["required_document"] == "phytosanitary_certificate"
    assert body["overall_status"] == "failed"


def test_unsupported_pct_code_returns_manual_review_after_basic_checks() -> None:
    payload = deepcopy(valid_tshirt_payload())
    payload["product_name"] = "Unsupported textile product"
    payload["pct_code"] = "9999.9999"

    body = run_check(payload)
    support_check = result_by_id(body, "mvp_pct_support")
    assert body["pct_code"] == "99999999"
    assert body["supported_product"] is False
    assert body["overall_status"] == "manual_review"
    assert body["is_compliant"] is False
    assert support_check["status"] == "manual_review"
    assert support_check["message"] == "product is outside the current textile MVP scope"
    assert result_by_id(body, "invoice_line_calculation")["status"] == "passed"


def test_missing_packing_list() -> None:
    payload = valid_tshirt_payload()
    payload["uploaded_document_types"].remove("packing_list")

    body = run_check(payload)
    check = result_by_id(body, "required_document_packing_list")
    assert check["status"] == "failed"
    assert check["required_document"] == "packing_list"
    assert body["overall_status"] == "failed"


def test_invalid_pct_code_is_rejected_by_schema() -> None:
    payload = valid_tshirt_payload()
    payload["pct_code"] = "6109.100"

    with pytest.raises(ValidationError, match="PCT code must contain eight digits"):
        ShipmentComplianceInput.model_validate(payload)


@pytest.mark.parametrize("quantity", ["0", "-1"])
def test_zero_and_negative_quantity_fail(quantity: str) -> None:
    payload = valid_tshirt_payload()
    payload["quantity"] = quantity
    payload["invoice_line_total"] = str(Decimal(quantity) * Decimal("5.5"))
    payload["invoice_total"] = payload["invoice_line_total"]

    body = run_check(payload)

    assert result_by_id(body, "positive_quantity")["status"] == "failed"
    assert body["overall_status"] == "failed"


def test_decimal_rounding_uses_one_cent_tolerance() -> None:
    payload = valid_tshirt_payload()
    payload["quantity"] = "3"
    payload["unit_price"] = "0.333"
    payload["invoice_line_total"] = "1.00"
    payload["invoice_total"] = "1.00"

    body = run_check(payload)
    calculation = result_by_id(body, "invoice_line_calculation")

    assert calculation["status"] == "passed"
    assert "1.00" in calculation["message"]
    assert body["overall_status"] == "passed"


def test_missing_form_e() -> None:
    payload = valid_tshirt_payload()
    payload["uploaded_document_types"].remove("form_e")

    body = run_check(payload)
    check = result_by_id(body, "required_document_form_e")

    assert check["status"] == "failed"
    assert check["required_document"] == "form_e"
    assert body["overall_status"] == "failed"


def test_china_shipment_missing_certificate_of_origin() -> None:
    payload = valid_tshirt_payload()
    payload["uploaded_document_types"].remove("certificate_of_origin")

    body = run_check(payload)
    check = result_by_id(body, "destination_certificate_of_origin")

    assert check["status"] == "failed"
    assert check["required_document"] == "certificate_of_origin"
    assert body["overall_status"] == "failed"


def test_raw_cotton_shipment_exactly_at_180_day_limit() -> None:
    payload = valid_raw_cotton_payload()
    payload["letter_of_credit_date"] = "2026-01-01"
    payload["shipment_date"] = "2026-06-30"

    body = run_check(payload)
    check = result_by_id(body, "raw_cotton_shipment_within_180_days")

    # Exactly 180 days is inside the allowed 0–180 window, so with complete
    # provenance the check is a genuine pass (previously it only read as
    # manual_review because the effective date was missing).
    assert check["status"] == "passed"
    assert "180 day(s)" in check["message"]
    assert body["overall_status"] == "passed"


def test_raw_cotton_shipment_after_180_day_limit() -> None:
    payload = valid_raw_cotton_payload()
    payload["letter_of_credit_date"] = "2026-01-01"
    payload["shipment_date"] = "2026-07-01"

    body = run_check(payload)
    check = result_by_id(body, "raw_cotton_shipment_within_180_days")

    assert check["status"] == "failed"
    assert "181 day(s)" in check["message"]
    assert body["overall_status"] == "failed"


def test_raw_cotton_missing_letter_of_credit_date() -> None:
    """An unsupplied letter of credit is unverifiable, not a proven breach.

    This asserted "failed", which states that the shipment missed the 180-day
    deadline. CACE cannot know that: without the letter of credit there is no
    anchor date to measure from. The executable rule for the same requirement
    already reported manual review for this case, so the result contradicted
    itself and the stronger FAILED decided the overall verdict.

    The requirement is not weakened - the shipment is still not compliant and
    still cannot be submitted; the outstanding letter of credit is reported as
    paperwork to obtain rather than as a rule the uploaded documents broke.
    The genuine-breach case above (181 days elapsed) still fails.
    """
    payload = valid_raw_cotton_payload()
    payload["letter_of_credit_date"] = None

    body = run_check(payload)
    check = result_by_id(body, "raw_cotton_shipment_within_180_days")

    assert check["status"] == "manual_review"
    assert "could not be checked" in check["message"]
    # This payload uploads the letter of credit and only omits its date, so the
    # exporter holds the document - the message must ask them to confirm the
    # date, not to obtain paperwork they already have.
    assert "date could not be read" in check["message"]
    assert check["required_document"] is None
    # Still blocked for submission, just not reported as a breach.
    assert body["overall_status"] == "manual_review"
    assert body["is_compliant"] is False


def test_unknown_destination_requires_manual_review_without_origin_certificate() -> None:
    payload = valid_tshirt_payload()
    payload["destination_country"] = "Unknown Destination"
    payload["uploaded_document_types"].remove("certificate_of_origin")

    body = run_check(payload)
    check = result_by_id(body, "destination_certificate_of_origin")

    assert check["status"] == "manual_review"
    assert body["overall_status"] == "manual_review"
    assert body["is_compliant"] is False


def test_real_licence_requirement_is_explicit_verified_not_required() -> None:
    # The rule data now records the T-shirt licence requirement as an explicit,
    # verified `false` under the Export Policy Order 2022 general-permission
    # policy, so it resolves to not_applicable rather than forcing manual review.
    # The safety invariant that a *null* or *uncertain* value can never produce a
    # pass is still covered by the fixture-based tests below.
    rules = load_compliance_rules()
    requirement = rules.product_requirements["61091000"].licence_required
    assert requirement is not None
    assert requirement.value is False
    assert "verified" in (requirement.resolved_validation_status or "")

    request = ShipmentComplianceInput.model_validate(valid_tshirt_payload())
    body = DeterministicComplianceRuleEngine(rules).check(request).model_dump(mode="json")
    licence_check = result_by_id(body, "product_licence_requirement")

    assert licence_check["status"] == "not_applicable"
    assert body["overall_status"] == "passed"
    assert body["is_compliant"] is True


@pytest.mark.parametrize(
    ("rule_value", "validation_status"),
    [
        ("unknown", "unknown"),
        ("unverified", "unverified"),
        ("unclear", "unclear"),
        (False, "unverified_source"),
    ],
)
def test_uncertain_requirement_cannot_produce_compliance_pass(
    rule_value: str | bool,
    validation_status: str,
) -> None:
    rules = load_compliance_rules()
    product_requirements = dict(rules.product_requirements)
    product = product_requirements["61091000"]
    product_requirements["61091000"] = product.model_copy(
        update={
            "licence_required": RegulatoryRequirement(
                value=rule_value,
                verification_status=validation_status,
            )
        }
    )
    uncertain_rules = rules.model_copy(
        update={"product_requirements": product_requirements},
    )

    request = ShipmentComplianceInput.model_validate(valid_tshirt_payload())
    body = (
        DeterministicComplianceRuleEngine(uncertain_rules)
        .check(request)
        .model_dump(mode="json")
    )
    licence_check = result_by_id(body, "product_licence_requirement")

    assert licence_check["status"] == "manual_review"
    assert "licence_required" in licence_check["message"]
    assert body["is_compliant"] is False


def test_missing_requirement_cannot_produce_compliance_pass() -> None:
    rules = load_compliance_rules()
    product_requirements = dict(rules.product_requirements)
    product = product_requirements["61091000"]
    product_requirements["61091000"] = product.model_copy(
        update={"licence_required": None}
    )
    incomplete_rules = rules.model_copy(
        update={"product_requirements": product_requirements},
    )

    request = ShipmentComplianceInput.model_validate(valid_tshirt_payload())
    body = (
        DeterministicComplianceRuleEngine(incomplete_rules)
        .check(request)
        .model_dump(mode="json")
    )
    licence_check = result_by_id(body, "product_licence_requirement")

    assert licence_check["status"] == "manual_review"
    assert "missing" in licence_check["message"]
    assert body["is_compliant"] is False


def test_multiple_and_duplicate_document_names_are_normalized() -> None:
    payload = valid_tshirt_payload()
    payload["uploaded_document_types"] = [
        "Commercial Invoice",
        "invoice",
        "PACKING-LIST",
        "packing_list",
        "Form E",
        "FORM-E",
        "Certificate of Origin",
        "COO",
    ]

    body = run_check(payload)

    assert (
        result_by_id(body, "required_document_commercial_invoice")["status"]
        == "passed"
    )
    assert (
        result_by_id(body, "required_document_packing_list")["status"]
        == "passed"
    )
    assert (
        result_by_id(body, "required_document_form_e")["status"]
        == "passed"
    )
    assert (
        result_by_id(body, "destination_certificate_of_origin")["status"]
        == "passed"
    )
    assert body["overall_status"] == "passed"


def test_fully_verified_non_required_rules_can_produce_pass() -> None:
    rules = load_compliance_rules()
    effective_date = date(2026, 1, 1)
    product_requirements = dict(rules.product_requirements)
    tshirt = product_requirements["61091000"]
    product_requirements["61091000"] = tshirt.model_copy(
        update={
            "licence_required": RegulatoryRequirement(
                value=False,
                verification_status="verified_not_required",
            ),
            "permit_required": RegulatoryRequirement(
                value=False,
                verification_status="verified_not_required",
            ),
            "certificate_required": RegulatoryRequirement(
                value="conditional",
                verification_status="verified_destination_conditional",
            ),
            "approval_required": RegulatoryRequirement(
                value=False,
                verification_status="verified_not_required",
            ),
            "issuing_authority": "Pakistan Single Window",
            "effective_date": effective_date,
            "source_locator": "TIPP commodity 610910000000",
        }
    )

    product_metadata = dict(rules.product_metadata)
    metadata = product_metadata["61091000"]
    product_metadata["61091000"] = metadata.model_copy(
        update={
            "source_url": "https://download1.fbr.gov.pk/tariff.pdf",
            "issuing_authority": "Federal Board of Revenue",
            "effective_date": effective_date,
        }
    )

    clearance = rules.common_export_clearance.model_copy(
        update={
            "effective_date": effective_date,
            "validation_status": "verified_from_tipp_procedure",
        }
    )
    certificate_rule = rules.conditional_certificate_of_origin
    procedures = list(certificate_rule.destination_procedures)
    china_index = next(
        index
        for index, procedure in enumerate(procedures)
        if "china" in procedure.destination_or_scheme.casefold()
    )
    procedures[china_index] = procedures[china_index].model_copy(
        update={
            "issuing_authority": "Trade Development Authority of Pakistan",
            "effective_date": effective_date,
            "validation_status": "verified_from_tipp_china_procedure",
        }
    )
    certificate_rule = certificate_rule.model_copy(
        update={
            "destination_procedures": procedures,
            "source_locator": "TIPP measure 188",
            "effective_date": effective_date,
            "validation_status": "verified_from_tipp_measure",
        }
    )
    verified_rules = rules.model_copy(
        update={
            "product_requirements": product_requirements,
            "product_metadata": product_metadata,
            "common_export_clearance": clearance,
            "conditional_certificate_of_origin": certificate_rule,
        },
    )

    request = ShipmentComplianceInput.model_validate(valid_tshirt_payload())
    body = (
        DeterministicComplianceRuleEngine(verified_rules)
        .check(request)
        .model_dump(mode="json")
    )

    assert body["overall_status"] == "passed"
    assert body["is_compliant"] is True


def test_government_checks_include_legal_traceability() -> None:
    body = run_check(valid_raw_cotton_payload())
    non_government_checks = {
        "required_fields",
        "positive_quantity",
        "positive_unit_price",
        "invoice_line_calculation",
        "invoice_total_consistency",
        "weight_consistency",
    }

    for check in body["checks"]:
        if check["check_id"] in non_government_checks:
            continue
        assert check["source_document"]
        assert "sro_number" in check
        assert "source_url" in check
        assert "source_page" in check
        assert "source_locator" in check
        assert "issuing_authority" in check
        assert "effective_date" in check
        assert check["legal_cutoff_date"] == "2026-07-22"
        assert check["rule_data_version"] == body["rule_data_version"]
        assert check["validation_status"]
        if check["status"] == "passed":
            assert check["source_url"]
            assert check["source_page"] or check["source_locator"]
            assert check["issuing_authority"]
            assert check["effective_date"]

    raw_cotton_check = result_by_id(body, "raw_cotton_sbp_deposit_proof")
    assert raw_cotton_check["sro_number"] == "2486(I)/2025"
    assert raw_cotton_check["source_page"] == 1


def test_rule_data_has_one_explicit_reload_path() -> None:
    original = load_compliance_rules()
    reloaded = reload_compliance_rules()

    assert reloaded is not original
    assert load_compliance_rules() is reloaded


def test_rule_reload_updates_combined_checksum(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary_pct = tmp_path / "textile_mvp_pct_codes.json"
    temporary_requirements = tmp_path / "textile_product_requirements.json"
    shutil.copyfile(rule_loader.PCT_CONFIG_PATH, temporary_pct)
    shutil.copyfile(rule_loader.PRODUCT_REQUIREMENTS_PATH, temporary_requirements)

    with monkeypatch.context() as patch:
        patch.setattr(rule_loader, "PROJECT_ROOT", tmp_path)
        patch.setattr(rule_loader, "PCT_CONFIG_PATH", temporary_pct)
        patch.setattr(
            rule_loader,
            "PRODUCT_REQUIREMENTS_PATH",
            temporary_requirements,
        )
        first = rule_loader.reload_compliance_rules()
        temporary_requirements.write_text(
            temporary_requirements.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )

        assert rule_loader.load_compliance_rules().rule_data_version == (
            first.rule_data_version
        )
        refreshed = rule_loader.reload_compliance_rules()
        assert refreshed.rule_data_version != first.rule_data_version

    rule_loader.reload_compliance_rules()


def test_http_compliance_endpoint_returns_rule_version() -> None:
    async def post_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post(
                "/api/v1/compliance/check",
                json=valid_tshirt_payload(),
            )

    response = asyncio.run(post_request())
    body = response.json()

    assert response.status_code == 200
    assert body["pct_code"] == "61091000"
    assert body["overall_status"] == "passed"
    assert body["rule_data_version"].startswith("sha256:")
