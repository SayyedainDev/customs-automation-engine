import asyncio
from datetime import date
from decimal import Decimal

import httpx
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.main import app
from app.models.documents import DocumentUploadRecord
from app.schemas.compliance import ComplianceCheckStatus
from app.schemas.ocr import OcrPageResult, OcrValidationStatus
from app.schemas.multi_line_extraction import (
    InvoiceLineItemCandidate,
    ItemMatchStatus,
    ItemMatchStrategy,
    MultiLineInvoiceCandidates,
    MultiLinePackingListCandidates,
    MultiLineShipmentRequest,
    PackingListItemCandidate,
    ShipmentItemResult,
)
from app.schemas.shipment_extraction import CandidateField, FieldValidationStatus
from app.services import multi_line_shipment_service
from app.services.extraction import document_bundle
from app.services.multi_line_shipment_service import _overall_shipment_status
from tests.unit.test_shipment_extraction import add_extracted_document


# --------------------------------------------------------------------------- #
# Candidate builders.
# --------------------------------------------------------------------------- #
def field(
    value,
    *,
    source_page: int | None = 1,
    confidence: str = "0.99",
    status: FieldValidationStatus = FieldValidationStatus.VERIFIED,
    note: str = "Clearly printed on the source page.",
) -> CandidateField:
    return CandidateField(
        value=value,
        source_page=source_page,
        confidence=Decimal(confidence),
        validation_status=status,
        validation_note=note,
    )


def missing_field(note: str = "Not printed on the document.") -> CandidateField:
    return field(
        None,
        source_page=None,
        confidence="0",
        status=FieldValidationStatus.MANUAL_REVIEW,
        note=note,
    )


def invoice_item(
    *,
    line_number=1,
    product="Cotton knitted T-shirts",
    pct="6109.1000",
    quantity="100",
    unit_price="5.50",
    line_total="550.00",
    net_weight="75",
    gross_weight="80",
    source_page=1,
) -> dict:
    return {
        "line_number": (
            field(line_number, source_page=source_page)
            if line_number is not None
            else missing_field("No line reference printed.")
        ),
        "product_name": (
            field(product, source_page=source_page)
            if product is not None
            else missing_field("No product name printed.")
        ),
        "pct_code": (
            field(pct, source_page=source_page)
            if pct is not None
            else missing_field("No PCT code printed.")
        ),
        "quantity": (
            field(Decimal(quantity), source_page=source_page)
            if quantity is not None
            else missing_field("No quantity printed.")
        ),
        "unit": field("PCS", source_page=source_page),
        "unit_price": (
            field(Decimal(unit_price), source_page=source_page)
            if unit_price is not None
            else missing_field("No unit price printed.")
        ),
        "line_total": (
            field(Decimal(line_total), source_page=source_page)
            if line_total is not None
            else missing_field("No line total printed.")
        ),
        "net_weight": (
            field(Decimal(net_weight), source_page=source_page)
            if net_weight is not None
            else missing_field("No net weight printed.")
        ),
        "gross_weight": (
            field(Decimal(gross_weight), source_page=source_page)
            if gross_weight is not None
            else missing_field("No gross weight printed.")
        ),
    }


def packing_item(
    *,
    line_number=1,
    product="Cotton knitted T-shirts",
    pct="6109.1000",
    quantity="100",
    net_weight="75",
    gross_weight="80",
    package_count=10,
    source_page=1,
) -> dict:
    return {
        "line_number": (
            field(line_number, source_page=source_page)
            if line_number is not None
            else missing_field("No item reference printed.")
        ),
        "product_name": (
            field(product, source_page=source_page)
            if product is not None
            else missing_field("No product name printed.")
        ),
        "pct_code": (
            field(pct, source_page=source_page)
            if pct is not None
            else missing_field("No PCT code printed.")
        ),
        "quantity": (
            field(Decimal(quantity), source_page=source_page)
            if quantity is not None
            else missing_field("No quantity printed.")
        ),
        "unit": field("PCS", source_page=source_page),
        "net_weight": (
            field(Decimal(net_weight), source_page=source_page)
            if net_weight is not None
            else missing_field("No net weight printed.")
        ),
        "gross_weight": (
            field(Decimal(gross_weight), source_page=source_page)
            if gross_weight is not None
            else missing_field("No gross weight printed.")
        ),
        "package_count": field(package_count, source_page=source_page),
    }


