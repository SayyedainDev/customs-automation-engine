import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ShipmentExtractionInputError,
    StructuredExtractionProviderError,
)
from app.core.database import get_db_session
from app.main import app
from app.models.documents import DocumentUploadRecord
from app.schemas.shipment_extraction import (
    CandidateField,
    CommercialInvoiceCandidates,
    FieldValidationStatus,
    PackingListCandidates,
    ShipmentExtractionRequest,
)
from app.services import shipment_extraction_service, structured_extraction_service


def candidate(
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


def invoice_candidates(
    *,
    pct_code: str | None = "6109.1000",
    quantity: str = "100",
    net_weight: str = "75",
    gross_weight: str = "80",
) -> CommercialInvoiceCandidates:
    return CommercialInvoiceCandidates(
        exporter_name=candidate("Demo Textile Exporter"),
        buyer_name=candidate("China Textile Buyer"),
        invoice_number=candidate("INV-1001"),
        invoice_date=candidate(date(2026, 7, 20)),
        product_name=candidate("Cotton knitted T-shirts"),
        pct_code=(
            candidate(pct_code)
            if pct_code is not None
            else candidate(
                None,
                source_page=None,
                confidence="0",
                status=FieldValidationStatus.MANUAL_REVIEW,
                note="No PCT code is printed.",
            )
        ),
        quantity=candidate(Decimal(quantity)),
        unit=candidate("PCS"),
        unit_price=candidate(Decimal("5.50")),
        line_total=candidate(Decimal("550.00")),
        invoice_total=candidate(Decimal("550.00")),
        currency=candidate("USD"),
        destination_country=candidate("China"),
        net_weight=candidate(Decimal(net_weight)),
        gross_weight=candidate(Decimal(gross_weight)),
    )


def packing_candidates(
    *,
    pct_code: str | None = "6109.1000",
    quantity: str = "100",
    net_weight: str = "75",
    gross_weight: str = "80",
) -> PackingListCandidates:
    return PackingListCandidates(
        product_name=candidate("Cotton knitted T-shirts"),
        pct_code=(
            candidate(pct_code)
            if pct_code is not None
            else candidate(
                None,
                source_page=None,
                confidence="0",
                status=FieldValidationStatus.MANUAL_REVIEW,
                note="No PCT code is printed.",
            )
        ),
        quantity=candidate(Decimal(quantity)),
        unit=candidate("PCS"),
        net_weight=candidate(Decimal(net_weight)),
        gross_weight=candidate(Decimal(gross_weight)),
        package_count=candidate(10),
    )


def add_extracted_document(
    engine: Engine,
    *,
    original_filename: str,
    text: str,
) -> UUID:
    document_id = uuid4()
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
                extracted_text=text,
                extracted_pages=[
                    {
                        "page_number": 1,
                        "text": text,
                    }
                ],
                page_count=1,
                character_count=len(text),
            )
        )
        db.commit()
    return document_id


def configure_fake_llm(
    monkeypatch,
    invoice: CommercialInvoiceCandidates,
    packing: PackingListCandidates,
) -> None:
    def fake_structured_output(**kwargs):
        response_model = kwargs["response_model"]
        if response_model is CommercialInvoiceCandidates:
            return invoice
        if response_model is PackingListCandidates:
            return packing
        raise AssertionError("Unexpected structured-output model.")

    monkeypatch.setattr(
        shipment_extraction_service,
        "extract_structured_model_from_text",
        fake_structured_output,
    )


def configure_counting_fake_llm(
    monkeypatch,
    invoice: CommercialInvoiceCandidates,
    packing: PackingListCandidates,
) -> dict[type, int]:
    calls: dict[type, int] = {}

    def fake_structured_output(**kwargs):
        response_model = kwargs["response_model"]
        calls[response_model] = calls.get(response_model, 0) + 1
        if response_model is CommercialInvoiceCandidates:
            return invoice
        if response_model is PackingListCandidates:
            return packing
        raise AssertionError("Unexpected structured-output model.")

    monkeypatch.setattr(
        shipment_extraction_service,
        "extract_structured_model_from_text",
        fake_structured_output,
    )
    return calls


