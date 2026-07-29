from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
import threading
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import Base
from app.core.exceptions import StructuredExtractionProviderUnavailableError
from app.models.documents import DocumentUploadRecord
from app.schemas.multi_line_extraction import (
    InvoiceLineItemCandidate,
    MultiLineInvoiceCandidates,
    MultiLinePackingListCandidates,
)
from app.schemas.shipment_extraction import CandidateField, FieldValidationStatus
from app.schemas.supporting_documents import (
    SupportingDocumentCandidates,
    SupportingDocumentRef,
)
from app.services import (
    multi_line_shipment_service,
    structured_extraction_service,
    supporting_document_service,
)
from app.services.extraction import staged_multi_line
from app.services.extraction import cache_fingerprint
from app.services.extraction.staged_multi_line import (
    InvoiceHeaderCandidates,
    LineDiscovery,
)
from tests.unit.test_multi_line_shipment import (
    phase_2c_request,
    run_phase_2c,
    two_line_invoice,
    two_line_packing,
    two_matching_documents,
)
from tests.unit.test_shipment_extraction import add_extracted_document


def _configured_counting_extractor(
    monkeypatch: Any,
) -> dict[type[BaseModel], int]:
    calls: dict[type[BaseModel], int] = {}
    invoice = two_line_invoice()
    packing = two_line_packing()

    def fake_structured_output(**kwargs: Any) -> BaseModel:
        response_model = kwargs["response_model"]
        calls[response_model] = calls.get(response_model, 0) + 1
        if response_model is MultiLineInvoiceCandidates:
            return invoice
        if response_model is MultiLinePackingListCandidates:
            return packing
        raise AssertionError(f"Unexpected structured-output model {response_model}.")

    monkeypatch.setattr(
        multi_line_shipment_service,
        "extract_structured_model_from_text",
        fake_structured_output,
    )
    return calls


