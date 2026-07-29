"""Supporting-document verification tests (Stage 11, items 11-32).

The headline invariant: a claimed document-type *string* can never satisfy a
required-document check. Everything here is hermetic - extraction results are
constructed directly, so no Groq, no Tesseract, no database.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.schemas.compliance import ComplianceCheckStatus
from app.schemas.compliance import ShipmentComplianceInput
from app.schemas.shipment_extraction import ExtractionMethod, FieldValidationStatus
from app.schemas.supporting_documents import (
    AuthenticityStatus,
    SupportingDocumentExtraction,
    SupportingDocumentState,
    SupportingDocumentType,
    canonical_supporting_type,
)
from app.services.supporting_document_service import (
    present_document_types,
    verified_document_types,
    verify_supporting_document,
)
from app.services.compliance.rule_engine import DeterministicComplianceRuleEngine

DOC = uuid4()

SHIPMENT: dict[str, Any] = {
    "shipment_exporter": "Lahore Cotton Garments (Pvt.) Ltd.",
    "shipment_buyer": "Shanghai Sample Trading Co., Ltd.",
    "shipment_invoice_number": "LCG-INV-2026-002",
    "shipment_destination": "China",
    "shipment_pct_code": "6109.1000",
    "shipment_product": "Cotton knitted T-shirts",
    "shipment_date": date(2026, 6, 29),
    "shipment_invoice_total": Decimal("550.00"),
    "shipment_currency": "USD",
}

_ALL_FIELDS = tuple(SupportingDocumentExtraction.model_fields)


def _field(value: Any, page: int = 1, confidence: str = "0.96") -> dict[str, Any]:
    verified = value is not None
    return {
        "value": value,
        "source_document_id": str(DOC),
        "source_page": page,
        "extraction_method": ExtractionMethod.PDF_TEXT_LLM_STRUCTURED_OUTPUT.value,
        "confidence": Decimal(confidence) if verified else Decimal("0"),
        "validation_status": (
            FieldValidationStatus.VERIFIED.value
            if verified
            else FieldValidationStatus.MANUAL_REVIEW.value
        ),
        "validation_note": "",
    }


def _extraction(confidence: str = "0.96", **values: Any) -> SupportingDocumentExtraction:
    payload: dict[str, Any] = {}
    for name in _ALL_FIELDS:
        if name in {
            "document_source_page",
            "document_confidence",
            "document_validation_status",
            "document_note",
        }:
            continue
        payload[name] = _field(values.get(name), confidence=confidence)
    payload["document_source_page"] = 1
    payload["document_confidence"] = Decimal(confidence)
    payload["document_validation_status"] = FieldValidationStatus.VERIFIED.value
    payload["document_note"] = ""
    return SupportingDocumentExtraction.model_validate(payload)


def _verify(claimed: str, extraction, **overrides: Any):
    kwargs = {**SHIPMENT, **overrides}
    return verify_supporting_document(
        claimed_type=claimed,
        document_id=kwargs.pop("document_id", DOC),
        extraction=extraction,
        ocr_confidence=kwargs.pop("ocr_confidence", None),
        today=kwargs.pop("today", date(2026, 7, 1)),
        **kwargs,
    )


def _coo_extraction(**overrides: Any) -> SupportingDocumentExtraction:
    base: dict[str, Any] = dict(
        detected_document_type="certificate_of_origin",
        document_number="COO-TEST-001",
        exporter_or_applicant="Lahore Cotton Garments (Pvt.) Ltd.",
        destination_country="China",
        issuing_authority="Trade Development Authority of Pakistan",
        pct_code="6109.1000",
        product_or_commodity="Cotton knitted T-shirts",
        issue_date=date(2026, 6, 20),
    )
    base.update(overrides)
    return _extraction(**base)


# --------------------------------------------------------------------------- #
# 11-12: claimed-only can never count as present
# --------------------------------------------------------------------------- #
def test_11_claimed_string_without_uuid_does_not_count_as_present() -> None:
    result = _verify("form_e", None, document_id=None)
    assert result.uploaded is False
    assert result.state is SupportingDocumentState.CLAIMED_ONLY
    assert result.content_status == ComplianceCheckStatus.FAILED.value
    assert "not evidence" in (result.required_action or "")
    assert verified_document_types([result]) == set()


def test_12_required_document_uuid_missing_is_reported_as_failure() -> None:
    result = _verify("phytosanitary_certificate", None, document_id=None)
    assert result.content_status == ComplianceCheckStatus.FAILED.value
    assert result.checks[0].status == ComplianceCheckStatus.FAILED


# --------------------------------------------------------------------------- #
# 13-16: type + readability
# --------------------------------------------------------------------------- #
def test_13_uploaded_correct_document_type_verifies() -> None:
    result = _verify("certificate_of_origin", _coo_extraction())
    assert result.state is SupportingDocumentState.SHIPMENT_MATCHED
    assert result.content_status == ComplianceCheckStatus.PASSED.value
    assert "certificate_of_origin" in verified_document_types([result])


def test_14_uploaded_wrong_document_type_fails_and_is_preserved() -> None:
    wrong = _coo_extraction(detected_document_type="bill_of_lading")
    result = _verify("certificate_of_origin", wrong)
    assert result.state is SupportingDocumentState.TYPE_MISMATCH
    assert result.content_status == ComplianceCheckStatus.FAILED.value
    assert result.extraction is not None, "mismatched upload must be kept as evidence"
    assert verified_document_types([result]) == set()


def test_15_unreadable_supporting_document_is_manual_review() -> None:
    unreadable = _extraction(confidence="0")
    result = _verify("certificate_of_origin", unreadable)
    assert result.state is SupportingDocumentState.UNREADABLE
    assert result.content_status == ComplianceCheckStatus.MANUAL_REVIEW.value
    assert verified_document_types([result]) == set()


def test_16_low_confidence_ocr_is_surfaced_not_hidden() -> None:
    result = _verify(
        "certificate_of_origin", _coo_extraction(), ocr_confidence=Decimal("0.62")
    )
    assert result.ocr_confidence == Decimal("0.62")


# --------------------------------------------------------------------------- #
# 17-24: mismatches
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("field", "bad_value", "check_fragment"),
    [
        ("exporter_or_applicant", "Some Other Exporter Ltd.", "exporter_match"),
        ("destination_country", "Germany", "destination_match"),
        ("pct_code", "6302.3110", "pct_match"),
    ],
)
def test_17_to_21_field_mismatches_fail(
    field: str, bad_value: str, check_fragment: str
) -> None:
    result = _verify("certificate_of_origin", _coo_extraction(**{field: bad_value}))
    assert result.content_status == ComplianceCheckStatus.FAILED.value
    assert any(
        check_fragment in check.check_id
        and check.status == ComplianceCheckStatus.FAILED
        for check in result.checks
    )
    assert verified_document_types([result]) == set()
    assert "certificate_of_origin" in present_document_types([result])
    assert result.presence_status == "shipment_mismatched"


def test_19_invoice_reference_mismatch_fails() -> None:
    form_e = _extraction(
        detected_document_type="form_e_or_psw_export_declaration",
        document_number="FE-TEST-001",
        exporter_or_applicant=SHIPMENT["shipment_exporter"],
        invoice_reference="LCG-INV-9999-999",
    )
    result = _verify("form_e", form_e)
    assert result.content_status == ComplianceCheckStatus.FAILED.value
    assert any(
        "invoice_reference_match" in c.check_id
        and c.status == ComplianceCheckStatus.FAILED
        for c in result.checks
    )
    assert "form_e" in present_document_types([result])
    assert result.presence_status == "shipment_mismatched"


def test_mismatched_upload_is_present_but_never_verified() -> None:
    result = _verify(
        "form_e",
        _extraction(
            detected_document_type="form_e_or_psw_export_declaration",
            document_number="FE-TEST-002",
            exporter_or_applicant=SHIPMENT["shipment_exporter"],
            invoice_reference="WRONG-INVOICE",
        ),
    )
    assert "form_e" in present_document_types([result])
    assert "form_e" not in verified_document_types([result])
    assert result.content_status == ComplianceCheckStatus.FAILED.value
    assert result.presence_status == "shipment_mismatched"


def test_wrong_type_and_unreadable_uploads_are_not_present() -> None:
    wrong = _verify(
        "certificate_of_origin",
        _coo_extraction(detected_document_type="Ocean Bill of Lading"),
    )
    unreadable = _verify("certificate_of_origin", _extraction(confidence="0"))
    assert present_document_types([wrong, unreadable]) == set()
    assert wrong.presence_status == "invalid"
    assert unreadable.presence_status == "unresolved"


def test_mismatched_form_e_and_coo_are_not_also_called_missing() -> None:
    form_e = _verify(
        "form_e",
        _extraction(
            detected_document_type="form_e_or_psw_export_declaration",
            document_number="FE-TEST-003",
            exporter_or_applicant=SHIPMENT["shipment_exporter"],
            invoice_reference="WRONG-INVOICE",
        ),
    )
    coo = _verify(
        "certificate_of_origin",
        _coo_extraction(destination_country="Germany"),
    )
    present = sorted(present_document_types([form_e, coo]))
    response = DeterministicComplianceRuleEngine().check(
        ShipmentComplianceInput(
            product_name="Men's woven cotton trousers",
            pct_code="62034200",
            quantity=Decimal("100"),
            unit_price=Decimal("5.50"),
            invoice_line_total=Decimal("550"),
            invoice_total=Decimal("550"),
            net_weight=Decimal("75"),
            gross_weight=Decimal("80"),
            destination_country="China",
            shipment_date=date(2026, 7, 20),
            uploaded_document_types=present,
        )
    )
    document_checks = [
        check
        for check in [*response.checks, *response.executable_rule_checks]
        if check.required_document in {"form_e", "certificate_of_origin"}
    ]
    assert document_checks
    assert all("missing" not in check.message.casefold() for check in document_checks)
    assert form_e.content_status == coo.content_status == "failed"


def test_22_sbp_deposit_percentage_mismatch_fails() -> None:
    deposit = _extraction(
        detected_document_type="sbp_deposit_proof",
        document_number="SBP-DEP-001",
        exporter_or_applicant=SHIPMENT["shipment_exporter"],
        invoice_reference=SHIPMENT["shipment_invoice_number"],
        percentage=Decimal("0.5"),
    )
    result = _verify("sbp_deposit_proof", deposit)
    assert result.content_status == ComplianceCheckStatus.FAILED.value
    assert any(
        c.check_id == "supporting_sbp_deposit_percentage"
        and c.status == ComplianceCheckStatus.FAILED
        for c in result.checks
    )


def test_23_expired_permit_fails() -> None:
    permit = _extraction(
        detected_document_type="importing_country_permit",
        document_number="IP-TEST-001",
        issuing_authority="NPPO of the importing country",
        exporter_or_applicant=SHIPMENT["shipment_exporter"],
        destination_country=SHIPMENT["shipment_destination"],
        expiry_date=date(2026, 1, 1),
    )
    result = _verify("import_permit", permit, today=date(2026, 7, 1))
    assert result.content_status == ComplianceCheckStatus.FAILED.value
    assert any(
        "not_expired" in c.check_id and c.status == ComplianceCheckStatus.FAILED
        for c in result.checks
    )


def test_24_missing_certificate_number_is_manual_review() -> None:
    result = _verify("certificate_of_origin", _coo_extraction(document_number=None))
    assert result.content_status == ComplianceCheckStatus.MANUAL_REVIEW.value
    assert any(
        "required_fields" in c.check_id
        and c.status == ComplianceCheckStatus.MANUAL_REVIEW
        for c in result.checks
    )
    assert "certificate_of_origin" in verified_document_types([result])


# --------------------------------------------------------------------------- #
# 25-31: valid documents per type
# --------------------------------------------------------------------------- #
def test_25_valid_certificate_of_origin() -> None:
    result = _verify("certificate_of_origin", _coo_extraction())
    assert result.content_status == ComplianceCheckStatus.PASSED.value


def test_25b_trailing_abbreviation_period_is_not_an_exporter_mismatch() -> None:
    """A real defect found end-to-end: the invoice extractor read the
    exporter as "...Ltd" (no period) while the certificate-of-origin
    extractor read the same company as "...Ltd." (with one). Both are the
    same company printed with an inconsistent abbreviation period - not a
    mismatch a human would ever flag."""
    result = _verify(
        "certificate_of_origin",
        _coo_extraction(exporter_or_applicant="Lahore Cotton Garments (Pvt.) Ltd"),
        shipment_exporter="Lahore Cotton Garments (Pvt.) Ltd.",
    )
    exporter_check = next(
        check
        for check in result.checks
        if check.check_id == "supporting_certificate_of_origin_exporter_match"
    )
    assert exporter_check.status == ComplianceCheckStatus.PASSED.value


def test_26_valid_form_e_declaration() -> None:
    form_e = _extraction(
        detected_document_type="form_e_or_psw_export_declaration",
        document_number="FE-TEST-001",
        exporter_or_applicant=SHIPMENT["shipment_exporter"],
        invoice_reference=SHIPMENT["shipment_invoice_number"],
        bank_name="Synthetic Test Bank",
        amount=Decimal("550.00"),
        currency="USD",
    )
    result = _verify("form_e", form_e)
    assert result.content_status == ComplianceCheckStatus.PASSED.value
    assert result.canonical_document_type is (
        SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION
    )


def test_27_28_valid_sbp_documents() -> None:
    deposit = _extraction(
        detected_document_type="sbp_deposit_proof",
        document_number="SBP-DEP-001",
        exporter_or_applicant=SHIPMENT["shipment_exporter"],
        invoice_reference=SHIPMENT["shipment_invoice_number"],
        percentage=Decimal("1"),
    )
    confirmation = _extraction(
        detected_document_type="sbp_confirmation",
        document_number="SBP-CONF-001",
        exporter_or_applicant=SHIPMENT["shipment_exporter"],
        invoice_reference=SHIPMENT["shipment_invoice_number"],
        related_reference="SBP-DEP-001",
    )
    assert _verify("sbp_deposit_proof", deposit).content_status == "passed"
    # The confirmation only verifies against the deposit receipt actually
    # supplied with this shipment, not against any receipt number it prints.
    assert (
        _verify(
            "sbp_confirmation",
            confirmation,
            related_deposit_reference="SBP-DEP-001",
        ).content_status
        == "passed"
    )


def test_29_valid_irrevocable_letter_of_credit() -> None:
    lc = _extraction(
        detected_document_type="irrevocable_letter_of_credit",
        document_number="LC-TEST-001",
        exporter_or_applicant=SHIPMENT["shipment_exporter"],
        buyer_or_beneficiary=SHIPMENT["shipment_buyer"],
        issue_date=date(2026, 1, 1),
        amount=Decimal("550.00"),
        currency="USD",
        shipment_deadline=date(2026, 7, 29),
    )
    result = _verify("irrevocable_letter_of_credit", lc)
    assert result.content_status == ComplianceCheckStatus.PASSED.value


def test_30_valid_phytosanitary_certificate() -> None:
    phyto = _extraction(
        detected_document_type="phytosanitary_certificate",
        document_number="PHY-TEST-001",
        exporter_or_applicant="Multan Raw Cotton Traders (Pvt.) Ltd.",
        product_or_commodity="Raw cotton, other",
        destination_country="United Arab Emirates",
        issuing_authority="Department of Plant Protection",
    )
    result = _verify(
        "phytosanitary_certificate",
        phyto,
        shipment_exporter="Multan Raw Cotton Traders (Pvt.) Ltd.",
        shipment_destination="United Arab Emirates",
        shipment_product="Raw cotton, other",
        shipment_pct_code="5201.0090",
    )
    assert result.content_status == ComplianceCheckStatus.PASSED.value


def test_31_raw_cotton_document_set_all_verify() -> None:
    results = []
    cases: list[tuple[str, str, dict[str, Any]]] = [
        ("sbp_deposit_proof", "sbp_deposit_proof", {"percentage": Decimal("1")}),
        ("sbp_confirmation", "sbp_confirmation", {"related_reference": "SBPDEP-001"}),
        (
            "irrevocable_letter_of_credit",
            "irrevocable_letter_of_credit",
            {"issue_date": date(2026, 1, 1), "shipment_deadline": date(2026, 7, 1)},
        ),
    ]
    for claimed, detected, extra in cases:
        extraction = _extraction(
            detected_document_type=detected,
            document_number=f"{detected[:6].upper()}-001",
            exporter_or_applicant="Multan Raw Cotton Traders (Pvt.) Ltd.",
            invoice_reference="MRC-INV-2026-014",
            **extra,
        )
        results.append(
            _verify(
                claimed,
                extraction,
                shipment_exporter="Multan Raw Cotton Traders (Pvt.) Ltd.",
                shipment_invoice_number="MRC-INV-2026-014",
                shipment_destination="United Arab Emirates",
                shipment_product="Raw cotton, other",
                shipment_pct_code="5201.0090",
                shipment_date=date(2026, 6, 1),
                shipment_invoice_total=Decimal("2000.00"),
                related_deposit_reference="SBPDEP-001",
            )
        )
    assert all(r.content_status == "passed" for r in results)
    assert {
        "sbp_deposit_proof",
        "sbp_confirmation",
        "irrevocable_letter_of_credit",
    } <= verified_document_types(results)


# --------------------------------------------------------------------------- #
# 32: authenticity is never claimed
# --------------------------------------------------------------------------- #
def test_32_external_authenticity_is_never_claimed_verified() -> None:
    for result in (
        _verify("certificate_of_origin", _coo_extraction()),
        _verify("form_e", None, document_id=None),
    ):
        assert result.authenticity_status is AuthenticityStatus.NOT_EXTERNALLY_VERIFIED


def test_alias_mapping_is_stable() -> None:
    assert canonical_supporting_type("form_e") is (
        SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION
    )
    assert canonical_supporting_type("Certificate of Origin") is (
        SupportingDocumentType.CERTIFICATE_OF_ORIGIN
    )
    assert canonical_supporting_type("nonsense") is SupportingDocumentType.UNKNOWN


# --------------------------------------------------------------------------- #
# DEF-009: a field that is not printed is not evidence of an unreadable page
# --------------------------------------------------------------------------- #
def test_33_absent_fields_do_not_make_a_readable_document_unreadable() -> None:
    """Found live: every real supporting document came back ``unreadable``.

    ``SupportingDocumentCandidates`` is one flat model covering ten document
    types, so a certificate of origin legitimately has no deposit percentage, no
    LC shipment deadline and no bank name. Those come back null at confidence 0.
    Taking the document confidence as the minimum across *all* fields therefore
    yielded 0 for every real document, and confidence 0 means "could not be
    read" - so a perfectly legible certificate was reported as unreadable.

    This reproduces the exact shape of the live extraction: the printed fields
    read cleanly, the unprinted ones are null.
    """
    from app.schemas.supporting_documents import SupportingDocumentCandidates
    from app.services.extraction.document_bundle import DocumentTextBundle, StoredPage
    from app.services.supporting_document_service import _materialize

    printed = {
        "detected_document_type": "Certificate of Origin",
        "document_number": "SYN-COO-LCGINV2026002",
        "issue_date": "2026-06-25",
        "exporter_or_applicant": "Lahore Cotton Garments (Pvt.) Ltd.",
        "buyer_or_beneficiary": "Shanghai Sample Trading Co., Ltd.",
        "invoice_reference": "LCG-INV-2026-002",
        "pct_code": "6109.1000",
        "product_or_commodity": "Cotton knitted T-shirts",
        "destination_country": "China",
        "issuing_authority": "Lahore Chamber of Commerce and Industry",
        "quantity": Decimal("100"),
    }
    payload: dict[str, Any] = {}
    for name in SupportingDocumentCandidates.model_fields:
        value = printed.get(name)
        payload[name] = {
            "value": value,
            "source_page": 1 if value is not None else None,
            "confidence": Decimal("0.96") if value is not None else Decimal("0"),
            "validation_status": (
                FieldValidationStatus.VERIFIED.value
                if value is not None
                else FieldValidationStatus.MANUAL_REVIEW.value
            ),
            "validation_note": "" if value is not None else "Not printed on the page.",
        }
    candidates = SupportingDocumentCandidates.model_validate(payload)
    bundle = DocumentTextBundle(
        document_id=DOC,
        document_type="supporting_document",
        pages=[
            StoredPage(
                page_number=1,
                text=(
                    "CERTIFICATE OF ORIGIN Certificate Number "
                    "SYN-COO-LCGINV2026002 Exporter Lahore Cotton Garments"
                ),
            )
        ],
        reviews=[],
    )

    extraction = _materialize(candidates, bundle)
    assert extraction.document_confidence > 0, (
        "Unprinted fields must not drag document confidence to zero"
    )
    assert extraction.document_number.value == "SYN-COO-LCGINV2026002"

    result = _verify("certificate_of_origin", extraction)
    assert result.state is not SupportingDocumentState.UNREADABLE
    assert result.content_status == ComplianceCheckStatus.PASSED.value


def test_34_document_with_nothing_readable_is_still_unreadable() -> None:
    """The other half of DEF-009: recovering nothing must still be unreadable."""
    empty = _extraction(confidence="0")
    result = _verify("certificate_of_origin", empty)
    assert result.state is SupportingDocumentState.UNREADABLE
    assert result.content_status == ComplianceCheckStatus.MANUAL_REVIEW.value


# --------------------------------------------------------------------------- #
# DEF-010: a document that names itself in prose must still classify
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        # The exact live failure: a correct Form-E described itself more fully
        # than the alias table did, and was rejected as the wrong document.
        (
            "Pakistan Single Window export declaration (synthetic)",
            SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION,
        ),
        ("FORM E EXPORT DECLARATION", SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION),
        ("Certificate of Origin", SupportingDocumentType.CERTIFICATE_OF_ORIGIN),
        (
            "Certificate of Origin (CPFTA - China-Pakistan Free Trade Agreement)",
            SupportingDocumentType.CERTIFICATE_OF_ORIGIN,
        ),
        ("Irrevocable Letter of Credit", SupportingDocumentType.IRREVOCABLE_LETTER_OF_CREDIT),
        ("Phytosanitary Certificate", SupportingDocumentType.PHYTOSANITARY_CERTIFICATE),
        ("Ocean Bill of Lading", SupportingDocumentType.BILL_OF_LADING),
    ],
)
def test_35_prose_document_titles_resolve_to_their_canonical_type(
    printed: str, expected: SupportingDocumentType
) -> None:
    assert canonical_supporting_type(printed) is expected


@pytest.mark.parametrize(
    "printed",
    [
        # Two different document types named at once: unresolvable, so it must
        # not be guessed in either direction.
        "Bill of Lading and Certificate of Origin",
        "Certificate of origin / phytosanitary certificate combined form",
        # Nothing recognisable at all.
        "Quarterly Warehouse Stock Summary",
        "",
    ],
)
def test_36_ambiguous_or_unknown_titles_never_guess(printed: str) -> None:
    assert canonical_supporting_type(printed) is SupportingDocumentType.UNKNOWN


def test_37_containment_never_upgrades_a_genuinely_wrong_document() -> None:
    """The relaxation must not let a real mismatch through."""
    wrong = _coo_extraction(detected_document_type="Ocean Bill of Lading")
    result = _verify("certificate_of_origin", wrong)
    assert result.state is SupportingDocumentState.TYPE_MISMATCH
    assert result.content_status == ComplianceCheckStatus.FAILED.value
    assert verified_document_types([result]) == set()


# --------------------------------------------------------------------------- #
# DEF-011: the presence set and the rule vocabulary must be the same vocabulary
# --------------------------------------------------------------------------- #
def test_38_verified_types_are_emitted_under_the_names_the_rules_use() -> None:
    """Found live: a fully verified Form-E still failed `required_document_form_e`.

    The rule data names the document `form_e`; the canonical type is
    `form_e_or_psw_export_declaration`. The presence set spoke one vocabulary
    and the rules the other, so an uploaded, read and cross-checked declaration
    was reported as missing.
    """
    form_e = _extraction(
        detected_document_type="Form E Export Declaration",
        document_number="SYN-FORME-FYSINV2026101",
        exporter_or_applicant=SHIPMENT["shipment_exporter"],
        invoice_reference=SHIPMENT["shipment_invoice_number"],
        amount=Decimal("550.00"),
        currency="USD",
    )
    verified = verified_document_types([_verify("form_e", form_e)])
    assert "form_e" in verified, "the rule data's own name must be present"
    assert "form_e_or_psw_export_declaration" in verified


def test_39_alias_expansion_never_admits_an_unverified_document() -> None:
    """Widening names must not widen *which* documents count."""
    claimed_only = _verify("form_e", None, document_id=None)
    assert verified_document_types([claimed_only]) == set()

    wrong_type = _verify(
        "certificate_of_origin",
        _coo_extraction(detected_document_type="Ocean Bill of Lading"),
    )
    assert verified_document_types([wrong_type]) == set()


# --------------------------------------------------------------------------- #
# DEF-012: a provider outage is not a statement about the document
# --------------------------------------------------------------------------- #
def test_40_provider_outage_is_not_reported_as_an_unreadable_document() -> None:
    """Found live when the Groq quota ran out mid-run.

    Two perfectly legible PDFs came back as ``unreadable`` / ``manual_review``
    with "the uploaded document could not be read reliably" - blaming the
    trader's paperwork for our own rate limit, and settling a shipment on a
    status that had never actually been assessed. An unserved request is not
    evidence about the document, so it must surface as a provider failure (503)
    exactly as the invoice path already does.
    """
    from app.core.exceptions import StructuredExtractionProviderUnavailableError
    from app.schemas.supporting_documents import SupportingDocumentRef
    from app.services import supporting_document_service as service

    def unavailable(db: Any, document_id: Any) -> Any:
        raise StructuredExtractionProviderUnavailableError("rate limited")

    original = service.extract_supporting_document
    service.extract_supporting_document = unavailable  # type: ignore[assignment]
    try:
        with pytest.raises(StructuredExtractionProviderUnavailableError):
            service.verify_supporting_documents(
                None,  # type: ignore[arg-type]
                supporting_documents=[
                    SupportingDocumentRef(
                        document_type="certificate_of_origin", document_id=DOC
                    )
                ],
                claimed_only_types=[],
                shipment_exporter=SHIPMENT["shipment_exporter"],
                shipment_buyer=SHIPMENT["shipment_buyer"],
                shipment_invoice_number=SHIPMENT["shipment_invoice_number"],
                shipment_destination=SHIPMENT["shipment_destination"],
                shipment_pct_code=SHIPMENT["shipment_pct_code"],
                shipment_product=SHIPMENT["shipment_product"],
            )
    finally:
        service.extract_supporting_document = original  # type: ignore[assignment]


def test_41_a_genuinely_bad_pdf_is_still_reported_as_unreadable() -> None:
    """The DEF-012 fix must not stop reporting real extraction failures."""
    from app.core.exceptions import PdfExtractionError
    from app.schemas.supporting_documents import SupportingDocumentRef
    from app.services import supporting_document_service as service

    def bad_pdf(db: Any, document_id: Any) -> Any:
        raise PdfExtractionError("no text layer and OCR recovered nothing")

    original = service.extract_supporting_document
    service.extract_supporting_document = bad_pdf  # type: ignore[assignment]
    try:
        results = service.verify_supporting_documents(
            None,  # type: ignore[arg-type]
            supporting_documents=[
                SupportingDocumentRef(
                    document_type="certificate_of_origin", document_id=DOC
                )
            ],
            claimed_only_types=[],
            shipment_exporter=SHIPMENT["shipment_exporter"],
            shipment_buyer=SHIPMENT["shipment_buyer"],
            shipment_invoice_number=SHIPMENT["shipment_invoice_number"],
            shipment_destination=SHIPMENT["shipment_destination"],
            shipment_pct_code=SHIPMENT["shipment_pct_code"],
            shipment_product=SHIPMENT["shipment_product"],
        )
    finally:
        service.extract_supporting_document = original  # type: ignore[assignment]

    assert len(results) == 1
    assert results[0].state is SupportingDocumentState.UNREADABLE
    assert results[0].content_status == ComplianceCheckStatus.MANUAL_REVIEW.value


def test_42_malformed_provider_output_is_not_reported_as_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider response that fails schema validation is an HTTP 502 path.

    Unlike a genuinely unreadable PDF, malformed structured output says nothing
    about the trader's document. Swallowing it both misclassified the document
    and allowed a later request for the shared UUID to spend provider quota
    again.
    """
    from app.core.exceptions import (
        StructuredExtractionProviderError,
        StructuredExtractionProviderUnavailableError,
    )
    from app.schemas.supporting_documents import SupportingDocumentRef
    from app.services import supporting_document_service as service

    def malformed_output(db: Any, document_id: Any) -> Any:
        raise StructuredExtractionProviderError(
            "provider JSON failed local validation",
            code="schema_validation_failed",
        )

    monkeypatch.setattr(service, "extract_supporting_document", malformed_output)

    with pytest.raises(StructuredExtractionProviderError) as caught:
        service.verify_supporting_documents(
            None,  # type: ignore[arg-type]
            supporting_documents=[
                SupportingDocumentRef(
                    document_type="certificate_of_origin", document_id=DOC
                )
            ],
            claimed_only_types=[],
            shipment_exporter=SHIPMENT["shipment_exporter"],
            shipment_buyer=SHIPMENT["shipment_buyer"],
            shipment_invoice_number=SHIPMENT["shipment_invoice_number"],
            shipment_destination=SHIPMENT["shipment_destination"],
            shipment_pct_code=SHIPMENT["shipment_pct_code"],
            shipment_product=SHIPMENT["shipment_product"],
        )

    assert caught.value.code == "schema_validation_failed"
    assert not isinstance(
        caught.value, StructuredExtractionProviderUnavailableError
    )
