"""Shared low-level Groq call helpers for the customs_chat pipeline.

One place for client construction and error classification so the scope
classifier, query rewriter, answer generator and output verifier do not each
reimplement it. Reuses the same classified exception types as
``structured_extraction_service`` (see ``core/exceptions.py``) so any
existing caller-side handling of Groq failures keeps working unchanged.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from groq import Groq

from app.core.config import get_settings
from app.core.exceptions import (
    StructuredExtractionAuthError,
    StructuredExtractionConfigurationError,
    StructuredExtractionProviderError,
    StructuredExtractionProviderUnavailableError,
    StructuredExtractionRateLimitedError,
)

logger = logging.getLogger(__name__)


def get_groq_client() -> Groq:
    api_key = get_settings().groq_api_key
    if api_key is None or not api_key.get_secret_value().strip():
        raise StructuredExtractionConfigurationError("GROQ_API_KEY is not configured.")
    return Groq(api_key=api_key.get_secret_value(), max_retries=0)


def _classify_error(exc: Exception) -> Exception:
    status = getattr(exc, "status_code", None)
    if status == 429:
        return StructuredExtractionRateLimitedError(
            f"Groq rate-limited the request: {exc}"
        )
    if status in (401, 403):
        return StructuredExtractionAuthError(f"Groq rejected credentials: {exc}")
    return StructuredExtractionProviderUnavailableError(f"Groq request failed: {exc}")


def call_json(
    client: Groq,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_completion_tokens: int,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """One JSON-object-mode Groq call, used by the scope classifier, query
    rewriter and output verifier. Raises a classified ``StructuredExtraction*``
    exception on any provider or parsing failure - callers are expected to
    catch it and fall back to a deterministic default rather than surface a
    raw error to the user."""
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as exc:  # groq.APIError and subclasses
        raise _classify_error(exc) from exc
    content = response.choices[0].message.content
    if not content:
        raise StructuredExtractionProviderError(
            "Groq returned an empty response.", code="empty_response"
        )
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise StructuredExtractionProviderError(
            "Groq returned malformed JSON.", code="malformed_json"
        ) from exc
    if not isinstance(parsed, dict):
        raise StructuredExtractionProviderError(
            "Groq JSON response was not an object.", code="malformed_json"
        )
    return parsed


def call_text(
    client: Groq,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_completion_tokens: int,
    temperature: float,
) -> str:
    """One non-streaming text completion, used by the output verifier and by
    ``generation.answerer`` when streaming is not requested."""
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as exc:
        raise _classify_error(exc) from exc
    content = response.choices[0].message.content
    if not content:
        raise StructuredExtractionProviderError(
            "Groq returned an empty response.", code="empty_response"
        )
    return content


def stream_text(
    client: Groq,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_completion_tokens: int,
    temperature: float,
) -> Iterator[str]:
    """Yield text deltas as they arrive. The caller is responsible for
    accumulating them; nothing here buffers the full answer, so a slow
    consumer does not delay the model's own generation."""
    try:
        stream = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
            stream=True,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
    except Exception as exc:
        raise _classify_error(exc) from exc