def test_shared_groq_client_disables_sdk_retries(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    def fake_groq(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(get_settings(), "groq_api_key", SecretStr("test-key"))
    monkeypatch.setattr(structured_extraction_service, "Groq", fake_groq)

    structured_extraction_service._get_groq_client()

    assert captured["max_retries"] == 0


def test_runtime_cache_capability_uses_import_time_app_code_version(
    monkeypatch: Any,
) -> None:
    startup_version = cache_fingerprint.APPLICATION_CODE_VERSION

    def file_read_at_health_time_is_a_bug(_path: Path) -> bytes:
        raise AssertionError("Health checks must not reread edited source files.")

    monkeypatch.setattr(Path, "read_bytes", file_read_at_health_time_is_a_bug)

    capability = cache_fingerprint.runtime_extraction_cache_capability()

    assert capability["application_code_version"] == startup_version
    assert len(startup_version) == 64
    int(startup_version, 16)


def test_same_phase_2c_document_ids_reuse_stored_model_extractions(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    calls = _configured_counting_extractor(monkeypatch)
    request = phase_2c_request(invoice_id, packing_id)

    first = run_phase_2c(isolated_database, request)
    second = run_phase_2c(isolated_database, request)

    assert second.invoice == first.invoice
    assert second.packing_list == first.packing_list
    assert calls == {
        MultiLineInvoiceCandidates: 1,
        MultiLinePackingListCandidates: 1,
    }


def test_invoice_prompt_change_invalidates_only_the_invoice_extraction(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    calls = _configured_counting_extractor(monkeypatch)
    request = phase_2c_request(invoice_id, packing_id)

    run_phase_2c(isolated_database, request)
    monkeypatch.setattr(
        multi_line_shipment_service,
        "MULTI_LINE_INVOICE_SYSTEM_PROMPT",
        multi_line_shipment_service.MULTI_LINE_INVOICE_SYSTEM_PROMPT
        + "\nPrompt-version regression test.",
    )
    run_phase_2c(isolated_database, request)

    assert calls == {
        MultiLineInvoiceCandidates: 2,
        MultiLinePackingListCandidates: 1,
    }


def test_invoice_user_prompt_template_change_invalidates_only_invoice(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    calls = _configured_counting_extractor(monkeypatch)
    request = phase_2c_request(invoice_id, packing_id)

    run_phase_2c(isolated_database, request)
    monkeypatch.setattr(
        multi_line_shipment_service,
        "MULTI_LINE_INVOICE_USER_PROMPT_TEMPLATE",
        multi_line_shipment_service.MULTI_LINE_INVOICE_USER_PROMPT_TEMPLATE
        + "\nUser-template regression test.",
    )
    run_phase_2c(isolated_database, request)

    assert calls == {
        MultiLineInvoiceCandidates: 2,
        MultiLinePackingListCandidates: 1,
    }


def test_schema_name_change_invalidates_only_invoice(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    calls = _configured_counting_extractor(monkeypatch)
    request = phase_2c_request(invoice_id, packing_id)

    run_phase_2c(isolated_database, request)
    monkeypatch.setattr(
        multi_line_shipment_service,
        "MULTI_LINE_INVOICE_SCHEMA_NAME",
        "phase_2c_commercial_invoice_changed",
    )
    run_phase_2c(isolated_database, request)

    assert calls == {
        MultiLineInvoiceCandidates: 2,
        MultiLinePackingListCandidates: 1,
    }


def test_staged_prompt_change_invalidates_both_phase_2c_profiles(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    calls = _configured_counting_extractor(monkeypatch)
    request = phase_2c_request(invoice_id, packing_id)

    run_phase_2c(isolated_database, request)
    monkeypatch.setattr(
        staged_multi_line,
        "LINE_SYSTEM_PROMPT",
        staged_multi_line.LINE_SYSTEM_PROMPT + "\nStaged-prompt regression test.",
    )
    run_phase_2c(isolated_database, request)

    assert calls == {
        MultiLineInvoiceCandidates: 2,
        MultiLinePackingListCandidates: 2,
    }


def test_structured_output_fallback_transport_change_invalidates_cache(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    calls = _configured_counting_extractor(monkeypatch)
    request = phase_2c_request(invoice_id, packing_id)

    run_phase_2c(isolated_database, request)
    monkeypatch.setattr(
        structured_extraction_service,
        "JSON_OBJECT_FALLBACK_INSTRUCTIONS",
        structured_extraction_service.JSON_OBJECT_FALLBACK_INSTRUCTIONS
        + "\nFallback-transport regression test.",
    )
    run_phase_2c(isolated_database, request)

    assert calls == {
        MultiLineInvoiceCandidates: 2,
        MultiLinePackingListCandidates: 2,
    }


def test_invoice_schema_change_invalidates_only_the_invoice_extraction(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    calls = _configured_counting_extractor(monkeypatch)
    request = phase_2c_request(invoice_id, packing_id)

    run_phase_2c(isolated_database, request)
    original = structured_extraction_service._groq_strict_schema

    def changed_schema(
        response_model: type[BaseModel] = MultiLineInvoiceCandidates,
    ) -> dict[str, Any]:
        schema = original(response_model)
        if response_model is MultiLineInvoiceCandidates:
            schema["description"] = "Schema-version regression test."
        return schema

    monkeypatch.setattr(
        structured_extraction_service,
        "_groq_strict_schema",
        changed_schema,
    )
    run_phase_2c(isolated_database, request)

    assert calls == {
        MultiLineInvoiceCandidates: 2,
        MultiLinePackingListCandidates: 1,
    }


def test_model_change_never_reuses_a_phase_2c_extraction(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    calls = _configured_counting_extractor(monkeypatch)
    request = phase_2c_request(invoice_id, packing_id)

    run_phase_2c(isolated_database, request)
    monkeypatch.setattr(get_settings(), "groq_model", "different/model-version")
    run_phase_2c(isolated_database, request)

    assert calls == {
        MultiLineInvoiceCandidates: 2,
        MultiLinePackingListCandidates: 2,
    }


@pytest.mark.parametrize(
    ("setting_name", "changed_value"),
    [
        ("ocr_executable", "different-tesseract"),
        ("ocr_language", "eng+urd"),
        ("ocr_dpi", 400),
        ("ocr_page_segmentation_mode", 3),
        ("ocr_timeout_seconds", 90),
        ("ocr_min_confidence", Decimal("0.85")),
    ],
)
def test_ocr_setting_change_never_reuses_a_phase_2c_extraction(
    isolated_database: Engine,
    monkeypatch: Any,
    setting_name: str,
    changed_value: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    calls = _configured_counting_extractor(monkeypatch)
    request = phase_2c_request(invoice_id, packing_id)

    run_phase_2c(isolated_database, request)
    monkeypatch.setattr(get_settings(), setting_name, changed_value)
    run_phase_2c(isolated_database, request)

    assert calls == {
        MultiLineInvoiceCandidates: 2,
        MultiLinePackingListCandidates: 2,
    }


def test_changed_page_marked_text_never_reuses_phase_2c_candidates(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    calls = _configured_counting_extractor(monkeypatch)
    request = phase_2c_request(invoice_id, packing_id)

    run_phase_2c(isolated_database, request)
    changed_text = "Commercial invoice INV-CHANGED with two product lines."
    with Session(isolated_database) as db:
        invoice = db.get(DocumentUploadRecord, invoice_id)
        assert invoice is not None
        invoice.extracted_text = changed_text
        invoice.extracted_pages = [{"page_number": 1, "text": changed_text}]
        db.commit()

    run_phase_2c(isolated_database, request)

    assert calls == {
        MultiLineInvoiceCandidates: 2,
        MultiLinePackingListCandidates: 1,
    }


def test_global_status_from_another_profile_does_not_hide_valid_cache(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    calls = _configured_counting_extractor(monkeypatch)
    request = phase_2c_request(invoice_id, packing_id)

    run_phase_2c(isolated_database, request)
    with Session(isolated_database) as db:
        for document_id in (invoice_id, packing_id):
            document = db.get(DocumentUploadRecord, document_id)
            assert document is not None
            document.structured_extraction_status = "failed"
        db.commit()

    run_phase_2c(isolated_database, request)

    assert calls == {
        MultiLineInvoiceCandidates: 1,
        MultiLinePackingListCandidates: 1,
    }


def test_invoice_and_packing_profiles_do_not_overwrite_each_other(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    shared_id = add_extracted_document(
        isolated_database,
        original_filename="shared.pdf",
        text="Commercial invoice and packing list with two product lines.",
    )
    calls = _configured_counting_extractor(monkeypatch)

    with Session(isolated_database) as db:
        multi_line_shipment_service._extract_invoice(db, shared_id)
        multi_line_shipment_service._extract_packing_list(db, shared_id)
        multi_line_shipment_service._extract_invoice(db, shared_id)
        multi_line_shipment_service._extract_packing_list(db, shared_id)

    assert calls == {
        MultiLineInvoiceCandidates: 1,
        MultiLinePackingListCandidates: 1,
    }


def test_provider_unavailable_does_not_enter_direct_staged_fallback(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    request = phase_2c_request(invoice_id, packing_id)
    calls = 0

    def unavailable(**_kwargs: Any) -> BaseModel:
        nonlocal calls
        calls += 1
        raise StructuredExtractionProviderUnavailableError("TPD exhausted")

    def staged_must_not_run(_marked_text: str) -> MultiLineInvoiceCandidates:
        raise AssertionError("A provider outage must not enter staged extraction.")

    monkeypatch.setattr(
        multi_line_shipment_service,
        "extract_structured_model_from_text",
        unavailable,
    )
    monkeypatch.setattr(
        multi_line_shipment_service,
        "extract_invoice_staged",
        staged_must_not_run,
    )

    with pytest.raises(StructuredExtractionProviderUnavailableError):
        run_phase_2c(isolated_database, request)

    assert calls == 1
    with Session(isolated_database) as db:
        invoice = db.get(DocumentUploadRecord, invoice_id)
        assert invoice is not None
        assert not isinstance(invoice.structured_data, dict) or not any(
            key.startswith("phase_2c_commercial_invoice")
            for key in invoice.structured_data
        )


def test_provider_unavailable_mid_staged_extraction_is_not_a_placeholder(
    monkeypatch: Any,
) -> None:
    invoice = two_line_invoice()
    header = InvoiceHeaderCandidates.model_validate(
        {
            name: getattr(invoice, name).model_dump(mode="json")
            for name in InvoiceHeaderCandidates.model_fields
        }
    )

    def staged_provider(**kwargs: Any) -> BaseModel:
        response_model = kwargs["response_model"]
        if response_model is InvoiceHeaderCandidates:
            return header
        if response_model is LineDiscovery:
            return LineDiscovery.model_validate(
                {
                    "line_count": 1,
                    "lines": [
                        {
                            "line_number": 1,
                            "source_page": 1,
                            "product_name_hint": "Cotton T-shirts",
                        }
                    ],
                }
            )
        if response_model is InvoiceLineItemCandidate:
            raise StructuredExtractionProviderUnavailableError("TPD exhausted")
        raise AssertionError(f"Unexpected staged model {response_model}.")

    monkeypatch.setattr(
        staged_multi_line,
        "extract_structured_model_from_text",
        staged_provider,
    )

    with pytest.raises(StructuredExtractionProviderUnavailableError):
        staged_multi_line.extract_invoice_staged(
            '<page number="1">commercial invoice</page>'
        )


def _candidate(value: Any = None) -> CandidateField[Any]:
    present = value is not None
    return CandidateField[Any](
        value=value,
        source_page=1 if present else None,
        confidence=Decimal("0.99") if present else Decimal("0"),
        validation_status=(
            FieldValidationStatus.VERIFIED
            if present
            else FieldValidationStatus.MANUAL_REVIEW
        ),
        validation_note="Printed." if present else "Not printed.",
    )


def _supporting_candidates() -> SupportingDocumentCandidates:
    printed = {
        "detected_document_type": "Certificate of Origin",
        "document_number": "COO-1001",
        "exporter_or_applicant": "Demo Textile Exporter",
        "destination_country": "China",
        "issuing_authority": "Lahore Chamber of Commerce and Industry",
    }
    return SupportingDocumentCandidates.model_validate(
        {
            name: _candidate(printed.get(name)).model_dump()
            for name in SupportingDocumentCandidates.model_fields
        }
    )


def test_same_supporting_document_id_reuses_stored_model_extraction(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    document_id = add_extracted_document(
        isolated_database,
        original_filename="certificate_of_origin.pdf",
        text=(
            "Certificate of Origin COO-1001 issued by Lahore Chamber of "
            "Commerce and Industry for Demo Textile Exporter to China."
        ),
    )
    calls = 0
    candidates = _supporting_candidates()

    def fake_structured_output(**kwargs: Any) -> SupportingDocumentCandidates:
        nonlocal calls
        calls += 1
        assert kwargs["response_model"] is SupportingDocumentCandidates
        return candidates

    monkeypatch.setattr(
        supporting_document_service,
        "extract_structured_model_from_text",
        fake_structured_output,
    )

    with Session(isolated_database) as db:
        first, _ = supporting_document_service.extract_supporting_document(
            db, document_id
        )
        second, _ = supporting_document_service.extract_supporting_document(
            db, document_id
        )

    assert second == first
    # Direct calls without a claimed document type retain the legacy profile;
    # the validated result is still cached after its single provider call.
    assert calls == 1


def test_supporting_cache_is_profile_and_document_text_scoped(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    document_id = add_extracted_document(
        isolated_database,
        original_filename="certificate_of_origin.pdf",
        text=(
            "Certificate of Origin COO-1001 issued by Lahore Chamber of "
            "Commerce and Industry for Demo Textile Exporter to China."
        ),
    )
    calls = 0
    candidates = _supporting_candidates()

    def fake_structured_output(**_kwargs: Any) -> SupportingDocumentCandidates:
        nonlocal calls
        calls += 1
        return candidates

    monkeypatch.setattr(
        supporting_document_service,
        "extract_structured_model_from_text",
        fake_structured_output,
    )

    with Session(isolated_database) as db:
        supporting_document_service.extract_supporting_document(db, document_id)

        # A document-wide status written by another extraction profile must not
        # hide this profile's independently successful cache entry.
        document = db.get(DocumentUploadRecord, document_id)
        assert document is not None
        document.structured_extraction_status = "failed"
        db.commit()
        supporting_document_service.extract_supporting_document(db, document_id)
        assert calls == 1

        monkeypatch.setattr(
            supporting_document_service,
            "SUPPORTING_USER_PROMPT_TEMPLATE",
            supporting_document_service.SUPPORTING_USER_PROMPT_TEMPLATE
            + "\nSupporting user-template regression test.",
        )
        supporting_document_service.extract_supporting_document(db, document_id)
        assert calls == 2

        monkeypatch.setattr(
            supporting_document_service,
            "SUPPORTING_SCHEMA_NAME",
            "supporting_document_changed",
        )
        supporting_document_service.extract_supporting_document(db, document_id)
        assert calls == 3

        monkeypatch.setattr(get_settings(), "groq_model", "different/supporting-model")
        supporting_document_service.extract_supporting_document(db, document_id)
        assert calls == 4

        monkeypatch.setattr(get_settings(), "ocr_dpi", 450)
        supporting_document_service.extract_supporting_document(db, document_id)
        assert calls == 5

        changed_text = "Certificate of Origin COO-CHANGED for another shipment."
        document = db.get(DocumentUploadRecord, document_id)
        assert document is not None
        document.extracted_text = changed_text
        document.extracted_pages = [{"page_number": 1, "text": changed_text}]
        db.commit()
        supporting_document_service.extract_supporting_document(db, document_id)
        assert calls == 6


def test_concurrent_supporting_requests_spend_provider_quota_once(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'concurrent-cache.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    document_id = add_extracted_document(
        engine,
        original_filename="certificate_of_origin.pdf",
        text="Certificate of Origin COO-1001 for Demo Textile Exporter to China.",
    )
    candidates = _supporting_candidates()
    provider_entered = threading.Event()
    release_provider = threading.Event()
    duplicate_provider_entry = threading.Event()
    second_request_started = threading.Event()
    calls_lock = threading.Lock()
    calls = 0

    def blocking_provider(**_kwargs: Any) -> SupportingDocumentCandidates:
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            provider_entered.set()
            assert release_provider.wait(timeout=5)
        else:
            duplicate_provider_entry.set()
        return candidates

    monkeypatch.setattr(
        supporting_document_service,
        "extract_structured_model_from_text",
        blocking_provider,
    )

    def run_request() -> Any:
        with Session(engine) as db:
            return supporting_document_service.extract_supporting_document(
                db, document_id
            )

    def run_second_request() -> Any:
        second_request_started.set()
        return run_request()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(run_request)
            assert provider_entered.wait(timeout=5)
            second = executor.submit(run_second_request)
            assert second_request_started.wait(timeout=5)

            assert not duplicate_provider_entry.wait(timeout=0.25)
            release_provider.set()
            first_result = first.result(timeout=5)
            second_result = second.result(timeout=5)
    finally:
        release_provider.set()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()

    assert calls == 1
    assert second_result[0] == first_result[0]


def test_concurrent_cross_profile_writes_preserve_both_cache_slots(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'cross-profile-cache.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    document_id = add_extracted_document(
        engine,
        original_filename="shared-profile-document.pdf",
        text=(
            "Certificate of Origin and commercial invoice fields for a "
            "cross-profile cache serialization test."
        ),
    )
    supporting_provider_entered = threading.Event()
    release_supporting_provider = threading.Event()
    invoice_request_started = threading.Event()
    invoice_provider_entered = threading.Event()

    def blocking_supporting_provider(
        **_kwargs: Any,
    ) -> SupportingDocumentCandidates:
        supporting_provider_entered.set()
        assert release_supporting_provider.wait(timeout=5)
        return _supporting_candidates()

    def invoice_provider(**kwargs: Any) -> BaseModel:
        assert kwargs["response_model"] is MultiLineInvoiceCandidates
        invoice_provider_entered.set()
        return two_line_invoice()

    monkeypatch.setattr(
        supporting_document_service,
        "extract_structured_model_from_text",
        blocking_supporting_provider,
    )
    monkeypatch.setattr(
        multi_line_shipment_service,
        "extract_structured_model_from_text",
        invoice_provider,
    )

    def run_supporting() -> Any:
        with Session(engine) as db:
            return supporting_document_service.extract_supporting_document(
                db, document_id
            )

    def run_invoice() -> Any:
        invoice_request_started.set()
        with Session(engine) as db:
            return multi_line_shipment_service._extract_invoice(db, document_id)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            supporting = executor.submit(run_supporting)
            assert supporting_provider_entered.wait(timeout=5)
            invoice = executor.submit(run_invoice)
            assert invoice_request_started.wait(timeout=5)

            assert not invoice_provider_entered.wait(timeout=0.25)
            release_supporting_provider.set()
            supporting.result(timeout=5)
            invoice.result(timeout=5)

        with Session(engine) as db:
            document = db.get(DocumentUploadRecord, document_id)
            assert document is not None
            assert isinstance(document.structured_data, dict)
            assert "supporting_document" in document.structured_data
            assert "phase_2c_commercial_invoice" in document.structured_data
    finally:
        release_supporting_provider.set()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_corrupt_stored_candidates_fail_closed_and_are_reextracted(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    invoice_id, packing_id = two_matching_documents(isolated_database)
    calls = _configured_counting_extractor(monkeypatch)
    request = phase_2c_request(invoice_id, packing_id)
    run_phase_2c(isolated_database, request)

    with Session(isolated_database) as db:
        document = db.get(DocumentUploadRecord, invoice_id)
        assert document is not None
        structured_data = dict(document.structured_data or {})
        phase_2c = dict(structured_data["phase_2c_commercial_invoice"])
        phase_2c["candidates"] = {"not": "the invoice schema"}
        structured_data["phase_2c_commercial_invoice"] = phase_2c
        document.structured_data = structured_data
        db.commit()

    run_phase_2c(isolated_database, request)

    assert calls == {
        MultiLineInvoiceCandidates: 2,
        MultiLinePackingListCandidates: 1,
    }


def test_provider_failure_is_never_reused_as_a_supporting_extraction(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    document_id = add_extracted_document(
        isolated_database,
        original_filename="certificate_of_origin.pdf",
        text="Certificate of Origin COO-1001 for Demo Textile Exporter to China.",
    )
    calls = 0

    def fail_then_succeed(**_kwargs: Any) -> SupportingDocumentCandidates:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise StructuredExtractionProviderUnavailableError("TPD exhausted")
        return _supporting_candidates()

    monkeypatch.setattr(
        supporting_document_service,
        "extract_structured_model_from_text",
        fail_then_succeed,
    )

    with Session(isolated_database) as db:
        with pytest.raises(StructuredExtractionProviderUnavailableError):
            supporting_document_service.extract_supporting_document(db, document_id)
        extraction, _ = supporting_document_service.extract_supporting_document(
            db, document_id
        )

    assert extraction.document_number.value == "COO-1001"
    assert calls == 2


def test_cached_supporting_extraction_is_rechecked_against_each_shipment(
    isolated_database: Engine,
    monkeypatch: Any,
) -> None:
    document_id = add_extracted_document(
        isolated_database,
        original_filename="certificate_of_origin.pdf",
        text=(
            "CERTIFICATE OF ORIGIN\n"
            "Certificate Number: COO-1001\n"
            "Exporter: Demo Textile Exporter\n"
            "Destination Country: China\n"
            "Issuing Authority: Lahore Chamber of Commerce and Industry\n"
        ),
    )
    calls = 0

    def fake_structured_output(**_kwargs: Any) -> SupportingDocumentCandidates:
        nonlocal calls
        calls += 1
        return _supporting_candidates()

    monkeypatch.setattr(
        supporting_document_service,
        "extract_structured_model_from_text",
        fake_structured_output,
    )
    reference = SupportingDocumentRef(
        document_type="certificate_of_origin",
        document_id=document_id,
    )

    with Session(isolated_database) as db:
        matching = supporting_document_service.verify_supporting_documents(
            db,
            supporting_documents=[reference],
            claimed_only_types=[],
            shipment_exporter="Demo Textile Exporter",
            shipment_buyer=None,
            shipment_invoice_number=None,
            shipment_destination="China",
            shipment_pct_code=None,
            shipment_product=None,
        )
        conflicting = supporting_document_service.verify_supporting_documents(
            db,
            supporting_documents=[reference],
            claimed_only_types=[],
            shipment_exporter="Demo Textile Exporter",
            shipment_buyer=None,
            shipment_invoice_number=None,
            shipment_destination="United States",
            shipment_pct_code=None,
            shipment_product=None,
        )

    assert matching[0].content_status == "passed"
    assert conflicting[0].content_status == "failed"
    assert calls == 0