def invoice_candidates(
    *,
    items: list[dict],
    invoice_total="1100.00",
    destination="China",
    declared_net=None,
    declared_gross=None,
) -> MultiLineInvoiceCandidates:
    return MultiLineInvoiceCandidates(
        exporter_name=field("Demo Textile Exporter"),
        buyer_name=field("China Textile Buyer"),
        invoice_number=field("INV-2001"),
        invoice_date=field(date(2026, 7, 20)),
        currency=field("USD"),
        destination_country=field(destination),
        invoice_total=(
            field(Decimal(invoice_total))
            if invoice_total is not None
            else missing_field("No invoice total printed.")
        ),
        declared_net_weight_total=(
            field(Decimal(declared_net))
            if declared_net is not None
            else missing_field("No declared net-weight total printed.")
        ),
        declared_gross_weight_total=(
            field(Decimal(declared_gross))
            if declared_gross is not None
            else missing_field("No declared gross-weight total printed.")
        ),
        line_items=[InvoiceLineItemCandidate.model_validate(i) for i in items],
    )


def packing_candidates(
    *,
    items: list[dict],
    declared_net=None,
    declared_gross=None,
) -> MultiLinePackingListCandidates:
    return MultiLinePackingListCandidates(
        declared_net_weight_total=(
            field(Decimal(declared_net))
            if declared_net is not None
            else missing_field("No declared net-weight total printed.")
        ),
        declared_gross_weight_total=(
            field(Decimal(declared_gross))
            if declared_gross is not None
            else missing_field("No declared gross-weight total printed.")
        ),
        items=[PackingListItemCandidate.model_validate(i) for i in items],
    )


def configure_fake_llm(
    monkeypatch,
    invoice: MultiLineInvoiceCandidates,
    packing: MultiLinePackingListCandidates,
) -> None:
    def fake_structured_output(**kwargs):
        response_model = kwargs["response_model"]
        if response_model is MultiLineInvoiceCandidates:
            return invoice
        if response_model is MultiLinePackingListCandidates:
            return packing
        raise AssertionError("Unexpected structured-output model.")

    monkeypatch.setattr(
        multi_line_shipment_service,
        "extract_structured_model_from_text",
        fake_structured_output,
    )


def phase_2c_request(
    invoice_document_id,
    packing_document_id,
) -> MultiLineShipmentRequest:
    return MultiLineShipmentRequest(
        commercial_invoice_document_id=invoice_document_id,
        packing_list_document_id=packing_document_id,
        shipment_date=date(2026, 7, 20),
        additional_uploaded_document_types=["form_e", "certificate_of_origin"],
    )


def item_and_shipment_status(result) -> ComplianceCheckStatus:
    """Status of the matching/arithmetic subject under test only.

    Supporting-document presence is deliberately excluded: these tests claim
    document *types* without uploading the PDFs, which since supporting-document
    verification landed is correctly a document-presence failure. That is
    asserted separately by `assert_claimed_only_documents_do_not_count`.
    """
    statuses = [
        check.status for item in result.items for check in item.item_checks
    ]
    statuses.extend(check.status for check in result.shipment_level_checks)
    # An item with no packing counterpart is surfaced by match_status, which the
    # per-item cross-document checks cannot express, so fold it in here.
    statuses.extend(
        ComplianceCheckStatus.MANUAL_REVIEW
        for item in result.items
        if item.match_status != ItemMatchStatus.MATCHED
    )
    if ComplianceCheckStatus.FAILED in statuses:
        return ComplianceCheckStatus.FAILED
    if ComplianceCheckStatus.MANUAL_REVIEW in statuses:
        return ComplianceCheckStatus.MANUAL_REVIEW
    return ComplianceCheckStatus.PASSED


def assert_claimed_only_documents_do_not_count(result) -> None:
    """A document-type string with no uploaded PDF is never evidence."""
    claimed = [d for d in result.supporting_documents if not d.uploaded]
    assert claimed, "expected the claimed-only documents to be reported"
    assert all(d.state.value == "claimed_only" for d in claimed)
    assert all(d.content_status == "failed" for d in claimed)


def run_phase_2c(engine: Engine, request: MultiLineShipmentRequest):
    with Session(engine) as db:
        return (
            multi_line_shipment_service
            .extract_match_and_check_multi_line_shipment(db, request)
        )


