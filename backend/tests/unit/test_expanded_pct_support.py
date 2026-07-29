"""Deterministic compliance coverage for the expanded textile PCT catalog.

Every supported code is exercised through the same checks rather than a
hand-written block per code, because the rules are configuration: a per-code
test that duplicated the config would only assert that the config equals
itself. What is worth asserting is the behaviour the config is supposed to
produce, for every code, including the ones added later.

No language model is called anywhere here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.schemas.compliance import ComplianceCheckStatus, ShipmentComplianceInput
from app.services.assistant.foundation import normalize_pct_code, validate_pct_scope
from app.services.assistant.guidance import generate_pre_submission_guidance
from app.services.assistant.regulatory_chat import answer_regulatory_question
from app.services.compliance.document_requirements import collect_outstanding_documents
from app.services.compliance.pct_catalog import (
    codes_by_category,
    load_pct_catalog,
    non_raw_material_codes,
    raw_material_codes,
    supported_pct_codes,
    supported_pct_products,
)
from app.services.compliance.rule_engine import get_compliance_rule_engine
from app.services.regulatory.embeddings import (
    FakeEmbeddingProvider,
    reset_embedding_provider,
    set_embedding_provider,
)
from app.services.regulatory.reranker import FakeReranker, reset_reranker, set_reranker
from tests.unit.test_regulatory_retrieval import build_corpus

#: Codes the catalog gained beyond the original prototype five.
ORIGINAL_FIVE = {"52010090", "52051100", "52094200", "61091000", "63023110"}
NEW_CODES = [c for c in supported_pct_codes() if c not in ORIGINAL_FIVE]
ALL_CODES = list(supported_pct_codes())

#: Deliberately outside the deterministic catalog: a real textile tariff code
#: (men's other garments, of cotton) that CACE has no rules for.
UNSUPPORTED = "62113200"


@pytest.fixture(autouse=True)
def _offline_models():
    set_embedding_provider(FakeEmbeddingProvider(dimension=16))
    set_reranker(FakeReranker())
    yield
    reset_embedding_provider()
    reset_reranker()


def _shipment(code: str, destination: str, documents: list[str]) -> ShipmentComplianceInput:
    return ShipmentComplianceInput(
        product_name=supported_pct_products()[code],
        pct_code=code,
        quantity=Decimal("100"),
        unit_price=Decimal("5.50"),
        invoice_line_total=Decimal("550.00"),
        invoice_total=Decimal("550.00"),
        net_weight=Decimal("75"),
        gross_weight=Decimal("80"),
        destination_country=destination,
        uploaded_document_types=documents,
    )


def test_catalog_expanded_and_balanced() -> None:
    """The catalog covers every textile category the expansion targeted."""
    assert len(ALL_CODES) == 17
    assert len(NEW_CODES) == 12
    categories = codes_by_category()
    for required in (
        "raw_material",
        "yarn",
        "woven_fabric",
        "knitted_garment",
        "woven_garment",
        "made_up",
    ):
        assert categories.get(required), f"no codes in category {required}"
    # Raw cotton remains the only raw material, so its export-policy conditions
    # are not accidentally inherited by manufactured goods.
    assert raw_material_codes() == ("52010090",)
    assert len(non_raw_material_codes()) == 16


@pytest.mark.parametrize("code", ALL_CODES)
def test_every_code_has_tariff_provenance(code: str) -> None:
    """1. Each supported code names the tariff page it was verified from."""
    product = next(p for p in load_pct_catalog() if p.pct_code == code)
    assert product.simple_product_name
    assert product.official_tariff_description
    assert product.tariff_source_page and product.tariff_source_page > 0
    assert "pakistan_customs_tariff" in (product.source_document or "")


@pytest.mark.parametrize("code", ALL_CODES)
def test_valid_guidance_for_every_code(isolated_database: Engine, code: str) -> None:
    """2. Guidance succeeds and lists documents to prepare for every code."""
    with Session(isolated_database) as db:
        build_corpus(db)
        response = generate_pre_submission_guidance(
            db, product=supported_pct_products()[code], pct_code=code, destination="China"
        )
    assert response.supported_scope is True
    assert response.pct_code == code
    types = {d.document_type for d in response.documents}
    assert {"commercial_invoice", "packing_list", "form_e"} <= types
    for document in response.documents:
        assert document.preparation_status == "to_prepare"
        assert "Missing required document" not in document.reason


@pytest.mark.parametrize("code", ALL_CODES)
def test_pct_normalization_for_every_code(code: str) -> None:
    """3. Dotted, spaced and hyphenated forms all resolve to the same code."""
    dotted = f"{code[:4]}.{code[4:]}"
    for written in (dotted, f"{code[:4]} {code[4:]}", f"{code[:4]}-{code[4:]}", code):
        assert normalize_pct_code(written) == code
        supported, _, resolved, _ = validate_pct_scope(written, None)
        assert supported is True
        assert resolved == code


def test_product_and_pct_conflict_is_reported() -> None:
    """4. A product description that contradicts the code is refused."""
    supported, message, _, _ = validate_pct_scope("61091000", "Cotton yarn")
    assert supported is False
    assert "inconsistent" in message.casefold()


@pytest.mark.parametrize("code", ALL_CODES)
def test_fully_documented_shipment_has_no_outstanding_paperwork(code: str) -> None:
    """5. A shipment holding every document has nothing left to obtain."""
    engine = get_compliance_rule_engine()
    documents = [
        "commercial_invoice", "packing_list", "form_e", "certificate_of_origin",
        "sbp_deposit_proof", "sbp_confirmation", "irrevocable_letter_of_credit",
        "phytosanitary_certificate", "import_permit", "product_licence",
        "product_permit", "product_certificate", "product_approval",
    ]
    response = engine.check(_shipment(code, "China", documents))
    outstanding = collect_outstanding_documents(
        response.checks + response.executable_rule_checks
    )
    assert outstanding == []


@pytest.mark.parametrize("code", ALL_CODES)
def test_missing_documents_are_reported_for_every_code(code: str) -> None:
    """6. With nothing uploaded, Form-E is always outstanding."""
    engine = get_compliance_rule_engine()
    response = engine.check(_shipment(code, "China", []))
    outstanding = {
        d.document_type
        for d in collect_outstanding_documents(
            response.checks + response.executable_rule_checks
        )
    }
    assert "form_e" in outstanding
    assert response.overall_status is not ComplianceCheckStatus.PASSED


@pytest.mark.parametrize("code", sorted(set(non_raw_material_codes())))
def test_china_destination_requires_certificate_of_origin(code: str) -> None:
    """7. The China destination rule reaches every non-raw-material code."""
    engine = get_compliance_rule_engine()
    response = engine.check(_shipment(code, "China", []))
    outstanding = {
        d.document_type: d
        for d in collect_outstanding_documents(
            response.checks + response.executable_rule_checks
        )
    }
    assert "certificate_of_origin" in outstanding
    assert outstanding["certificate_of_origin"].requirement == "required"


@pytest.mark.parametrize("code", sorted(set(non_raw_material_codes())))
def test_other_destination_makes_certificate_conditional(code: str) -> None:
    """8. Outside China the certificate is conditional, never asserted."""
    engine = get_compliance_rule_engine()
    response = engine.check(_shipment(code, "Germany", []))
    outstanding = {
        d.document_type: d
        for d in collect_outstanding_documents(
            response.checks + response.executable_rule_checks
        )
    }
    assert outstanding["certificate_of_origin"].requirement == "conditional"


@pytest.mark.parametrize("code", ALL_CODES)
def test_raw_cotton_rules_do_not_leak_to_manufactured_goods(code: str) -> None:
    """9. SRO 2486 conditions stay on raw cotton alone."""
    engine = get_compliance_rule_engine()
    response = engine.check(_shipment(code, "China", []))
    types = {
        d.document_type
        for d in collect_outstanding_documents(
            response.checks + response.executable_rule_checks
        )
    }
    cotton_only = {
        "sbp_deposit_proof", "sbp_confirmation",
        "irrevocable_letter_of_credit", "phytosanitary_certificate",
    }
    if code in raw_material_codes():
        assert cotton_only <= types
    else:
        assert not (cotton_only & types)


def test_yarn_afghanistan_duty_drawback_is_destination_scoped() -> None:
    """The Export Policy Order Schedule-III negative list is scoped correctly.

    Schedule-III (page 18) lists "Yarn all types" for exports to Afghanistan
    under the duty drawback scheme. It must not touch a shipment to anywhere
    else, and it must never be a failure: CACE cannot tell from the shipment
    fields whether duty drawback is being claimed.
    """
    engine = get_compliance_rule_engine()
    yarn = codes_by_category()["yarn"]
    for code in yarn:
        to_china = engine.check(_shipment(code, "China", []))
        to_kabul = engine.check(_shipment(code, "Afghanistan", []))
        china_check = next(
            c for c in to_china.executable_rule_checks
            if c.check_id == "xr_yarn_afghanistan_duty_drawback"
        )
        kabul_check = next(
            c for c in to_kabul.executable_rule_checks
            if c.check_id == "xr_yarn_afghanistan_duty_drawback"
        )
        assert china_check.status is ComplianceCheckStatus.NOT_APPLICABLE
        assert kabul_check.status is ComplianceCheckStatus.MANUAL_REVIEW

    # And it applies to yarn only.
    non_yarn = next(c for c in ALL_CODES if c not in yarn)
    other = engine.check(_shipment(non_yarn, "Afghanistan", []))
    assert not [
        c for c in other.executable_rule_checks
        if c.check_id == "xr_yarn_afghanistan_duty_drawback"
    ]


@pytest.mark.parametrize("code", ALL_CODES)
def test_regulatory_evidence_is_attached_to_guidance(
    isolated_database: Engine, code: str
) -> None:
    """10. Each requirement carries an explicit evidence classification."""
    valid = {
        "direct_evidence", "indirect_support", "configured_rule_only",
        "evidence_unavailable", "conflicting_evidence",
    }
    with Session(isolated_database) as db:
        build_corpus(db)
        response = generate_pre_submission_guidance(
            db, product=supported_pct_products()[code], pct_code=code, destination="China"
        )
    for document in response.documents:
        assert document.evidence_class in valid
        if document.evidence_class == "direct_evidence":
            assert document.citations


def test_unsupported_pct_boundary_is_still_enforced(isolated_database: Engine) -> None:
    """11. Expanding the catalog did not dissolve the boundary."""
    assert UNSUPPORTED not in supported_pct_codes()
    supported, message, _, _ = validate_pct_scope(UNSUPPORTED, None)
    assert supported is False
    assert "validated textile PCT codes" in message

    with Session(isolated_database) as db:
        build_corpus(db)
        guidance = generate_pre_submission_guidance(
            db, product="Some garment", pct_code=UNSUPPORTED, destination="China"
        )
        chat = answer_regulatory_question(
            db, question=f"Is PCT {UNSUPPORTED} compliant?"
        )
    assert guidance.supported_scope is False
    assert guidance.documents == []
    assert chat.informational_only is True
    assert "is compliant" not in chat.answer.casefold()


@pytest.mark.parametrize("code", NEW_CODES)
def test_new_codes_record_their_export_policy_verification(code: str) -> None:
    """Every added code records how its licence/permit/approval was decided."""
    import json

    from app.services.compliance.rule_loader import PCT_CONFIG_PATH

    data = json.loads(PCT_CONFIG_PATH.read_text(encoding="utf-8"))
    entry = next(p for p in data["products"] if p["pct_code"] == code)
    status = entry["export_policy_schedule_status"]
    assert status["value"] == "absent_from_epo_2022_schedules"
    assert "export_policy_order_2022" in status["verified_against"]
    assert "paragraph 4(1)" in status["note"]