def phase_2a_request(
    invoice_document_id: UUID,
    packing_document_id: UUID | None,
) -> ShipmentExtractionRequest:
    return ShipmentExtractionRequest(
        commercial_invoice_document_id=invoice_document_id,
        packing_list_document_id=packing_document_id,
        shipment_date=date(2026, 7, 20),
        additional_uploaded_document_types=[
            "form_e",
            "certificate_of_origin",
        ],
    )


def run_phase_2a(
    engine: Engine,
    request: ShipmentExtractionRequest,
):
    with Session(engine) as db:
        return shipment_extraction_service.extract_validate_and_check_shipment(
            db,
            request,
        )


def test_valid_invoice_and_packing_list_extraction(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="commercial_invoice.pdf",
        text="Commercial invoice INV-1001 for cotton knitted T-shirts.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing_list.pdf",
        text="Packing list for 100 cotton knitted T-shirts in 10 packages.",
    )
    configure_fake_llm(
        monkeypatch,
        invoice_candidates(),
        packing_candidates(),
    )

    result = run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )

    assert result.status == "ready"
    assert result.invoice.invoice_number.value == "INV-1001"
    assert result.invoice.invoice_number.source_document_id == invoice_id
    assert result.invoice.invoice_number.source_page == 1
    assert result.packing_list.package_count.value == 10
    assert all(
        check.status == "passed"
        for check in result.cross_document_checks
    )
    with Session(isolated_database) as db:
        invoice_record = db.get(DocumentUploadRecord, invoice_id)
        assert invoice_record is not None
        assert invoice_record.extracted_text is not None
        assert invoice_record.structured_data is not None
        assert "phase_2a" in invoice_record.structured_data


def test_missing_pct_code_is_null_and_requires_manual_review(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice with one cotton T-shirt product line.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list with one cotton T-shirt product line.",
    )
    configure_fake_llm(
        monkeypatch,
        invoice_candidates(pct_code=None),
        packing_candidates(pct_code=None),
    )

    result = run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )

    assert result.invoice.pct_code.value is None
    assert result.invoice.pct_code.validation_status == "manual_review"
    assert result.shipment_input.pct_code is None
    assert "invoice.pct_code" in result.fields_requiring_manual_review
    assert next(
        check
        for check in result.cross_document_checks
        if check.check_id == "pct_code_match"
    ).status == "manual_review"


def test_quantity_mismatch(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice quantity is 100 pieces of cotton T-shirts.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list quantity is 90 pieces of cotton T-shirts.",
    )
    configure_fake_llm(
        monkeypatch,
        invoice_candidates(quantity="100"),
        packing_candidates(quantity="90"),
    )

    result = run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )
    check = next(
        item
        for item in result.cross_document_checks
        if item.check_id == "quantity_match"
    )

    assert result.status == "failed"
    assert check.status == "failed"
    assert result.shipment_input.quantity is None


def test_weight_mismatch(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice states net 75 kg and gross 80 kg.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list states net 70 kg and gross 78 kg.",
    )
    configure_fake_llm(
        monkeypatch,
        invoice_candidates(),
        packing_candidates(net_weight="70", gross_weight="78"),
    )

    result = run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )

    assert result.status == "failed"
    assert result.shipment_input.net_weight is None
    assert result.shipment_input.gross_weight is None
    assert next(
        item
        for item in result.cross_document_checks
        if item.check_id == "net_weight_match"
    ).status == "failed"


