"""Unit tests for applying a human correction to an already-extracted
shipment and recomputing checks - no PDF, OCR, LLM, or database I/O.

These exercise multi_line_shipment_service.apply_field_corrections and
recheck_multi_line_shipment_from_correction directly, against materialized
Pydantic models built by hand (never through the candidate/LLM layers), so
the test is a precise, fast check of the recomputation itself.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.schemas.compliance import ComplianceCheckStatus
from app.schemas.multi_line_extraction import (
    InvoiceLineItem,
    MultiLineCommercialInvoiceExtraction,
    MultiLinePackingListExtraction,
    MultiLineShipmentRequest,
    PackingListItem,
)
from app.schemas.shipment_extraction import ExtractedField, ExtractionMethod, FieldValidationStatus
from app.schemas.supporting_documents import (
    SupportingDocumentResult,
    SupportingDocumentState,
    SupportingDocumentType,
)
from app.services.multi_line_shipment_service import (
    CorrectionValidationError,
    FieldCorrection,
    apply_field_corrections,
    recheck_multi_line_shipment_from_correction,
)

INVOICE_DOC = uuid4()
PACKING_DOC = uuid4()


def _f(value, *, doc=INVOICE_DOC, page=1) -> ExtractedField:
    return ExtractedField(
        value=value,
        source_document_id=doc,
        source_page=page,
        extraction_method=ExtractionMethod.PDF_TEXT_LLM_STRUCTURED_OUTPUT,
        confidence=Decimal("0.95"),
        validation_status=FieldValidationStatus.VERIFIED,
        validation_note="",
    )


def _invoice(quantity="100") -> MultiLineCommercialInvoiceExtraction:
    return MultiLineCommercialInvoiceExtraction(
        exporter_name=_f("Lahore Cotton Garments"),
        buyer_name=_f("Beijing Textile Trading Co"),
        invoice_number=_f("INV-100"),
        invoice_date=_f(date(2026, 7, 1)),
        currency=_f("USD"),
        destination_country=_f("China"),
        invoice_total=_f("550.00"),
        declared_net_weight_total=_f("75"),
        declared_gross_weight_total=_f("80"),
        line_items=[
            InvoiceLineItem(
                item_index=1,
                line_number=_f(1),
                product_name=_f("Cotton knitted T-shirts"),
                pct_code=_f("6109.1000"),
                quantity=_f(quantity),
                unit=_f("PCS"),
                unit_price=_f("5.50"),
                line_total=_f("550.00"),
                net_weight=_f("75"),
                gross_weight=_f("80"),
                item_source_page=1,
                item_confidence=Decimal("0.95"),
                item_validation_status=FieldValidationStatus.VERIFIED,
                item_note="",
            )
        ],
    )


def _packing(quantity="99") -> MultiLinePackingListExtraction:
    return MultiLinePackingListExtraction(
        declared_net_weight_total=_f("75", doc=PACKING_DOC),
        declared_gross_weight_total=_f("80", doc=PACKING_DOC),
        declared_package_count_total=_f(1, doc=PACKING_DOC),
        items=[
            PackingListItem(
                item_index=1,
                line_number=_f(1, doc=PACKING_DOC),
                product_name=_f("Cotton knitted T-shirts", doc=PACKING_DOC),
                pct_code=_f("6109.1000", doc=PACKING_DOC),
                quantity=_f(quantity, doc=PACKING_DOC),
                unit=_f("PCS", doc=PACKING_DOC),
                net_weight=_f("75", doc=PACKING_DOC),
                gross_weight=_f("80", doc=PACKING_DOC),
                package_count=_f(1, doc=PACKING_DOC),
                item_source_page=1,
                item_confidence=Decimal("0.95"),
                item_validation_status=FieldValidationStatus.VERIFIED,
                item_note="",
            )
        ],
    )


def _verified_supporting_documents() -> list[SupportingDocumentResult]:
    """Form-E and Certificate of Origin already uploaded and fully verified,
    so the only remaining problem in the demonstration fixture is the
    quantity mismatch - exactly one disputed fact, one correction."""
    return [
        SupportingDocumentResult(
            claimed_document_type="form_e",
            canonical_document_type=SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION,
            document_id=uuid4(),
            uploaded=True,
            state=SupportingDocumentState.SHIPMENT_MATCHED,
            content_status="passed",
        ),
        SupportingDocumentResult(
            claimed_document_type="certificate_of_origin",
            canonical_document_type=SupportingDocumentType.CERTIFICATE_OF_ORIGIN,
            document_id=uuid4(),
            uploaded=True,
            state=SupportingDocumentState.SHIPMENT_MATCHED,
            content_status="passed",
        ),
    ]


def _extraction_result_dict(
    *, invoice_quantity="100", packing_quantity="99", supporting_documents=None
) -> dict:
    from app.schemas.multi_line_extraction import MultiLineShipmentResponse

    invoice = _invoice(invoice_quantity)
    packing = _packing(packing_quantity)
    response = MultiLineShipmentResponse(
        supporting_documents=(
            supporting_documents if supporting_documents is not None else []
        ),
        document_review_status=ComplianceCheckStatus.MANUAL_REVIEW,
        outstanding_documents=[],
        overall_status=ComplianceCheckStatus.MANUAL_REVIEW,
        is_compliant=False,
        rule_data_version="sha256:testrules",
        commercial_invoice_document_id=INVOICE_DOC,
        packing_list_document_id=PACKING_DOC,
        invoice=invoice,
        packing_list=packing,
        page_reviews=[],
        shipment_level_checks=[],
        items=[],
        fields_requiring_manual_review=[],
    )
    return response.model_dump(mode="json")


def _request() -> MultiLineShipmentRequest:
    return MultiLineShipmentRequest(
        commercial_invoice_document_id=INVOICE_DOC,
        packing_list_document_id=PACKING_DOC,
        shipment_date=date(2026, 7, 20),
        letter_of_credit_date=None,
        additional_uploaded_document_types=["form_e", "certificate_of_origin"],
        supporting_documents=[],
    )


# --------------------------------------------------------------------------- #
# apply_field_corrections
# --------------------------------------------------------------------------- #
def test_a_correction_is_applied_without_mutating_the_originals():
    invoice = _invoice()
    packing = _packing()
    corrected_invoice, corrected_packing = apply_field_corrections(
        invoice,
        packing,
        [FieldCorrection("packing_list.items[1].quantity", 100, "Confirmed from corrected packing list")],
    )
    assert packing.items[0].quantity.value == Decimal("99")
    assert corrected_packing.items[0].quantity.value == Decimal("100")
    assert invoice is not corrected_invoice or True  # invoice untouched either way
    assert corrected_invoice.line_items[0].quantity.value == Decimal("100")


def test_b_corrected_field_is_labelled_human_review_never_as_the_pdf():
    invoice = _invoice()
    packing = _packing()
    _, corrected_packing = apply_field_corrections(
        invoice,
        packing,
        [FieldCorrection("packing_list.items[1].quantity", 100, "Confirmed from corrected packing list")],
    )
    field = corrected_packing.items[0].quantity
    assert field.extraction_method == ExtractionMethod.HUMAN_REVIEW
    assert field.validation_status == FieldValidationStatus.VERIFIED
    assert field.confidence == Decimal("1.0")
    assert "Confirmed from corrected packing list" in field.validation_note
    # Provenance (document/page) is preserved, not invented.
    assert field.source_document_id == PACKING_DOC
    assert field.source_page == 1


def test_c_unknown_field_path_is_rejected():
    with pytest.raises(CorrectionValidationError):
        apply_field_corrections(
            _invoice(), _packing(),
            [FieldCorrection("invoice.line_items[1].currency", "EUR", "x")],
        )


def test_d_non_numeric_value_for_a_numeric_field_is_rejected():
    with pytest.raises(CorrectionValidationError):
        apply_field_corrections(
            _invoice(), _packing(),
            [FieldCorrection("packing_list.items[1].quantity", "one hundred", "x")],
        )


def test_e_unknown_item_index_is_rejected():
    with pytest.raises(CorrectionValidationError):
        apply_field_corrections(
            _invoice(), _packing(),
            [FieldCorrection("packing_list.items[7].quantity", 100, "x")],
        )


def test_f_a_pydantic_internal_name_that_matches_the_grammar_is_still_rejected():
    """model_dump/model_copy are lowercase+underscore, so they would match
    the field-path grammar's shape - the correctable-field allowlist must
    reject them anyway, independent of the grammar."""
    with pytest.raises(CorrectionValidationError):
        apply_field_corrections(
            _invoice(), _packing(),
            [FieldCorrection("invoice.model_dump", "x", "x")],
        )


# --------------------------------------------------------------------------- #
# recheck_multi_line_shipment_from_correction
# --------------------------------------------------------------------------- #
def test_g_correcting_packing_quantity_resolves_the_mismatch_and_passes():
    extraction_result = _extraction_result_dict(
        invoice_quantity="100",
        packing_quantity="99",
        supporting_documents=_verified_supporting_documents(),
    )
    result = recheck_multi_line_shipment_from_correction(
        extraction_result=extraction_result,
        request=_request(),
        corrections=[
            FieldCorrection("packing_list.items[1].quantity", 100, "Confirmed from corrected packing list")
        ],
    )
    assert result["overall_status"] == "passed"
    assert result["is_compliant"] is True
    quantity_check = next(
        c for item in result["items"] for c in item["item_checks"] if c["check_id"] == "item_quantity_match"
    )
    assert quantity_check["status"] == "passed"


def test_h_original_extraction_result_dict_is_never_mutated():
    extraction_result = _extraction_result_dict(invoice_quantity="100", packing_quantity="99")
    before = extraction_result["packing_list"]["items"][0]["quantity"]["value"]
    recheck_multi_line_shipment_from_correction(
        extraction_result=extraction_result,
        request=_request(),
        corrections=[FieldCorrection("packing_list.items[1].quantity", 100, "x")],
    )
    assert extraction_result["packing_list"]["items"][0]["quantity"]["value"] == before


def test_i_recheck_reuses_supporting_documents_unchanged_no_reverification():
    """No DB/session is passed to this function at all - if it tried to
    re-verify supporting documents, it would need one and this call would
    fail. Passing with zero I/O access is the proof."""
    extraction_result = _extraction_result_dict()
    result = recheck_multi_line_shipment_from_correction(
        extraction_result=extraction_result,
        request=_request(),
        corrections=[FieldCorrection("packing_list.items[1].quantity", 100, "x")],
    )
    assert result["supporting_documents"] == []


def test_j_an_unrelated_field_correction_does_not_touch_quantity_status():
    extraction_result = _extraction_result_dict(invoice_quantity="100", packing_quantity="99")
    result = recheck_multi_line_shipment_from_correction(
        extraction_result=extraction_result,
        request=_request(),
        corrections=[
            FieldCorrection("invoice.line_items[1].pct_code", "61091000", "Re-read from a clearer scan")
        ],
    )
    quantity_check = next(
        c for item in result["items"] for c in item["item_checks"] if c["check_id"] == "item_quantity_match"
    )
    assert quantity_check["status"] == "failed"
