"""Regression matrix for deterministic-first Form-E/COO extraction.

Every provider is fake. A missing GROQ_API_KEY is intentional: pytest must
never make a live request.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    StructuredExtractionAuthError,
    StructuredExtractionProviderError,
    StructuredExtractionProviderUnavailableError,
    StructuredExtractionRateLimitedError,
)
from app.models.documents import DocumentUploadRecord
from app.schemas.ocr import OcrValidationStatus
from app.schemas.supporting_documents import SupportingDocumentType
from app.schemas.supporting_documents import SupportingDocumentRef
from app.services import supporting_document_service
from app.services.extraction.document_bundle import DocumentTextBundle, StoredPage
from app.services.extraction import supporting_document_hybrid as hybrid
from tests.unit.test_shipment_extraction import add_extracted_document


FORM = SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION
COO = SupportingDocumentType.CERTIFICATE_OF_ORIGIN

FORM_STANDARD = """FORM E EXPORT DECLARATION
Form E Number: SYN-FORME-1001
Issue Date: 2026-07-20
Exporter: Synthetic Textile Exporter Ltd.
Buyer / Consignee: Synthetic Buyer Co.
Invoice Number: SYN-INV-1001
PCT Code: 6203.4200
Commodity: Men's woven cotton trousers
Destination Country: China
Bank: Synthetic Test Bank
Amount: 17400.00
Currency: USD
Shipment Reference: SYN-SHP-1001
"""

COO_STANDARD = """CERTIFICATE OF ORIGIN
Certificate Number: SYN-COO-1001
Issue Date: 2026-07-20
Exporter: Synthetic Textile Exporter Ltd.
Consignee: Synthetic Buyer Co.
Invoice Number: SYN-INV-1001
PCT Code: 6203.4200
Commodity: Men's woven cotton trousers
Country of Destination: China
Issuing Authority: Synthetic Chamber of Commerce
Quantity: 1200 PCS
Shipment Reference: SYN-SHP-1001
"""


def _bundle(
    text: str,
    *,
    method: str = "pdf_embedded_text",
    document_id: UUID | None = None,
) -> DocumentTextBundle:
    return DocumentTextBundle(
        document_id=document_id or uuid4(),
        document_type="supporting_document",
        pages=[
            StoredPage(
                page_number=1,
                text=text,
                original_embedded_text=text if method == "pdf_embedded_text" else "",
                extraction_method=method,
                ocr_confidence=Decimal("0.96") if method == "tesseract_ocr" else None,
                ocr_validation_status=(
                    OcrValidationStatus.VERIFIED
                    if method == "tesseract_ocr"
                    else None
                ),
            )
        ],
        reviews=[],
    )


@pytest.mark.parametrize(
    ("document_type", "text", "expected_number"),
    [
        (FORM, FORM_STANDARD, "SYN-FORME-1001"),
        (COO, COO_STANDARD, "SYN-COO-1001"),
    ],
)
def test_standard_documents_resolve_all_important_fields_deterministically(
    document_type: SupportingDocumentType,
    text: str,
    expected_number: str,
) -> None:
    extraction = hybrid.extract_deterministically(_bundle(text), document_type)

    assert extraction.unresolved_important_fields() == []
    assert extraction.fields["document_number"].value == expected_number
    assert all(
        extraction.fields[name].method == "regex_label"
        for name in hybrid.IMPORTANT_FIELDS[document_type]
    )


@pytest.mark.parametrize(
    ("document_type", "text"),
    [(FORM, FORM_STANDARD), (COO, COO_STANDARD)],
)
def test_standard_documents_make_zero_gapfill_calls(
    document_type: SupportingDocumentType,
    text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extraction = hybrid.extract_deterministically(_bundle(text), document_type)

    def provider_call_is_a_defect(**_kwargs: Any) -> Any:
        raise AssertionError("A complete supporting document must not call Groq.")

    monkeypatch.setattr(hybrid, "extract_structured_model_from_text", provider_call_is_a_defect)
    updates, telemetry = hybrid.gapfill(
        extraction, extraction.unresolved_important_fields()
    )

    assert updates == {}
    assert telemetry["groq_calls"] == 0


@pytest.mark.parametrize(
    ("document_type", "text", "field_name", "expected"),
    [
        (
            FORM,
            FORM_STANDARD.replace(
                "Form E Number: SYN-FORME-1001",
                "Export Declaration Number - SYN-FORME-ALT-1",
            ),
            "document_number",
            "SYN-FORME-ALT-1",
        ),
        (
            FORM,
            FORM_STANDARD.replace(
                "Invoice Number: SYN-INV-1001",
                "Invoice Reference\nSYN-INV-ALT-1",
            ),
            "invoice_reference",
            "SYN-INV-ALT-1",
        ),
        (
            COO,
            COO_STANDARD.replace(
                "Certificate Number: SYN-COO-1001",
                "Certificate Reference - SYN-COO-ALT-1",
            ),
            "document_number",
            "SYN-COO-ALT-1",
        ),
        (
            COO,
            COO_STANDARD.replace(
                "Exporter: Synthetic Textile Exporter Ltd.",
                "Consignor\nSynthetic Alternate Exporter Ltd.",
            ),
            "exporter_or_applicant",
            "Synthetic Alternate Exporter Ltd",
        ),
        (
            COO,
            COO_STANDARD.replace(
                "Country of Destination: China",
                "Final Country of Destination\nUnited States",
            ),
            "destination_country",
            "United States",
        ),
    ],
)
def test_controlled_alternate_labels_and_next_line_values(
    document_type: SupportingDocumentType,
    text: str,
    field_name: str,
    expected: str,
) -> None:
    extraction = hybrid.extract_deterministically(_bundle(text), document_type)
    assert extraction.fields[field_name].value == expected


@pytest.mark.parametrize("document_type,text", [(FORM, FORM_STANDARD), (COO, COO_STANDARD)])
def test_ocr_text_uses_ocr_regex_provenance(
    document_type: SupportingDocumentType, text: str
) -> None:
    extraction = hybrid.extract_deterministically(
        _bundle(text.replace(": ", "  " * 3), method="tesseract_ocr"),
        document_type,
    )
    assert extraction.unresolved_important_fields() == []
    assert all(
        extraction.fields[name].method == "ocr_regex"
        for name in hybrid.IMPORTANT_FIELDS[document_type]
    )


@pytest.mark.parametrize("document_type,text", [(FORM, FORM_STANDARD), (COO, COO_STANDARD)])
def test_optional_missing_fields_do_not_trigger_groq(
    document_type: SupportingDocumentType, text: str
) -> None:
    extraction = hybrid.extract_deterministically(_bundle(text), document_type)
    assert extraction.optional_fields_missing()
    assert extraction.unresolved_important_fields() == []


def _fake_gapfill(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    captured: dict[str, Any],
) -> None:
    def fake_structured(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return kwargs["response_model"].model_validate(payload)

    monkeypatch.setattr(hybrid, "extract_structured_model_from_text", fake_structured)


@pytest.mark.parametrize(
    ("document_type", "text", "field_name", "value"),
    [
        (
            FORM,
            FORM_STANDARD.replace(
                "Form E Number: SYN-FORME-1001",
                "Export Filing Identifier: SYN-FORME-GAP-1",
            ),
            "document_number",
            "SYN-FORME-GAP-1",
        ),
        (
            COO,
            COO_STANDARD.replace(
                "Certificate Number: SYN-COO-1001",
                "Origin Document Identifier: SYN-COO-GAP-1",
            ),
            "document_number",
            "SYN-COO-GAP-1",
        ),
    ],
)
def test_one_unresolved_field_uses_one_bounded_allowlisted_request(
    document_type: SupportingDocumentType,
    text: str,
    field_name: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extraction = hybrid.extract_deterministically(_bundle(text), document_type)
    unresolved = extraction.unresolved_important_fields()
    captured: dict[str, Any] = {}
    _fake_gapfill(monkeypatch, {field_name: value}, captured)

    updates, telemetry = hybrid.gapfill(extraction, unresolved)
    conflicts = hybrid.merge_gapfill(extraction, updates)

    assert unresolved == [field_name]
    assert set(captured["response_model"].model_fields) == {field_name}
    assert "SupportingDocumentCandidates" not in captured["user_prompt"]
    assert len(captured["extracted_text"]) <= (
        get_settings().supporting_gapfill_max_context_characters + 500
    )
    assert captured["max_completion_tokens"] == (
        get_settings().groq_supporting_gapfill_max_completion_tokens
    )
    assert telemetry["groq_calls"] == 1
    assert extraction.fields[field_name].method == "llm_gapfill"
    assert conflicts == []


def test_gapfill_never_overwrites_a_reliable_deterministic_value() -> None:
    extraction = hybrid.extract_deterministically(_bundle(FORM_STANDARD), FORM)
    original = extraction.fields["document_number"]
    conflicts = hybrid.merge_gapfill(
        extraction,
        {
            "document_number": hybrid.DeterministicField(
                value="SYN-FORME-DIFFERENT",
                normalized_value="SYN-FORME-DIFFERENT",
                method="llm_gapfill",
                confidence=Decimal("0.85"),
                validation_status="valid",
            )
        },
    )
    assert extraction.fields["document_number"] is original
    assert extraction.fields["document_number"].value == "SYN-FORME-1001"
    assert conflicts == ["document_number"]


def test_unknown_or_forbidden_gapfill_keys_are_rejected() -> None:
    model = hybrid._gapfill_model(["document_number"])
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "document_number": "SYN-COO-1",
                "customs_clearance": "approved",
            }
        )
    assert not {
        "passed",
        "failed",
        "manual_review",
        "authenticity",
        "customs_clearance",
    } & set(model.model_fields)


def test_dynamic_schema_has_no_ambiguous_integer_number_union() -> None:
    textual = hybrid._gapfill_model(["document_number"]).model_json_schema()
    amount = hybrid._gapfill_model(["amount"]).model_json_schema()
    textual_types = {
        option.get("type")
        for option in textual["properties"]["document_number"]["anyOf"]
    }
    amount_types = {
        option.get("type")
        for option in amount["properties"]["amount"]["anyOf"]
    }
    assert textual_types == {"string", "null"}
    assert amount_types == {"number", "null"}


@pytest.mark.parametrize(
    ("bad_value", "field_name"),
    [
        ("not-an-identifier", "document_number"),
        ("not-a-country-123", "destination_country"),
        ("99XX.4200", "pct_code"),
    ],
)
def test_invalid_gapfill_values_are_not_merged(
    bad_value: str,
    field_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = COO_STANDARD.replace(
        "Certificate Number: SYN-COO-1001",
        "Origin Document Identifier: printed-source-value",
    )
    extraction = hybrid.extract_deterministically(_bundle(text), COO)
    # Exercise the requested validator directly for optional fields too.
    spec = hybrid._field_spec(COO, field_name)
    assert spec is not None
    assert spec.normalizer(bad_value) is None


def test_prompt_context_is_bounded_and_does_not_include_unrelated_tail() -> None:
    tail = "UNRELATED_TAIL_" * 1000
    text = (
        FORM_STANDARD.replace(
            "Form E Number: SYN-FORME-1001",
            "Export Filing Identifier: SYN-FORME-GAP-1",
        )
        + tail
    )
    extraction = hybrid.extract_deterministically(_bundle(text), FORM)
    snippets = hybrid.select_snippets(
        extraction, extraction.unresolved_important_fields()
    )
    context = hybrid._context(snippets)
    assert len(context) <= get_settings().supporting_gapfill_max_context_characters + 500
    assert tail not in context


def test_environment_override_for_supporting_completion_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROQ_SUPPORTING_GAPFILL_MAX_COMPLETION_TOKENS", "384")
    assert Settings().groq_supporting_gapfill_max_completion_tokens == 384


@pytest.mark.parametrize("value", ["0", "-1", "127", "1025", "not-a-number"])
def test_invalid_supporting_completion_ceiling_fails_safely(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_SUPPORTING_GAPFILL_MAX_COMPLETION_TOKENS", value)
    with pytest.raises(ValidationError):
        Settings()


def _standard_document(
    engine: Engine, text: str, filename: str
) -> UUID:
    return add_extracted_document(
        engine,
        original_filename=filename,
        text=text,
    )


@pytest.mark.parametrize(
    ("document_type", "text", "filename"),
    [
        (FORM, FORM_STANDARD, "form_e.pdf"),
        (COO, COO_STANDARD, "certificate_of_origin.pdf"),
    ],
)
def test_service_persists_deterministic_provenance_and_cache(
    isolated_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    document_type: SupportingDocumentType,
    text: str,
    filename: str,
) -> None:
    document_id = _standard_document(isolated_database, text, filename)

    def provider_call_is_a_defect(**_kwargs: Any) -> Any:
        raise AssertionError("Standard Form-E/COO must use zero provider calls.")

    monkeypatch.setattr(hybrid, "extract_structured_model_from_text", provider_call_is_a_defect)
    with Session(isolated_database) as db:
        first, _ = supporting_document_service.extract_supporting_document(
            db, document_id, document_type
        )
        second, _ = supporting_document_service.extract_supporting_document(
            db, document_id, document_type
        )
        record = db.get(DocumentUploadRecord, document_id)
        assert record is not None
        assert isinstance(record.structured_data, dict)
        cache = record.structured_data["supporting_document"]

    assert first == second
    assert first.document_number.extraction_method.value == "regex_label"
    assert first.document_number.original_field_location is not None
    assert cache["telemetry"]["groq_calls"] == 0
    assert cache["status"] == "extracted"


@pytest.mark.parametrize(
    "provider_error",
    [
        StructuredExtractionRateLimitedError(
            "rate limited", retry_after_seconds=3.0, limit_kind="TPM"
        ),
        StructuredExtractionProviderUnavailableError("timeout"),
        StructuredExtractionAuthError("unauthorized"),
        StructuredExtractionProviderError("invalid JSON", code="malformed_json"),
        StructuredExtractionProviderError("empty response", code="empty_response"),
        StructuredExtractionProviderError(
            "schema invalid", code="schema_validation_failed"
        ),
    ],
    ids=["429", "timeout", "auth", "invalid-json", "empty", "schema-invalid"],
)
def test_provider_failure_preserves_deterministic_fields_and_retry_state(
    isolated_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    provider_error: Exception,
) -> None:
    text = FORM_STANDARD.replace(
        "Form E Number: SYN-FORME-1001",
        "Export Filing Identifier: SYN-FORME-RETRY-1",
    )
    document_id = _standard_document(isolated_database, text, "form_e.pdf")

    def fail(**_kwargs: Any) -> Any:
        raise provider_error

    monkeypatch.setattr(hybrid, "extract_structured_model_from_text", fail)
    with Session(isolated_database) as db:
        with pytest.raises(type(provider_error)):
            supporting_document_service.extract_supporting_document(
                db, document_id, FORM
            )
        record = db.get(DocumentUploadRecord, document_id)
        assert record is not None
        assert isinstance(record.structured_data, dict)
        cache = record.structured_data["supporting_document"]
        assert cache["status"] == "partial"
        assert cache["candidates"]["exporter_or_applicant"]["value"] == (
            "Synthetic Textile Exporter Ltd"
        )
        assert cache["candidates"]["document_number"]["value"] is None
        assert record.structured_extraction_status == "partial"
        assert db.query(DocumentUploadRecord).filter_by(id=document_id).count() == 1


def test_retry_requests_only_the_still_unresolved_field(
    isolated_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = FORM_STANDARD.replace(
        "Form E Number: SYN-FORME-1001",
        "Export Filing Identifier: SYN-FORME-RETRY-1",
    )
    document_id = _standard_document(isolated_database, text, "form_e.pdf")
    calls = 0
    captured: dict[str, Any] = {}

    def fail_then_succeed(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise StructuredExtractionRateLimitedError(
                "rate limited", retry_after_seconds=1.0
            )
        captured.update(kwargs)
        return kwargs["response_model"].model_validate(
            {"document_number": "SYN-FORME-RETRY-1"}
        )

    monkeypatch.setattr(hybrid, "extract_structured_model_from_text", fail_then_succeed)
    with Session(isolated_database) as db:
        with pytest.raises(StructuredExtractionRateLimitedError):
            supporting_document_service.extract_supporting_document(
                db, document_id, FORM
            )
        extraction, _ = supporting_document_service.extract_supporting_document(
            db, document_id, FORM
        )
        record = db.get(DocumentUploadRecord, document_id)
        assert record is not None

    assert set(captured["response_model"].model_fields) == {"document_number"}
    assert extraction.document_number.value == "SYN-FORME-RETRY-1"
    assert extraction.document_number.extraction_method.value == "llm_gapfill"
    assert record.structured_extraction_status == "extracted"
    assert calls == 2


def _verify_pair(
    engine: Engine,
    form_text: str,
    coo_text: str,
) -> tuple[UUID, UUID, list[SupportingDocumentRef]]:
    form_id = _standard_document(engine, form_text, "form_e.pdf")
    coo_id = _standard_document(engine, coo_text, "certificate_of_origin.pdf")
    return (
        form_id,
        coo_id,
        [
            SupportingDocumentRef(document_type=FORM.value, document_id=form_id),
            SupportingDocumentRef(document_type=COO.value, document_id=coo_id),
        ],
    )


def _run_pair(
    db: Session, references: list[SupportingDocumentRef]
) -> Any:
    return supporting_document_service.verify_supporting_documents(
        db,
        supporting_documents=references,
        claimed_only_types=[],
        shipment_exporter="Synthetic Textile Exporter Ltd",
        shipment_buyer="Synthetic Buyer Co",
        shipment_invoice_number="SYN-INV-1001",
        shipment_destination="China",
        shipment_pct_code="62034200",
        shipment_product="Men's woven cotton trousers",
        shipment_invoice_total=Decimal("17400.00"),
        shipment_currency="USD",
    )


def test_complete_form_e_and_coo_make_zero_total_groq_calls(
    isolated_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, references = _verify_pair(
        isolated_database, FORM_STANDARD, COO_STANDARD
    )

    def provider_call_is_a_defect(**_kwargs: Any) -> Any:
        raise AssertionError("The standard pair must cost zero Groq calls.")

    monkeypatch.setattr(hybrid, "extract_structured_model_from_text", provider_call_is_a_defect)
    with Session(isolated_database) as db:
        results = _run_pair(db, references)

    assert len(results) == 2
    assert all(result.extraction_summary == "Extracted deterministically" for result in results)


def test_only_incomplete_supporting_document_makes_one_call(
    isolated_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    difficult_form = FORM_STANDARD.replace(
        "Form E Number: SYN-FORME-1001",
        "Export Filing Identifier: SYN-FORME-ONE-CALL",
    )
    _, _, references = _verify_pair(
        isolated_database, difficult_form, COO_STANDARD
    )
    calls: list[set[str]] = []

    def provider(**kwargs: Any) -> Any:
        fields = set(kwargs["response_model"].model_fields)
        calls.append(fields)
        return kwargs["response_model"].model_validate(
            {"document_number": "SYN-FORME-ONE-CALL"}
        )

    monkeypatch.setattr(hybrid, "extract_structured_model_from_text", provider)
    with Session(isolated_database) as db:
        _run_pair(db, references)

    assert calls == [{"document_number"}]


def test_two_incomplete_documents_use_two_sequential_bounded_calls(
    isolated_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    difficult_form = FORM_STANDARD.replace(
        "Form E Number: SYN-FORME-1001",
        "Export Filing Identifier: SYN-FORME-TWO-CALL",
    )
    difficult_coo = COO_STANDARD.replace(
        "Certificate Number: SYN-COO-1001",
        "Origin Document Identifier: SYN-COO-TWO-CALL",
    )
    _, _, references = _verify_pair(
        isolated_database, difficult_form, difficult_coo
    )
    active = False
    calls: list[str] = []

    def provider(**kwargs: Any) -> Any:
        nonlocal active
        assert active is False, "Supporting gap-fill calls must never overlap."
        active = True
        try:
            document_type = (
                "form"
                if "form_e_or_psw_export_declaration" in kwargs["user_prompt"]
                else "coo"
            )
            calls.append(document_type)
            value = (
                "SYN-FORME-TWO-CALL"
                if document_type == "form"
                else "SYN-COO-TWO-CALL"
            )
            return kwargs["response_model"].model_validate(
                {"document_number": value}
            )
        finally:
            active = False

    monkeypatch.setattr(hybrid, "extract_structured_model_from_text", provider)
    with Session(isolated_database) as db:
        _run_pair(db, references)

    assert calls == ["form", "coo"]