def two_matching_documents(engine: Engine):
    invoice_id = add_extracted_document(
        engine,
        original_filename="invoice.pdf",
        text="Commercial invoice INV-2001 with two product lines.",
    )
    packing_id = add_extracted_document(
        engine,
        original_filename="packing.pdf",
        text="Packing list with two product lines.",
    )
    return invoice_id, packing_id


def two_line_invoice(**overrides) -> MultiLineInvoiceCandidates:
    return invoice_candidates(
        items=[
            invoice_item(),
            invoice_item(
                line_number=2,
                product="Denim fabric",
                pct="5209.4200",
                quantity="50",
                unit_price="11.00",
                line_total="550.00",
                net_weight="60",
                gross_weight="65",
            ),
        ],
        **overrides,
    )


def two_line_packing(**overrides) -> MultiLinePackingListCandidates:
    return packing_candidates(
        items=[
            packing_item(),
            packing_item(
                line_number=2,
                product="Denim fabric",
                pct="5209.4200",
                quantity="50",
                net_weight="60",
                gross_weight="65",
            ),
        ],
        **overrides,
    )


def item_by_reference(items: list[ShipmentItemResult], reference: str):
    return next(item for item in items if item.item_reference == reference)


# --------------------------------------------------------------------------- #
# 1. Two valid matching items.
# --------------------------------------------------------------------------- #
def test_two_valid_matching_items(isolated_database, monkeypatch) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(monkeypatch, two_line_invoice(), two_line_packing())

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )

    assert len(result.items) == 2
    assert all(
        item.match_status == ItemMatchStatus.MATCHED for item in result.items
    )
    assert all(
        item.match_strategy == ItemMatchStrategy.LINE_REFERENCE
        for item in result.items
    )
    # Every per-item cross-document check that applies must pass.
    for item in result.items:
        for check in item.item_checks:
            assert check.status in (
                ComplianceCheckStatus.PASSED,
                ComplianceCheckStatus.NOT_APPLICABLE,
            )
    # No item failed and no shipment-level check failed.
    assert item_and_shipment_status(result) != ComplianceCheckStatus.FAILED
    assert_claimed_only_documents_do_not_count(result)
    sum_check = next(
        check
        for check in result.shipment_level_checks
        if check.check_id == "sum_line_totals_match_invoice_total"
    )
    assert sum_check.status == ComplianceCheckStatus.PASSED


