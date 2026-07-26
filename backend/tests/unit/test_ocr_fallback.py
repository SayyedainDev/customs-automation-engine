from decimal import Decimal
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.models.documents import DocumentUploadRecord
from app.schemas.ocr import OcrPageResult, OcrValidationStatus
from app.schemas.shipment_extraction import (
    CommercialInvoiceCandidates,
    ExtractionMethod,
)
from app.services import document_service
from app.services.extraction import document_bundle
from app.services.extraction.ocr_extractor import OcrExtractionError
from tests.unit.test_shipment_extraction import (
    add_extracted_document,
    candidate,
    configure_fake_llm,
    invoice_candidates,
    packing_candidates,
    phase_2a_request,
    run_phase_2a,
)


def add_multi_page_document(
    engine: Engine,
    *,
    original_filename: str,
    pages: list[str],
) -> UUID:
    document_id = uuid4()
    combined_text = "\n\n".join(pages)
    with Session(engine) as db:
        db.add(
            DocumentUploadRecord(
                id=document_id,
                original_filename=original_filename,
                stored_filename=f"{document_id}.pdf",
                file_extension=".pdf",
                mime_type="application/pdf",
                size_bytes=100,
                status="extracted",
                extracted_text=combined_text,
                extracted_pages=[
                    {
                        "page_number": page_number,
                        "text": text,
                    }
                    for page_number, text in enumerate(pages, start=1)
                ],
                page_count=len(pages),
                character_count=len(combined_text),
            )
        )
        db.commit()
    return document_id


def successful_ocr_result(
    *,
    document_id: UUID,
    page_number: int,
    original_embedded_text: str,
    text: str,
    confidence: str = "0.95",
    status: OcrValidationStatus = OcrValidationStatus.VERIFIED,
) -> OcrPageResult:
    return OcrPageResult(
        document_id=document_id,
        page_number=page_number,
        original_embedded_text=original_embedded_text,
        ocr_text=text,
        ocr_confidence=Decimal(confidence),
        validation_status=status,
        validation_note="OCR test result.",
    )


def test_fully_scanned_invoice_uses_ocr(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_multi_page_document(
        isolated_database,
        original_filename="scanned_invoice.pdf",
        pages=[""],
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts in ten packages.",
    )

    def fake_ocr(**kwargs):
        return successful_ocr_result(
            document_id=kwargs["document_id"],
            page_number=kwargs["page_number"],
            original_embedded_text=kwargs["original_embedded_text"],
            text=(
                "Commercial invoice INV-1001 for 100 cotton knitted "
                "T-shirts PCT 6109.1000."
            ),
        )

    monkeypatch.setattr(document_bundle, "ocr_pdf_page", fake_ocr)
    configure_fake_llm(monkeypatch, invoice_candidates(), packing_candidates())

    result = run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )
    invoice_page = next(
        review
        for review in result.page_reviews
        if review.document_id == invoice_id
    )

    assert invoice_page.requires_ocr is True
    assert invoice_page.ocr_attempted is True
    assert invoice_page.ocr_validation_status == "verified"
    assert result.invoice.invoice_number.value == "INV-1001"
    assert (
        result.invoice.invoice_number.extraction_method
        == ExtractionMethod.TESSERACT_OCR_LLM_STRUCTURED_OUTPUT
    )
    assert result.invoice.invoice_number.ocr_confidence == Decimal("0.95")


def test_only_scanned_page_is_ocred_in_mixed_pdf(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_multi_page_document(
        isolated_database,
        original_filename="mixed_invoice.pdf",
        pages=[
            "Commercial invoice header number INV-1001 dated 2026-07-20.",
            "",
        ],
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts in ten packages.",
    )
    requested_pages: list[int] = []

    def fake_ocr(**kwargs):
        requested_pages.append(kwargs["page_number"])
        return successful_ocr_result(
            document_id=kwargs["document_id"],
            page_number=kwargs["page_number"],
            original_embedded_text=kwargs["original_embedded_text"],
            text=(
                "Product cotton knitted T-shirts PCT 6109.1000 quantity "
                "100 net 75 kg gross 80 kg."
            ),
        )

    invoice = invoice_candidates()
    invoice = invoice.model_copy(
        update={
            "product_name": candidate(
                "Cotton knitted T-shirts",
                source_page=2,
            ),
            "pct_code": candidate("6109.1000", source_page=2),
            "quantity": candidate(Decimal("100"), source_page=2),
            "net_weight": candidate(Decimal("75"), source_page=2),
            "gross_weight": candidate(Decimal("80"), source_page=2),
        }
    )
    monkeypatch.setattr(document_bundle, "ocr_pdf_page", fake_ocr)
    configure_fake_llm(monkeypatch, invoice, packing_candidates())

    result = run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )

    assert requested_pages == [2]
    assert (
        result.invoice.invoice_number.extraction_method
        == ExtractionMethod.PDF_TEXT_LLM_STRUCTURED_OUTPUT
    )
    assert (
        result.invoice.product_name.extraction_method
        == ExtractionMethod.TESSERACT_OCR_LLM_STRUCTURED_OUTPUT
    )