def test_uncertain_extracted_value_is_discarded(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice = invoice_candidates()
    invoice = invoice.model_copy(
        update={
            "buyer_name": candidate(
                "Possibly Buyer Ltd",
                confidence="0.40",
                status=FieldValidationStatus.VERIFIED,
                note="The buyer name is blurry.",
            )
        }
    )
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice with a blurry buyer-name field.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for cotton knitted T-shirts.",
    )
    configure_fake_llm(monkeypatch, invoice, packing_candidates())

    result = run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )

    assert result.invoice.buyer_name.value is None
    assert result.invoice.buyer_name.validation_status == "manual_review"
    assert "invoice.buyer_name" in result.fields_requiring_manual_review


def test_scanned_page_requires_ocr_without_calling_llm_for_that_document(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="scanned_invoice.pdf",
        text="",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts.",
    )
    calls: list[type] = []

    def fake_structured_output(**kwargs):
        calls.append(kwargs["response_model"])
        return packing_candidates()

    monkeypatch.setattr(
        shipment_extraction_service,
        "extract_structured_model_from_text",
        fake_structured_output,
    )

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
    assert result.invoice.invoice_number.value is None
    assert (
        result.invoice.invoice_number.extraction_method
        == "not_extracted_ocr_required"
    )
    assert calls == [PackingListCandidates]


def test_missing_packing_list_is_rejected(
    isolated_database: Engine,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice for cotton knitted T-shirts.",
    )

    with pytest.raises(
        ShipmentExtractionInputError,
        match="packing-list document ID is required",
    ):
        run_phase_2a(
            isolated_database,
            phase_2a_request(invoice_id, None),
        )


def test_malformed_llm_structured_output_marks_document_failed(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice for cotton knitted T-shirts.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for cotton knitted T-shirts.",
    )

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"unexpected": "provider value"}'
                        )
                    )
                ]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()),
    )
    monkeypatch.setattr(
        structured_extraction_service,
        "_get_groq_client",
        lambda: fake_client,
    )

    # Asserted by error code: the message names the provider and the failing
    # schema and changes with them, but the classification must not.
    with pytest.raises(StructuredExtractionProviderError) as raised:
        run_phase_2a(
            isolated_database,
            phase_2a_request(invoice_id, packing_id),
        )
    assert raised.value.code == "schema_validation_failed"

    with Session(isolated_database) as db:
        invoice_record = db.get(DocumentUploadRecord, invoice_id)
        assert invoice_record is not None
        assert invoice_record.structured_extraction_status == "failed"
        assert "failed local Pydantic validation" in (
            invoice_record.structured_extraction_error or ""
        )


def test_repeated_phase_2a_request_reuses_cached_candidates(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice for 100 cotton knitted T-shirts.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts.",
    )
    calls = configure_counting_fake_llm(
        monkeypatch,
        invoice_candidates(),
        packing_candidates(),
    )
    request = phase_2a_request(invoice_id, packing_id)

    first = run_phase_2a(isolated_database, request)
    second = run_phase_2a(isolated_database, request)

    assert second == first
    assert calls == {
        CommercialInvoiceCandidates: 1,
        PackingListCandidates: 1,
    }
    with Session(isolated_database) as db:
        invoice = db.get(DocumentUploadRecord, invoice_id)
        assert invoice is not None
        assert isinstance(invoice.structured_data, dict)
        cached = invoice.structured_data["phase_2a"]
        assert cached["status"] == "extracted"
        assert cached["document_type"] == "commercial_invoice"
        assert isinstance(cached["fingerprint"], dict)
        assert isinstance(cached["candidates"], dict)


