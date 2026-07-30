import json
import logging
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.exceptions import (
    StructuredExtractionProviderError,
    StructuredExtractionUnavailableError,
)
from app.core.config import Settings
from app.models.documents import DocumentUploadRecord
from app.schemas.extraction import ExtractedLineItem, ExtractedShipment
from app.schemas.multi_line_extraction import MultiLineInvoiceCandidates
from app.services import structured_extraction_service


def add_document(
    engine: Engine,
    *,
    status: str = "extracted",
    extracted_text: str | None = "Invoice INV-1001 total USD 250",
) -> UUID:
    document_id = uuid4()
    with Session(engine) as db:
        db.add(
            DocumentUploadRecord(
                id=document_id,
                original_filename="invoice.pdf",
                stored_filename=f"{document_id}.pdf",
                file_extension=".pdf",
                mime_type="application/pdf",
                size_bytes=100,
                status=status,
                extracted_text=extracted_text,
            )
        )
        db.commit()
    return document_id


def _candidate(value=None) -> dict:
    present = value is not None
    return {
        "value": value,
        "source_page": 1 if present else None,
        "confidence": "0.99" if present else "0",
        "validation_status": "verified" if present else "manual_review",
        "validation_note": "Printed on page 1." if present else "Not printed.",
    }


def _invoice_candidates(*, exporter_name="Demo Exporter") -> MultiLineInvoiceCandidates:
    return MultiLineInvoiceCandidates.model_validate(
        {
            "exporter_name": _candidate(exporter_name),
            "buyer_name": _candidate("Demo Buyer"),
            "invoice_number": _candidate("INV-1001"),
            "invoice_date": _candidate("2026-07-20"),
            "currency": _candidate("USD"),
            "destination_country": _candidate("China"),
            "invoice_total": _candidate("550.00"),
            "declared_net_weight_total": _candidate("75.00"),
            "declared_gross_weight_total": _candidate("80.00"),
            "line_items": [
                {
                    "line_number": _candidate(1),
                    "product_name": _candidate("Cotton knitted T-shirts"),
                    "pct_code": _candidate("6109.1000"),
                    "quantity": _candidate("100"),
                    "unit": _candidate("PCS"),
                    "unit_price": _candidate("5.50"),
                    "line_total": _candidate("550.00"),
                    "net_weight": _candidate("75.00"),
                    "gross_weight": _candidate("80.00"),
                }
            ],
        }
    )


class _SchemaRejectedError(Exception):
    status_code = 400
    body = {
        "error": {
            "type": "invalid_request_error",
            "message": (
                "invalid JSON schema for response_format: pattern uses "
                "unsupported regex features [pattern_unsupported_feature]"
            ),
        }
    }


def _extract_multiline(client) -> MultiLineInvoiceCandidates:
    return structured_extraction_service.extract_structured_model_from_text(
        extracted_text="Invoice text",
        response_model=MultiLineInvoiceCandidates,
        schema_name="phase_2c_commercial_invoice",
        system_prompt="Extract only printed values.",
        user_prompt="Extract this invoice.",
        client=client,
    )


def test_schema_accepts_missing_fields_and_rejects_negative_values() -> None:
    first = ExtractedShipment()
    second = ExtractedShipment()

    first.items.append(ExtractedLineItem(description="Cotton shirts"))

    assert second.items == []
    with pytest.raises(ValidationError):
        ExtractedLineItem(quantity=-1)


def test_groq_request_uses_strict_schema_and_validates_response() -> None:
    captured: dict = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=ExtractedShipment(
                                exporter_name="Demo Exporter",
                                invoice_number="INV-1001",
                                currency="USD",
                                declared_total=250,
                            ).model_dump_json()
                        )
                    )
                ]
            )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()),
    )

    shipment = structured_extraction_service.extract_shipment_from_text(
        "Invoice text",
        client=client,  # type: ignore[arg-type]
    )

    response_format = captured["response_format"]
    schema = response_format["json_schema"]["schema"]
    assert response_format["json_schema"]["strict"] is True
    assert captured["max_completion_tokens"] == 2000
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert shipment.invoice_number == "INV-1001"
    assert shipment.declared_total == 250


def test_structured_completion_ceiling_allows_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROQ_STRUCTURED_MAX_COMPLETION_TOKENS", "1500")
    settings = Settings()
    assert settings.groq_structured_max_completion_tokens == 1500


@pytest.mark.parametrize("configured", ["0", "255", "8193", "not-a-number"])
def test_invalid_structured_completion_ceiling_fails_configuration_safely(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    monkeypatch.setenv("GROQ_STRUCTURED_MAX_COMPLETION_TOKENS", configured)
    with pytest.raises(ValidationError):
        Settings()


def test_multiline_strict_structured_output_success() -> None:
    expected = _invoice_candidates()
    captured: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=expected.model_dump_json())
                    )
                ]
            )

    result = _extract_multiline(
        SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    )

    assert result == expected
    assert len(captured) == 1
    assert captured[0]["response_format"]["type"] == "json_schema"
    strict_schema = captured[0]["response_format"]["json_schema"]["schema"]
    # The transport schema must stay strict...
    assert captured[0]["response_format"]["json_schema"]["strict"] is True
    assert strict_schema["additionalProperties"] is False
    assert strict_schema["properties"]["line_items"]["type"] == "array"
    # ...but must not carry regex the provider's constrained decoder cannot
    # compile. Forwarding the lookahead Pydantic emits for Decimal made Groq
    # reject the schema outright and silently downgrade to unconstrained
    # decoding, which is how malformed line-item arrays reached us (DEF-001).
    serialized = json.dumps(strict_schema)
    assert "(?!" not in serialized
    assert "(?=" not in serialized


