"""Focused tests for strict structured output + safe JSON-object fallback.

All tests use a fake Groq client; no live API call, model download or key is
used. The tests assert the safety rules: never accept unvalidated JSON, never
invent values, surface the real provider error, never log the API key, and never
let the LLM path return a compliance decision.
"""

import logging
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.exceptions import StructuredExtractionProviderError
from app.schemas.multi_line_extraction import (
    InvoiceLineItemCandidate,
    MultiLineInvoiceCandidates,
)
from app.schemas.shipment_extraction import CandidateField, FieldValidationStatus
from app.services import structured_extraction_service as svc


def cf(value):
    return CandidateField(value=value, source_page=1, confidence=Decimal("0.99"), validation_status=FieldValidationStatus.VERIFIED, validation_note="clearly printed")


def blank(note="absent"):
    return CandidateField(value=None, source_page=None, confidence=Decimal("0"), validation_status=FieldValidationStatus.MANUAL_REVIEW, validation_note=note)


def valid_invoice(pct="6109.1000"):
    return MultiLineInvoiceCandidates(
        exporter_name=cf("Demo Textiles"), buyer_name=cf("Shanghai Trading"), invoice_number=cf("INV-2001"),
        invoice_date=cf(date(2026, 7, 20)), currency=cf("USD"), destination_country=cf("China"),
        invoice_total=cf(Decimal("550.00")), declared_net_weight_total=blank(), declared_gross_weight_total=blank(),
        line_items=[InvoiceLineItemCandidate(
            line_number=cf(1), product_name=cf("Cotton knitted T-shirts"),
            pct_code=cf(pct) if pct else blank("no pct printed"),
            quantity=cf(Decimal("100")), unit=cf("PCS"), unit_price=cf(Decimal("5.50")),
            line_total=cf(Decimal("550.00")), net_weight=cf(Decimal("75")), gross_weight=cf(Decimal("80")),
        )],
    )


VALID_JSON = valid_invoice().model_dump_json()
MISSING_PCT_JSON = valid_invoice(pct=None).model_dump_json()


class FakeGroqError(Exception):
    def __init__(self, status_code, body):
        super().__init__(str(body))
        self.status_code = status_code
        self.body = body


SCHEMA_REJECT = FakeGroqError(400, {"error": {"message": "invalid JSON schema for response_format: 'phase_2c': pattern uses unsupported regex features (lookarounds/backrefs)", "type": "invalid_request_error"}})
SERVER_ERROR = FakeGroqError(500, {"error": {"message": "upstream provider error", "type": "server_error"}})


class FakeCompletions:
    def __init__(self, behaviors):
        self._behaviors = list(behaviors)
        self.response_formats: list[str] = []

    def create(self, **kwargs):
        self.response_formats.append(kwargs["response_format"]["type"])
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, BaseException):
            raise behavior
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=behavior))])


def fake_client(behaviors):
    completions = FakeCompletions(behaviors)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def extract(client, **overrides):
    kwargs = dict(extracted_text="x", response_model=MultiLineInvoiceCandidates, schema_name="phase_2c_commercial_invoice", system_prompt="Extract invoice lines as data.", user_prompt="invoice text")
    kwargs.update(overrides)
    return svc.extract_structured_model_from_text(client=client, **kwargs)


# 1. Strict structured output success (single json_schema call).
def test_strict_structured_output_success():
    client, completions = fake_client([VALID_JSON])
    result = extract(client)
    assert isinstance(result, MultiLineInvoiceCandidates)
    assert len(result.line_items) == 1
    assert completions.response_formats == ["json_schema"]


# 2. Unsupported Groq schema error is detected as a schema rejection.
def test_unsupported_schema_triggers_fallback():
    client, completions = fake_client([SCHEMA_REJECT, VALID_JSON])
    result = extract(client)
    assert isinstance(result, MultiLineInvoiceCandidates)
    # It fell back from strict json_schema to validated json_object mode.
    assert completions.response_formats == ["json_schema", "json_object"]


# 3. Safe JSON-mode fallback success (same as 2, asserting the object mode).
def test_json_mode_fallback_success():
    client, completions = fake_client([SCHEMA_REJECT, VALID_JSON])
    result = extract(client)
    assert result.line_items[0].pct_code.value == "6109.1000"
    assert completions.response_formats[-1] == "json_object"


# 4. Malformed fallback JSON is rejected (never accepted).
def test_malformed_fallback_json_rejected():
    client, _ = fake_client([SCHEMA_REJECT, '{"unexpected": "value"}'])
    # Pinned by error *code*, not by message text: the wording names the
    # provider and the schema and is expected to change, the code is the
    # contract. What matters is that unusable output raises instead of being
    # accepted or repaired.
    with pytest.raises(StructuredExtractionProviderError) as raised:
        extract(client)
    assert raised.value.code == "schema_validation_failed"


# 5. Pydantic validation failure on the strict path (no fallback, no acceptance).
def test_pydantic_validation_failure_on_strict():
    client, completions = fake_client(['{"unexpected": "value"}'])
    with pytest.raises(StructuredExtractionProviderError) as raised:
        extract(client)
    assert raised.value.code == "schema_validation_failed"
    assert completions.response_formats == ["json_schema"]  # validation error != schema rejection


# 6. Missing fields remain null (not invented).
def test_missing_fields_remain_null():
    client, _ = fake_client([MISSING_PCT_JSON])
    result = extract(client)
    assert result.line_items[0].pct_code.value is None
    assert result.line_items[0].pct_code.validation_status == FieldValidationStatus.MANUAL_REVIEW


# 7. The exact provider error is surfaced (not hidden) and the API key never logged.
def test_provider_error_surfaced_and_key_never_logged(monkeypatch, caplog):
    monkeypatch.setattr(svc, "get_settings", lambda: SimpleNamespace(groq_model="test-model", groq_api_key=SimpleNamespace(get_secret_value=lambda: "sk-TESTSECRET-DO-NOT-LOG")))
    client, _ = fake_client([SERVER_ERROR])
    with caplog.at_level(logging.WARNING, logger="app.services.structured_extraction_service"):
        with pytest.raises(StructuredExtractionProviderError) as info:
            extract(client)
    message = str(info.value)
    # Real detail is surfaced, not a generic message.
    assert "status=500" in message and "model=test-model" in message and "upstream provider error" in message
    # The API key never appears in the exception or the logs.
    assert "sk-TESTSECRET" not in message
    assert "sk-TESTSECRET" not in caplog.text


# 8. The LLM extraction path never returns a compliance decision.
def test_llm_path_returns_candidates_not_compliance():
    client, _ = fake_client([VALID_JSON])
    result = extract(client)
    assert isinstance(result, MultiLineInvoiceCandidates)
    assert not hasattr(result, "overall_status")
    assert not hasattr(result, "is_compliant")