# --------------------------------------------------------------------------- #
# 2. Invoice-total sum mismatch.
# --------------------------------------------------------------------------- #
def test_invoice_total_sum_mismatch_fails_shipment(
    isolated_database, monkeypatch
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(
        monkeypatch,
        two_line_invoice(invoice_total="1200.00"),
        two_line_packing(),
    )

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    sum_check = next(
        check
        for check in result.shipment_level_checks
        if check.check_id == "sum_line_totals_match_invoice_total"
    )

    assert sum_check.status == ComplianceCheckStatus.FAILED
    assert item_and_shipment_status(result) == ComplianceCheckStatus.FAILED
    assert result.is_compliant is False


# --------------------------------------------------------------------------- #
# 3. One missing packing-list item.
# --------------------------------------------------------------------------- #
def test_one_missing_packing_list_item(isolated_database, monkeypatch) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(
        monkeypatch,
        two_line_invoice(),
        packing_candidates(items=[packing_item()]),
    )

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    invoice_only = [
        item
        for item in result.items
        if item.match_status == ItemMatchStatus.INVOICE_ONLY
    ]
    single_doc_check = next(
        check
        for check in result.shipment_level_checks
        if check.check_id == "items_present_in_both_documents"
    )

    assert len(invoice_only) == 1
    assert invoice_only[0].status == ComplianceCheckStatus.MANUAL_REVIEW
    assert invoice_only[0].compliance is None
    assert "could not be matched" in invoice_only[0].match_note
    assert single_doc_check.status == ComplianceCheckStatus.MANUAL_REVIEW
    assert item_and_shipment_status(result) == ComplianceCheckStatus.MANUAL_REVIEW


# --------------------------------------------------------------------------- #
# 4. Extra packing-list item.
# --------------------------------------------------------------------------- #
def test_extra_packing_list_item(isolated_database, monkeypatch) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(
        monkeypatch,
        invoice_candidates(items=[invoice_item()], invoice_total="550.00"),
        two_line_packing(),
    )

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    packing_only = [
        item
        for item in result.items
        if item.match_status == ItemMatchStatus.PACKING_ONLY
    ]

    assert len(packing_only) == 1
    assert packing_only[0].packing_item_index == 2
    assert packing_only[0].status == ComplianceCheckStatus.MANUAL_REVIEW
    assert item_and_shipment_status(result) == ComplianceCheckStatus.MANUAL_REVIEW


# --------------------------------------------------------------------------- #
# 5. Same product name with different PCT codes.
# --------------------------------------------------------------------------- #
def test_same_product_name_different_pct_codes(
    isolated_database, monkeypatch
) -> None:
    invoice = invoice_candidates(
        items=[
            invoice_item(line_number=1, product="Cotton article", pct="6109.1000"),
            invoice_item(
                line_number=2,
                product="Cotton article",
                pct="5209.4200",
                quantity="50",
                unit_price="11.00",
                line_total="550.00",
                net_weight="60",
                gross_weight="65",
            ),
        ],
    )
    packing = packing_candidates(
        items=[
            packing_item(line_number=1, product="Cotton article", pct="6109.1000"),
            packing_item(
                line_number=2,
                product="Cotton article",
                pct="5209.4200",
                quantity="50",
                net_weight="60",
                gross_weight="65",
            ),
        ],
    )
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(monkeypatch, invoice, packing)

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    first = item_by_reference(result.items, "invoice_line_1")
    second = item_by_reference(result.items, "invoice_line_2")

    # Line reference disambiguates the identical product names, and each item
    # is paired with the packing item that shares its PCT code.
    assert first.match_strategy == ItemMatchStrategy.LINE_REFERENCE
    assert second.match_strategy == ItemMatchStrategy.LINE_REFERENCE
    assert first.pct_code == "61091000"
    assert second.pct_code == "52094200"
    for item in (first, second):
        pct_check = next(
            check
            for check in item.item_checks
            if check.check_id == "item_pct_code_match"
        )
        assert pct_check.status == ComplianceCheckStatus.PASSED


# --------------------------------------------------------------------------- #
# 6. Duplicate PCT codes on separate valid lines.
# --------------------------------------------------------------------------- #
def test_duplicate_pct_codes_on_separate_valid_lines(
    isolated_database, monkeypatch
) -> None:
    invoice = invoice_candidates(
        items=[
            invoice_item(line_number=1, product="Cotton T-shirt small"),
            invoice_item(
                line_number=2,
                product="Cotton T-shirt large",
                pct="6109.1000",
                quantity="50",
                unit_price="11.00",
                line_total="550.00",
                net_weight="60",
                gross_weight="65",
            ),
        ],
    )
    packing = packing_candidates(
        items=[
            packing_item(line_number=1, product="Cotton T-shirt small"),
            packing_item(
                line_number=2,
                product="Cotton T-shirt large",
                pct="6109.1000",
                quantity="50",
                net_weight="60",
                gross_weight="65",
            ),
        ],
    )
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(monkeypatch, invoice, packing)

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    duplicate_check = next(
        check
        for check in result.shipment_level_checks
        if check.check_id == "duplicate_invoice_lines"
    )

    assert all(
        item.match_status == ItemMatchStatus.MATCHED for item in result.items
    )
    assert all(
        item.match_strategy == ItemMatchStrategy.LINE_REFERENCE
        for item in result.items
    )
    # Two valid lines that merely share a PCT code are not duplicates.
    assert duplicate_check.status == ComplianceCheckStatus.PASSED


# --------------------------------------------------------------------------- #
#    Duplicate invoice lines (genuine repeat) are flagged.
# --------------------------------------------------------------------------- #
def test_duplicate_invoice_lines_are_flagged(
    isolated_database, monkeypatch
) -> None:
    invoice = invoice_candidates(
        items=[invoice_item(line_number=1), invoice_item(line_number=1)],
        invoice_total="1100.00",
    )
    packing = two_line_packing()
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(monkeypatch, invoice, packing)

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    duplicate_check = next(
        check
        for check in result.shipment_level_checks
        if check.check_id == "duplicate_invoice_lines"
    )

    assert duplicate_check.status == ComplianceCheckStatus.FAILED
    assert item_and_shipment_status(result) == ComplianceCheckStatus.FAILED


# --------------------------------------------------------------------------- #
# 7. Quantity mismatch on only one item.
# --------------------------------------------------------------------------- #
def test_quantity_mismatch_on_one_item(isolated_database, monkeypatch) -> None:
    packing = packing_candidates(
        items=[
            packing_item(),
            packing_item(
                line_number=2,
                product="Denim fabric",
                pct="5209.4200",
                quantity="45",
                net_weight="60",
                gross_weight="65",
            ),
        ],
    )
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(monkeypatch, two_line_invoice(), packing)

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    first = item_by_reference(result.items, "invoice_line_1")
    second = item_by_reference(result.items, "invoice_line_2")
    second_quantity = next(
        check
        for check in second.item_checks
        if check.check_id == "item_quantity_match"
    )

    # Only the mismatched line may fail: the intact line's own cross-document
    # checks must all still be clean. (Its overall item status now also carries
    # the claimed-only Form-E failure, which is asserted separately.)
    assert all(
        check.status != ComplianceCheckStatus.FAILED
        for check in first.item_checks
    )
    assert second_quantity.status == ComplianceCheckStatus.FAILED
    assert second.status == ComplianceCheckStatus.FAILED
    assert item_and_shipment_status(result) == ComplianceCheckStatus.FAILED
    # The mismatched quantity is withheld from that item's compliance input.
    assert second.shipment_input is not None
    assert second.shipment_input.quantity is None


# --------------------------------------------------------------------------- #
# 8. Uncertain item matching.
# --------------------------------------------------------------------------- #
def test_uncertain_item_cannot_be_matched(
    isolated_database, monkeypatch
) -> None:
    invoice = invoice_candidates(
        items=[
            invoice_item(),
            # No line reference, no PCT, and an unreadable product name.
            invoice_item(
                line_number=None,
                product=None,
                pct=None,
                quantity="50",
                unit_price="11.00",
                line_total="550.00",
                net_weight="60",
                gross_weight="65",
            ),
        ],
    )
    packing = packing_candidates(items=[packing_item()])
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(monkeypatch, invoice, packing)

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    uncertain = item_by_reference(result.items, "invoice_line_2")

    assert uncertain.match_status == ItemMatchStatus.INVOICE_ONLY
    assert uncertain.match_strategy is None
    assert uncertain.status == ComplianceCheckStatus.MANUAL_REVIEW
    assert uncertain.compliance is None
    assert uncertain.fields_requiring_manual_review  # explains what was unclear
    assert item_and_shipment_status(result) == ComplianceCheckStatus.MANUAL_REVIEW


# --------------------------------------------------------------------------- #
# 9. One supported and one unsupported product.
# --------------------------------------------------------------------------- #
def test_one_supported_and_one_unsupported_product(
    isolated_database, monkeypatch
) -> None:
    invoice = invoice_candidates(
        items=[
            invoice_item(),
            invoice_item(
                line_number=2,
                product="Unlisted machine part",
                pct="8471.3000",
                quantity="50",
                unit_price="11.00",
                line_total="550.00",
                net_weight="60",
                gross_weight="65",
            ),
        ],
    )
    packing = packing_candidates(
        items=[
            packing_item(),
            packing_item(
                line_number=2,
                product="Unlisted machine part",
                pct="8471.3000",
                quantity="50",
                net_weight="60",
                gross_weight="65",
            ),
        ],
    )
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(monkeypatch, invoice, packing)

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    supported = item_by_reference(result.items, "invoice_line_1")
    unsupported = item_by_reference(result.items, "invoice_line_2")

    assert supported.compliance is not None
    assert supported.compliance.supported_product is True
    assert unsupported.compliance is not None
    assert unsupported.compliance.supported_product is False
    support_check = next(
        check
        for check in unsupported.compliance.checks
        if check.check_id == "mvp_pct_support"
    )
    assert support_check.status == ComplianceCheckStatus.MANUAL_REVIEW


# --------------------------------------------------------------------------- #
# 10. Scanned multi-line invoice using OCR.
# --------------------------------------------------------------------------- #
def test_scanned_multi_line_invoice_uses_ocr(
    isolated_database, monkeypatch
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="scanned_invoice.pdf",
        text="",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list with two product lines.",
    )

    def fake_ocr(**kwargs):
        return OcrPageResult(
            document_id=kwargs["document_id"],
            page_number=kwargs["page_number"],
            original_embedded_text=kwargs["original_embedded_text"],
            ocr_text=(
                "Commercial invoice INV-2001 lines: cotton knitted T-shirts "
                "PCT 6109.1000 and denim fabric PCT 5209.4200."
            ),
            ocr_confidence=Decimal("0.95"),
            validation_status=OcrValidationStatus.VERIFIED,
            validation_note="OCR test result.",
        )

    monkeypatch.setattr(document_bundle, "ocr_pdf_page", fake_ocr)
    configure_fake_llm(monkeypatch, two_line_invoice(), two_line_packing())

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    invoice_page = next(
        review
        for review in result.page_reviews
        if review.document_id == invoice_id
    )

    assert invoice_page.requires_ocr is True
    assert invoice_page.ocr_attempted is True
    assert invoice_page.ocr_validation_status == "verified"
    assert len(result.invoice.line_items) == 2
    assert (
        result.invoice.line_items[0].product_name.extraction_method
        == "tesseract_ocr_llm_structured_output"
    )
    assert result.invoice.line_items[0].product_name.value is not None
    assert all(
        item.match_status == ItemMatchStatus.MATCHED for item in result.items
    )


# --------------------------------------------------------------------------- #
# 11. Successful item-level compliance handoff.
# --------------------------------------------------------------------------- #
def test_successful_item_level_compliance_handoff(
    isolated_database, monkeypatch
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(monkeypatch, two_line_invoice(), two_line_packing())

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    first = item_by_reference(result.items, "invoice_line_1")
    second = item_by_reference(result.items, "invoice_line_2")

    assert first.shipment_input is not None
    assert first.shipment_input.pct_code == "61091000"
    assert second.shipment_input is not None
    assert second.shipment_input.pct_code == "52094200"
    for item in (first, second):
        assert item.compliance is not None
        assert item.compliance.supported_product is True
        assert item.shipment_input.uploaded_document_types is not None
        assert (
            "commercial_invoice" in item.shipment_input.uploaded_document_types
        )
        assert "packing_list" in item.shipment_input.uploaded_document_types
    # Each item is scored as its own single-line invoice.
    assert first.shipment_input.invoice_total == Decimal("550.00")


# --------------------------------------------------------------------------- #
#    Weight-total reconciliation against declared document totals.
# --------------------------------------------------------------------------- #
def test_declared_weight_totals_are_reconciled(
    isolated_database, monkeypatch
) -> None:
    invoice = two_line_invoice(declared_net="135", declared_gross="145")
    packing = two_line_packing(declared_net="135", declared_gross="145")
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(monkeypatch, invoice, packing)

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    net_total = next(
        check
        for check in result.shipment_level_checks
        if check.check_id == "invoice_net_weight_total"
    )
    packing_gross = next(
        check
        for check in result.shipment_level_checks
        if check.check_id == "packing_gross_weight_total"
    )

    assert net_total.status == ComplianceCheckStatus.PASSED
    assert packing_gross.status == ComplianceCheckStatus.PASSED


def test_missing_declared_weight_total_is_not_applicable(
    isolated_database, monkeypatch
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(monkeypatch, two_line_invoice(), two_line_packing())

    result = run_phase_2c(
        isolated_database, phase_2c_request(invoice_id, packing_id)
    )
    net_total = next(
        check
        for check in result.shipment_level_checks
        if check.check_id == "invoice_net_weight_total"
    )

    assert net_total.status == ComplianceCheckStatus.NOT_APPLICABLE


# --------------------------------------------------------------------------- #
#    Overall shipment aggregation rules (item 8), tested directly.
# --------------------------------------------------------------------------- #
def _item_result(status: ComplianceCheckStatus) -> ShipmentItemResult:
    return ShipmentItemResult(
        item_reference="ref",
        invoice_item_index=1,
        packing_item_index=1,
        invoice_line_number=1,
        packing_line_number=1,
        product_name="X",
        pct_code="61091000",
        match_status=ItemMatchStatus.MATCHED,
        match_strategy=ItemMatchStrategy.LINE_REFERENCE,
        match_note="ok",
        item_checks=[],
        shipment_input=None,
        compliance=None,
        status=status,
        fields_requiring_manual_review=[],
    )


def test_overall_status_any_failure_fails() -> None:
    items = [
        _item_result(ComplianceCheckStatus.PASSED),
        _item_result(ComplianceCheckStatus.FAILED),
        _item_result(ComplianceCheckStatus.MANUAL_REVIEW),
    ]
    assert _overall_shipment_status(items, []) == ComplianceCheckStatus.FAILED


def test_overall_status_manual_review_when_no_failure() -> None:
    items = [
        _item_result(ComplianceCheckStatus.PASSED),
        _item_result(ComplianceCheckStatus.MANUAL_REVIEW),
    ]
    assert (
        _overall_shipment_status(items, [])
        == ComplianceCheckStatus.MANUAL_REVIEW
    )


def test_overall_status_passes_when_all_items_pass() -> None:
    items = [
        _item_result(ComplianceCheckStatus.PASSED),
        _item_result(ComplianceCheckStatus.PASSED),
    ]
    assert _overall_shipment_status(items, []) == ComplianceCheckStatus.PASSED


# --------------------------------------------------------------------------- #
#    HTTP endpoint smoke test.
# --------------------------------------------------------------------------- #
def test_multi_line_http_endpoint(isolated_database, monkeypatch) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(monkeypatch, two_line_invoice(), two_line_packing())

    async def override_database():
        with Session(isolated_database) as db:
            yield db

    app.dependency_overrides[get_db_session] = override_database

    async def post_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post(
                "/api/v1/compliance/check-documents/multi-line",
                json={
                    "commercial_invoice_document_id": str(invoice_id),
                    "packing_list_document_id": str(packing_id),
                    "shipment_date": "2026-07-20",
                    "additional_uploaded_document_types": [
                        "form_e",
                        "certificate_of_origin",
                    ],
                },
            )

    response = asyncio.run(post_request())
    body = response.json()

    assert response.status_code == 200
    assert len(body["items"]) == 2
    assert body["items"][0]["compliance"]["supported_product"] is True


def test_multi_line_requires_packing_list_document(isolated_database) -> None:
    invoice_id, _ = two_matching_documents(isolated_database)

    async def override_database():
        with Session(isolated_database) as db:
            yield db

    app.dependency_overrides[get_db_session] = override_database

    async def post_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post(
                "/api/v1/compliance/check-documents/multi-line",
                json={
                    "commercial_invoice_document_id": str(invoice_id),
                    "packing_list_document_id": None,
                },
            )

    response = asyncio.run(post_request())

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Only an invoice and a packing list: the exporter's normal starting point.
# --------------------------------------------------------------------------- #
def test_invoice_and_packing_list_alone_split_the_verdict(
    isolated_database, monkeypatch
) -> None:
    """Two sound uploads, no supporting paperwork claimed at all.

    The shipment is still not submittable, and ``overall_status`` says so. But
    nothing is wrong with the two files, so the document review passes and the
    missing customs documents are reported as a checklist instead.
    """
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(monkeypatch, two_line_invoice(), two_line_packing())

    result = run_phase_2c(
        isolated_database,
        MultiLineShipmentRequest(
            commercial_invoice_document_id=invoice_id,
            packing_list_document_id=packing_id,
            shipment_date=date(2026, 7, 20),
            additional_uploaded_document_types=[],
        ),
    )

    assert result.overall_status == ComplianceCheckStatus.FAILED
    assert result.document_review_status == ComplianceCheckStatus.PASSED
    outstanding = {
        document.document_type for document in result.outstanding_documents
    }
    assert "form_e" in outstanding
    # Every checklist entry carries the rule text that demanded it.
    assert all(document.reasons for document in result.outstanding_documents)


def test_a_defect_in_the_uploaded_documents_fails_the_document_review(
    isolated_database, monkeypatch
) -> None:
    """The split must never hide a real problem in the uploaded files."""
    invoice_id, packing_id = two_matching_documents(isolated_database)
    configure_fake_llm(
        monkeypatch, two_line_invoice(invoice_total="1200.00"), two_line_packing()
    )

    result = run_phase_2c(
        isolated_database,
        MultiLineShipmentRequest(
            commercial_invoice_document_id=invoice_id,
            packing_list_document_id=packing_id,
            shipment_date=date(2026, 7, 20),
            additional_uploaded_document_types=[],
        ),
    )

    assert result.document_review_status == ComplianceCheckStatus.FAILED