def test_phase_2a_retry_reuses_invoice_after_packing_provider_failure(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice for 100 cotton knitted T-shirts.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts.",
    )
    calls = {
        CommercialInvoiceCandidates: 0,
        PackingListCandidates: 0,
    }

    def fail_packing_once(**kwargs):
        response_model = kwargs["response_model"]
        calls[response_model] += 1
        if response_model is CommercialInvoiceCandidates:
            return invoice_candidates()
        if calls[PackingListCandidates] == 1:
            raise StructuredExtractionProviderError(
                "Synthetic provider failure for quota-safe retry test."
            )
        return packing_candidates()

    monkeypatch.setattr(
        shipment_extraction_service,
        "extract_structured_model_from_text",
        fail_packing_once,
    )
    request = phase_2a_request(invoice_id, packing_id)

    with pytest.raises(StructuredExtractionProviderError):
        run_phase_2a(isolated_database, request)
    result = run_phase_2a(isolated_database, request)

    assert result.status == "ready"
    assert calls == {
        CommercialInvoiceCandidates: 1,
        PackingListCandidates: 2,
    }


def test_malformed_phase_2a_cache_is_ignored_and_reextracted(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice for 100 cotton knitted T-shirts.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts.",
    )
    calls = configure_counting_fake_llm(
        monkeypatch,
        invoice_candidates(),
        packing_candidates(),
    )
    request = phase_2a_request(invoice_id, packing_id)
    run_phase_2a(isolated_database, request)

    with Session(isolated_database) as db:
        invoice = db.get(DocumentUploadRecord, invoice_id)
        assert invoice is not None
        structured_data = dict(invoice.structured_data or {})
        cached = dict(structured_data["phase_2a"])
        cached["candidates"] = {"not": "a commercial invoice"}
        structured_data["phase_2a"] = cached
        invoice.structured_data = structured_data
        db.commit()

    result = run_phase_2a(isolated_database, request)

    assert result.invoice.invoice_number.value == "INV-1001"
    assert calls == {
        CommercialInvoiceCandidates: 2,
        PackingListCandidates: 1,
    }


def test_phase_2a_cache_write_preserves_other_structured_data_slots(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice for 100 cotton knitted T-shirts.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts.",
    )
    existing_profiles = {
        "phase_2c_commercial_invoice": {"sentinel": "phase-2c"},
        "supporting_document": {"sentinel": "supporting"},
    }
    with Session(isolated_database) as db:
        invoice = db.get(DocumentUploadRecord, invoice_id)
        assert invoice is not None
        invoice.structured_data = dict(existing_profiles)
        db.commit()
    configure_fake_llm(
        monkeypatch,
        invoice_candidates(),
        packing_candidates(),
    )

    run_phase_2a(
        isolated_database,
        phase_2a_request(invoice_id, packing_id),
    )

    with Session(isolated_database) as db:
        invoice = db.get(DocumentUploadRecord, invoice_id)
        assert invoice is not None
        assert isinstance(invoice.structured_data, dict)
        assert invoice.structured_data["phase_2c_commercial_invoice"] == (
            existing_profiles["phase_2c_commercial_invoice"]
        )
        assert invoice.structured_data["supporting_document"] == (
            existing_profiles["supporting_document"]
        )
        assert "phase_2a" in invoice.structured_data


def test_successful_handoff_to_existing_compliance_engine(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice for 100 cotton knitted T-shirts.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts.",
    )
    configure_fake_llm(
        monkeypatch,
        invoice_candidates(),
        packing_candidates(),
    )

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
    assert result.shipment_input.quantity == Decimal("100")
    assert result.shipment_input.uploaded_document_types is not None
    assert "commercial_invoice" in result.shipment_input.uploaded_document_types
    assert "packing_list" in result.shipment_input.uploaded_document_types
    assert result.compliance.supported_product is True
    assert support_check.pct_code == "61091000"


def test_phase_2a_http_endpoint_uses_existing_document_ids(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice for 100 cotton knitted T-shirts.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list for 100 cotton knitted T-shirts.",
    )
    configure_fake_llm(
        monkeypatch,
        invoice_candidates(),
        packing_candidates(),
    )

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
                "/api/v1/compliance/check-documents",
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

    assert response.status_code == 200
    assert response.json()["shipment_input"]["pct_code"] == "61091000"
    assert response.json()["compliance"]["supported_product"] is True