def test_unsupported_groq_schema_uses_safe_json_mode_fallback() -> None:
    expected = _invoice_candidates()
    captured: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            captured.append(kwargs)
            if len(captured) == 1:
                raise _SchemaRejectedError()
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=expected.model_dump_json())
                    )
                ]
            )

    result = _extract_multiline(
        SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    )

    assert result == expected
    assert [call["response_format"]["type"] for call in captured] == [
        "json_schema",
        "json_object",
    ]
    fallback_prompt = captured[1]["messages"][1]["content"]
    assert "JSON transport example" in fallback_prompt
    assert "$defs" not in fallback_prompt
    assert "pattern_unsupported_feature" not in fallback_prompt


def test_malformed_json_mode_fallback_is_rejected() -> None:
    class Completions:
        calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise _SchemaRejectedError()
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="{not-json"))
                ]
            )

    with pytest.raises(
        StructuredExtractionProviderError,
        match="malformed JSON",
    ):
        _extract_multiline(
            SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        )


def test_json_mode_fallback_pydantic_failure_is_rejected() -> None:
    class Completions:
        calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise _SchemaRejectedError()
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"unexpected": true}')
                    )
                ]
            )

    with pytest.raises(
        StructuredExtractionProviderError,
        match="Pydantic validation",
    ):
        _extract_multiline(
            SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        )


def test_missing_source_field_remains_null_after_fallback() -> None:
    expected = _invoice_candidates(exporter_name=None)

    class Completions:
        calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise _SchemaRejectedError()
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=expected.model_dump_json())
                    )
                ]
            )

    result = _extract_multiline(
        SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    )

    assert result.exporter_name.value is None
    assert result.exporter_name.validation_status == "manual_review"


def test_api_key_is_redacted_from_provider_logs(caplog) -> None:
    leaked_key = "gsk_TEST_SECRET_123456"

    class ProviderError(Exception):
        status_code = 401
        body = {
            "error": {
                "type": "authentication_error",
                "message": f"invalid key {leaked_key}",
            }
        }

    class Completions:
        def create(self, **_kwargs):
            raise ProviderError()

    caplog.set_level(logging.ERROR)
    with pytest.raises(StructuredExtractionProviderError) as captured:
        _extract_multiline(
            SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        )

    combined = caplog.text + str(captured.value)
    assert leaked_key not in combined
    assert "[REDACTED]" in combined


def test_structured_extraction_persists_validated_json(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    document_id = add_document(isolated_database)
    result = ExtractedShipment(
        exporter_name="Demo Exporter",
        exporter_ntn="1234567-8",
        invoice_number="INV-1001",
        invoice_date="2026-07-20",
        currency="USD",
        declared_total=250,
        total_net_weight_kg=10,
        total_gross_weight_kg=12,
        items=[
            ExtractedLineItem(
                description="Cotton shirts",
                pct_code="6105.1000",
                quantity=5,
                unit="PCS",
                unit_price=50,
                total_value=250,
            )
        ],
    )

    def fake_extract(text: str) -> ExtractedShipment:
        assert "INV-1001" in text
        with Session(isolated_database) as inspection_session:
            document = inspection_session.get(DocumentUploadRecord, document_id)
            assert document is not None
            assert document.structured_extraction_status == "extracting"
        return result

    monkeypatch.setattr(
        structured_extraction_service,
        "extract_shipment_from_text",
        fake_extract,
    )

    with Session(isolated_database) as db:
        response = structured_extraction_service.structure_extracted_document(
            db,
            document_id,
        )

    with Session(isolated_database) as db:
        document = db.get(DocumentUploadRecord, document_id)
        assert document is not None
        assert document.structured_extraction_status == "extracted"
        assert document.structured_extraction_error is None
        assert document.structured_data == result.model_dump(mode="json")
        assert document.structured_data["items"][0]["pct_code"] == "6105.1000"
        assert document.structured_extracted_at is not None
        assert response == result


def test_provider_failure_is_persisted(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    document_id = add_document(isolated_database)

    def fail_extraction(_text: str) -> ExtractedShipment:
        raise StructuredExtractionProviderError("Groq request failed.")

    monkeypatch.setattr(
        structured_extraction_service,
        "extract_shipment_from_text",
        fail_extraction,
    )

    with Session(isolated_database) as db:
        with pytest.raises(StructuredExtractionProviderError):
            structured_extraction_service.structure_extracted_document(
                db,
                document_id,
            )

    with Session(isolated_database) as db:
        document = db.get(DocumentUploadRecord, document_id)
        assert document is not None
        assert document.structured_extraction_status == "failed"
        assert document.structured_data is None
        assert document.structured_extraction_error is not None
        assert "Groq request failed" in document.structured_extraction_error


def test_document_must_have_extracted_text(isolated_database: Engine) -> None:
    document_id = add_document(
        isolated_database,
        status="uploaded",
        extracted_text=None,
    )

    with Session(isolated_database) as db:
        with pytest.raises(StructuredExtractionUnavailableError):
            structured_extraction_service.structure_extracted_document(
                db,
                document_id,
            )
