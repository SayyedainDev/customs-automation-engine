"""Regression tests for DEF-007: upstream quota errors reported as bad output.

Root cause: every ``StructuredExtractionProviderError`` was surfaced to the
caller as HTTP 502 "The language model returned malformed structured data."
A rate limit, an auth failure and a provider outage are not malformed model
output. During live evaluation this mislabelled 11 quota-exhausted runs as
extraction defects, which is both wrong for operators and actively misleading
during debugging.

The fix distinguishes *the provider could not be reached / refused to serve*
from *the provider replied with something we could not validate*.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.exceptions import (
    StructuredExtractionProviderError,
    StructuredExtractionProviderUnavailableError,
)
from app.services import structured_extraction_service as service


class _RaisingCompletions:
    def __init__(self, exc: Exception):
        self._exc = exc

    def create(self, **_: object) -> object:
        raise self._exc


def _client(exc: Exception) -> object:
    return SimpleNamespace(chat=SimpleNamespace(completions=_RaisingCompletions(exc)))


def _provider_exception(status_code: int, message: str, code: str) -> Exception:
    exc = RuntimeError(message)
    exc.status_code = status_code  # type: ignore[attr-defined]
    exc.body = {"error": {"message": message, "code": code, "type": code}}  # type: ignore[attr-defined]
    return exc


def test_unavailable_is_a_subclass_so_existing_handlers_still_catch_it() -> None:
    assert issubclass(
        StructuredExtractionProviderUnavailableError, StructuredExtractionProviderError
    )


@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (429, "rate_limit_exceeded"),
        (500, "internal_server_error"),
        (502, "bad_gateway"),
        (503, "service_unavailable"),
    ],
)
def test_upstream_failure_is_classified_as_unavailable(
    status_code: int, code: str
) -> None:
    with pytest.raises(StructuredExtractionProviderUnavailableError) as caught:
        service._groq_request(
            _client(_provider_exception(status_code, "Rate limit reached", code)),  # type: ignore[arg-type]
            model="test-model",
            system_prompt="s",
            user_prompt="u",
            response_format={"type": "json_object"},
            schema_name="unit_test",
        )
    assert str(status_code) in str(caught.value)


def test_malformed_output_is_not_classified_as_unavailable() -> None:
    """A genuinely unparseable response must stay a plain provider error."""
    with pytest.raises(StructuredExtractionProviderError) as caught:
        service._validate(  # noqa: SLF001
            service.ExtractedShipment, "this is not json"
        )
    assert not isinstance(
        caught.value, StructuredExtractionProviderUnavailableError
    )
    assert "malformed JSON" in str(caught.value)


def _transport_exception(class_name: str, message: str) -> Exception:
    """A status-less SDK exception, e.g. httpx.TimeoutException / ConnectError.

    These carry no ``status_code`` and no ``body`` at all - there was no HTTP
    response to have either. ``_safe_groq_error_detail`` falls back to
    ``type(exc).__name__`` for the error code, which is why the persisted
    error genuinely reads ``error_code=APITimeoutError``.
    """
    exc_type = type(class_name, (RuntimeError,), {})
    return exc_type(message)


@pytest.mark.parametrize(
    "class_name",
    ["APITimeoutError", "APIConnectionError"],
)
def test_transport_failure_with_no_http_status_is_classified_as_unavailable(
    class_name: str,
) -> None:
    """DEF-014 (found live): a request timeout was reported as bad model output.

    ``_groq_request`` classified upstream failures by checking
    ``isinstance(status_code, int) and (status_code == 429 or status_code >= 500)``.
    A transport-level failure - the request timed out, or the connection never
    completed - has no HTTP status at all, so ``status_code`` is ``None`` and
    ``isinstance(None, int)`` is ``False``. The check fell through to the
    generic branch, and the live evaluation runner reported
    'The language model returned malformed structured data.' (HTTP 502) for a
    request that timed out days into a token-scarce day - actively misleading,
    since nothing was ever returned to be malformed. The comment on this branch
    already named "quota, outage, transport" as the intended scope; only the
    transport case was missing from the actual condition.

    Confirmed live: the persisted document error was
    ``StructuredExtractionProviderError: ... http_status=None
    error_code=APITimeoutError model=openai/gpt-oss-20b
    schema=staged_invoice_header message=Request timed out.``, surfaced to the
    API caller as a 502 blaming the model's output.
    """
    with pytest.raises(StructuredExtractionProviderUnavailableError) as caught:
        service._groq_request(
            _client(_transport_exception(class_name, "Request timed out.")),  # type: ignore[arg-type]
            model="test-model",
            system_prompt="s",
            user_prompt="u",
            response_format={"type": "json_object"},
            schema_name="unit_test",
        )
    assert "malformed" not in str(caught.value).lower()


def test_rate_limit_message_does_not_claim_malformed_data() -> None:
    with pytest.raises(StructuredExtractionProviderError) as caught:
        service._groq_request(
            _client(_provider_exception(429, "Rate limit reached", "rate_limit_exceeded")),  # type: ignore[arg-type]
            model="test-model",
            system_prompt="s",
            user_prompt="u",
            response_format={"type": "json_object"},
            schema_name="unit_test",
        )
    assert "malformed" not in str(caught.value).lower()