def test_ocr_failure_is_recorded_and_requires_manual_review(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_multi_page_document(
        isolated_database,
        original_filename="scanned_invoice.pdf",
        pages=[""],
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts.",
    )

    def fail_ocr(**_kwargs):
        raise OcrExtractionError("Tesseract could not read the page.")

    monkeypatch.setattr(document_bundle, "ocr_pdf_page", fail_ocr)
    configure_fake_llm(monkeypatch, invoice_candidates(), packing_candidates())

    result = run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )
    invoice_page = next(
        review
        for review in result.page_reviews
        if review.document_id == invoice_id
    )

    assert result.status == "manual_review"
    assert invoice_page.ocr_validation_status == "failed"
    assert result.invoice.invoice_number.value is None
    with Session(isolated_database) as db:
        document = db.get(DocumentUploadRecord, invoice_id)
        assert document is not None
        assert document.ocr_pages is not None
        assert document.ocr_pages[0]["validation_status"] == "failed"


def test_low_confidence_ocr_values_are_null(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_multi_page_document(
        isolated_database,
        original_filename="low_confidence_invoice.pdf",
        pages=[""],
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts.",
    )

    def low_confidence_ocr(**kwargs):
        return successful_ocr_result(
            document_id=kwargs["document_id"],
            page_number=kwargs["page_number"],
            original_embedded_text=kwargs["original_embedded_text"],
            text="Blurry commercial invoice INV-1001 cotton T-shirts 6109.1000.",
            confidence="0.40",
            status=OcrValidationStatus.MANUAL_REVIEW,
        )

    monkeypatch.setattr(
        document_bundle,
        "ocr_pdf_page",
        low_confidence_ocr,
    )
    configure_fake_llm(monkeypatch, invoice_candidates(), packing_candidates())

    result = run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )

    assert result.invoice.invoice_number.value is None
    assert result.invoice.invoice_number.validation_status == "manual_review"
    assert result.invoice.invoice_number.ocr_confidence == Decimal("0.40")


def test_incorrect_pct_from_ocr_is_rejected(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_multi_page_document(
        isolated_database,
        original_filename="scanned_invoice.pdf",
        pages=[""],
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts.",
    )

    def fake_ocr(**kwargs):
        return successful_ocr_result(
            document_id=kwargs["document_id"],
            page_number=kwargs["page_number"],
            original_embedded_text=kwargs["original_embedded_text"],
            text=(
                "Commercial invoice cotton T-shirts PCT 6109.I000 "
                "quantity 100."
            ),
        )

    invoice = invoice_candidates()
    invoice = invoice.model_copy(
        update={"pct_code": candidate("6109.I000")}
    )
    monkeypatch.setattr(document_bundle, "ocr_pdf_page", fake_ocr)
    configure_fake_llm(monkeypatch, invoice, packing_candidates())

    result = run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )

    assert result.invoice.pct_code.value is None
    assert result.invoice.pct_code.validation_status == "manual_review"
    assert result.shipment_input.pct_code is None


def test_successful_ocr_handoff_reaches_phase_1(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_multi_page_document(
        isolated_database,
        original_filename="scanned_invoice.pdf",
        pages=[""],
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts.",
    )

    def fake_ocr(**kwargs):
        return successful_ocr_result(
            document_id=kwargs["document_id"],
            page_number=kwargs["page_number"],
            original_embedded_text=kwargs["original_embedded_text"],
            text=(
                "Commercial invoice INV-1001 cotton knitted T-shirts "
                "PCT 6109.1000 quantity 100."
            ),
        )

    monkeypatch.setattr(document_bundle, "ocr_pdf_page", fake_ocr)
    configure_fake_llm(monkeypatch, invoice_candidates(), packing_candidates())

    result = run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )
    support_check = next(
        check
        for check in result.compliance.checks
        if check.check_id == "mvp_pct_support"
    )

    assert result.shipment_input.pct_code == "61091000"
    assert result.compliance.supported_product is True
    assert support_check.pct_code == "61091000"


def test_original_pdf_and_embedded_text_remain_unchanged(
    isolated_database: Engine,
    monkeypatch,
    tmp_path: Path,
) -> None:
    invoice_id = add_multi_page_document(
        isolated_database,
        original_filename="scanned_invoice.pdf",
        pages=["SCAN"],
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts.",
    )
    original_pdf = b"%PDF-original-scanned-document-bytes"
    stored_path = tmp_path / f"{invoice_id}.pdf"
    stored_path.write_bytes(original_pdf)
    original_checksum = hashlib.sha256(original_pdf).hexdigest()
    original_extracted_text = "SCAN"
    original_extracted_pages = [{"page_number": 1, "text": "SCAN"}]
    monkeypatch.setattr(document_service, "UPLOAD_DIRECTORY", tmp_path)

    def fake_ocr(**kwargs):
        assert kwargs["pdf_path"] == stored_path
        return successful_ocr_result(
            document_id=kwargs["document_id"],
            page_number=kwargs["page_number"],
            original_embedded_text=kwargs["original_embedded_text"],
            text=(
                "Commercial invoice INV-1001 cotton knitted T-shirts "
                "PCT 6109.1000 quantity 100."
            ),
        )

    monkeypatch.setattr(document_bundle, "ocr_pdf_page", fake_ocr)
    configure_fake_llm(monkeypatch, invoice_candidates(), packing_candidates())

    run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )

    assert hashlib.sha256(stored_path.read_bytes()).hexdigest() == original_checksum
    with Session(isolated_database) as db:
        document = db.get(DocumentUploadRecord, invoice_id)
        assert document is not None
        assert document.extracted_text == original_extracted_text
        assert document.extracted_pages == original_extracted_pages
        assert document.ocr_pages is not None
        assert document.ocr_pages[0]["ocr_text"].startswith(
            "Commercial invoice"
        )
