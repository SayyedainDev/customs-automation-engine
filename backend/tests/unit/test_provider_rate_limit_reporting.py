"""A rate limit and an outage must not give the user the same advice.

Live defect: a New Review with two supporting documents returned HTTP 503 and
the console said "temporarily unavailable or has reached its free-tier limit …
try again later". The backend log showed the truth - Groq returned HTTP 429
``rate_limit_exceeded`` on *tokens per minute*, stating "Please try again in
26.67s". The daily quota was fine; the per-minute token budget was not.

Telling a user their free tier is exhausted when a half-minute wait would have
worked is worse than saying nothing, so these tests pin the distinction: 429
carries its retry timing through to the caller, 401/403 is reported as
configuration rather than capacity, and neither leaks provider text.

Everything here uses a fake client. No real Groq call is made.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import (
    StructuredExtractionAuthError,
    StructuredExtractionProviderError,
    StructuredExtractionProviderUnavailableError,
    StructuredExtractionRateLimitedError,
)
from app.main import app
from app.services import structured_extraction_service as service
from app.schemas.extraction import ExtractedShipment

#: The exact provider message observed in the deployed Railway logs, with the
#: organization identifier removed.
REAL_TPM_MESSAGE = (
    "Rate limit reached for model `openai/gpt-oss-20b` in organization "
    "`org_redacted` service tier `on_demand` on tokens per minute (TPM): "
    "Limit 8000, Used 6170, Requested 5386. Please try again in 26.67s. "
    "Need more tokens? Upgrade to Dev Tier today at "
    "https://console.groq.com/settings/billing"
)

REAL_RPM_MESSAGE = (
    "Rate limit reached for model `openai/gpt-oss-20b` in organization "
    "`org_redacted` service tier `on_demand` on requests per minute (RPM): "
    "Limit 30, Used 30, Requested 1. Please try again in 2s."
)

REAL_RPD_MESSAGE = (
    "Rate limit reached for model `openai/gpt-oss-20b` in organization "
    "`org_redacted` service tier `on_demand` on requests per day (RPD): "
    "Limit 1000, Used 1000, Requested 1. Please try again in 3600s."
)


class _RaisingCompletions:
    def __init__(self, exc: Exception):
        self._exc = exc
        self.calls = 0

    def create(self, **_: object) -> object:
        self.calls += 1
        raise self._exc


def _client(exc: Exception) -> object:
    return SimpleNamespace(chat=SimpleNamespace(completions=_RaisingCompletions(exc)))


def _provider_exception(
    status_code: int,
    message: str,
    code: str,
    *,
    headers: dict[str, str] | None = None,
) -> Exception:
    exc = RuntimeError(message)
    exc.status_code = status_code  # type: ignore[attr-defined]
    exc.body = {"error": {"message": message, "code": code, "type": code}}  # type: ignore[attr-defined]
    if headers is not None:
        exc.response = SimpleNamespace(headers=headers)  # type: ignore[attr-defined]
    return exc


def _extract(exc: Exception):
    return service.extract_shipment_from_text("INVOICE", client=_client(exc))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
def test_tpm_rate_limit_carries_its_retry_delay() -> None:
    with pytest.raises(StructuredExtractionRateLimitedError) as caught:
        _extract(_provider_exception(429, REAL_TPM_MESSAGE, "rate_limit_exceeded"))
    error = caught.value
    assert error.retry_after_seconds == 26.67
    assert error.limit_kind == "tokens per minute (TPM)"
    assert error.code == "provider_rate_limited"


def test_rpm_rate_limit_is_distinguished_from_tpm() -> None:
    with pytest.raises(StructuredExtractionRateLimitedError) as caught:
        _extract(_provider_exception(429, REAL_RPM_MESSAGE, "rate_limit_exceeded"))
    assert caught.value.limit_kind == "requests per minute (RPM)"
    assert caught.value.retry_after_seconds == 2.0


def test_daily_quota_is_reported_without_inventing_a_short_wait() -> None:
    """A 3600s wait is real but useless as advice, so it is not passed on."""
    with pytest.raises(StructuredExtractionRateLimitedError) as caught:
        _extract(_provider_exception(429, REAL_RPD_MESSAGE, "rate_limit_exceeded"))
    assert caught.value.limit_kind == "requests per day (RPD)"
    # Beyond the sane ceiling, so no retry time is claimed at all.
    assert caught.value.retry_after_seconds is None


def test_retry_after_header_is_preferred_over_the_message() -> None:
    exc = _provider_exception(
        429, REAL_TPM_MESSAGE, "rate_limit_exceeded", headers={"retry-after": "9"}
    )
    with pytest.raises(StructuredExtractionRateLimitedError) as caught:
        _extract(exc)
    assert caught.value.retry_after_seconds == 9.0


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_failure_is_not_reported_as_capacity(status_code: int) -> None:
    with pytest.raises(StructuredExtractionAuthError) as caught:
        _extract(
            _provider_exception(status_code, "Invalid API Key", "invalid_api_key")
        )
    assert caught.value.code == "provider_auth_failed"
    # Still an "unavailable" subclass, so existing handlers keep working.
    assert isinstance(caught.value, StructuredExtractionProviderUnavailableError)


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_provider_outage_remains_a_plain_unavailability(status_code: int) -> None:
    with pytest.raises(StructuredExtractionProviderUnavailableError) as caught:
        _extract(_provider_exception(status_code, "upstream error", "server_error"))
    assert not isinstance(caught.value, StructuredExtractionRateLimitedError)
    assert not isinstance(caught.value, StructuredExtractionAuthError)


def test_transport_failure_without_a_status_is_still_unavailable() -> None:
    exc = RuntimeError("connection reset")
    with pytest.raises(StructuredExtractionProviderUnavailableError) as caught:
        _extract(exc)
    assert not isinstance(caught.value, StructuredExtractionRateLimitedError)


def test_malformed_output_is_not_confused_with_a_rate_limit() -> None:
    """A served-but-unusable response stays a 502-class defect."""

    class _Completions:
        def create(self, **_: object) -> object:
            message = SimpleNamespace(content="{not json", parsed=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    with pytest.raises(StructuredExtractionProviderError) as caught:
        service.extract_shipment_from_text("INVOICE", client=client)  # type: ignore[arg-type]
    assert not isinstance(caught.value, StructuredExtractionProviderUnavailableError)


def test_rate_limit_is_raised_without_retrying_the_provider() -> None:
    """No hidden retry loop: one user action must not multiply into more calls."""
    completions = _RaisingCompletions(
        _provider_exception(429, REAL_TPM_MESSAGE, "rate_limit_exceeded")
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    with pytest.raises(StructuredExtractionRateLimitedError):
        service.extract_shipment_from_text("INVOICE", client=client)  # type: ignore[arg-type]
    assert completions.calls == 1


# --------------------------------------------------------------------------- #
# What the browser is told
# --------------------------------------------------------------------------- #
def _post_multi_line(monkeypatch: pytest.MonkeyPatch, exc: Exception):
    """Drive the real route with extraction replaced by a raising stub."""
    from app.api.routes import multi_line_shipment as route

    def _boom(*_: object, **__: object) -> None:
        raise exc

    monkeypatch.setattr(
        route, "extract_match_and_check_multi_line_shipment", _boom
    )
    client = TestClient(app, raise_server_exceptions=False)
    return client.post(
        "/api/v1/compliance/check-documents/multi-line",
        json={
            "commercial_invoice_document_id": "11111111-1111-1111-1111-111111111111",
            "packing_list_document_id": "22222222-2222-2222-2222-222222222222",
            "additional_uploaded_document_types": [],
            "supporting_documents": [],
        },
    )


def test_route_returns_429_with_retry_after_for_a_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _post_multi_line(
        monkeypatch,
        StructuredExtractionRateLimitedError(
            "rate limited", retry_after_seconds=26.67, limit_kind="tokens per minute (TPM)"
        ),
    )
    assert response.status_code == 429
    # Rounded up to whole seconds so we never advise retrying early.
    assert response.headers["retry-after"] == "27"
    detail = response.json()["detail"]
    assert "rate limited" in detail
    assert "saved" in detail and "retried" in detail


def test_route_returns_502_for_bad_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _post_multi_line(
        monkeypatch, StructuredExtractionAuthError("bad key")
    )
    assert response.status_code == 502
    assert "credentials" in response.json()["detail"]
    assert "retry" not in response.json()["detail"].casefold()


def test_route_still_returns_503_for_a_real_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _post_multi_line(
        monkeypatch, StructuredExtractionProviderUnavailableError("upstream down")
    )
    assert response.status_code == 503


@pytest.mark.parametrize(
    "exc",
    [
        StructuredExtractionRateLimitedError(
            f"rate limited {REAL_TPM_MESSAGE}", retry_after_seconds=26.67
        ),
        StructuredExtractionAuthError("Invalid API Key sk-abcdefghijklmnop"),
    ],
)
def test_no_provider_text_or_account_detail_reaches_the_browser(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    body = _post_multi_line(monkeypatch, exc).text
    for leak in ("org_", "Limit 8000", "Used 6170", "Requested 5386",
                 "console.groq.com", "sk-", "Upgrade to Dev Tier"):
        assert leak not in body, f"provider detail leaked to the browser: {leak}"
