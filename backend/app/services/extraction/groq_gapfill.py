"""The single Groq network call the hybrid extractor is allowed to make.

``llm_gapfill.py`` builds the prompt/context/validation for a combined
gap-fill request but never talks to Groq itself. This module is the one
caller: it reuses ``structured_extraction_service.extract_structured_model_from_text``
for the client, the strict-schema transport, and the 429/5xx/timeout/
malformed-JSON error classification - no new transport or retry code.

Exactly one call is made per invocation, regardless of how many fields are
unresolved. On any provider error the fields are simply left unresolved:
there is no retry and no fallback to a per-field or per-row ladder, so this
module can never regress into the legacy cascade.
"""

from __future__ import annotations

import logging
from typing import Any

from groq import Groq
from pydantic import BaseModel, create_model

from app.core.exceptions import (
    StructuredExtractionProviderError,
    StructuredExtractionProviderUnavailableError,
)
from app.services.extraction import llm_gapfill
from app.services.extraction.regex_extractor import FieldResult, RegexExtraction
from app.services.extraction.telemetry import DocumentTelemetry
from app.services.structured_extraction_service import (
    extract_structured_model_from_text,
)

logger = logging.getLogger(__name__)

GAPFILL_SCHEMA_NAME = "hybrid_gapfill_fields"


def _gapfill_response_model(unresolved: list[str]) -> type[BaseModel]:
    """A flat ``{field_name: value|null}`` model built for this call only.

    Every field is optional and untyped beyond ``str | None``:
    ``llm_gapfill.validate_returned_value`` re-normalises and re-validates
    every value against the same regex/normaliser contract the regex layer
    uses, so a loose transport type costs nothing in safety.
    """
    fields: dict[str, Any] = {name: (str | None, None) for name in unresolved}
    return create_model("HybridGapfillResponse", **fields)  # type: ignore[call-overload,no-any-return]


def run_gapfill(
    extraction: RegexExtraction,
    unresolved: list[str],
    *,
    document_ref: str = "gapfill",
    client: Groq | None = None,
) -> tuple[dict[str, FieldResult], DocumentTelemetry]:
    """Resolve every unresolved field with exactly one Groq call.

    Returns the updated ``FieldResult`` entries (only for fields that changed)
    plus telemetry for the attempt. An empty ``unresolved`` list makes zero
    calls and returns immediately.
    """
    telemetry = DocumentTelemetry(document_ref=document_ref, fields_total=len(unresolved))
    if not unresolved:
        return {}, telemetry

    context, candidates = llm_gapfill.build_context(extraction, unresolved)
    user_prompt = llm_gapfill.build_user_prompt(unresolved, context, candidates)
    response_model = _gapfill_response_model(unresolved)

    telemetry.llm_calls = 1
    try:
        result = extract_structured_model_from_text(
            extracted_text=context,
            response_model=response_model,
            schema_name=GAPFILL_SCHEMA_NAME,
            system_prompt=llm_gapfill.GAPFILL_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            client=client,
        )
    except StructuredExtractionProviderUnavailableError:
        # Quota/rate-limit/transport failure: never cascade, never retry. The
        # fields stay unresolved and route to manual review downstream.
        telemetry.notes.append("gapfill provider unavailable; fields left unresolved")
        return {}, telemetry
    except StructuredExtractionProviderError as exc:
        # Malformed JSON or schema-validation failure: one attempt only, no
        # stricter retry - a same-shape retry at temperature=0 is unlikely to
        # fix genuinely malformed output and would double the call budget.
        logger.warning("Gap-fill request failed (%s); fields left unresolved.", exc)
        telemetry.notes.append(f"gapfill request failed ({getattr(exc, 'code', 'unknown')}); fields left unresolved")
        return {}, telemetry

    payload = result.model_dump()
    updated = llm_gapfill.apply_gapfill_response(extraction, unresolved, payload)
    telemetry.fields_from_llm = sum(
        1 for name in unresolved if updated[name].value is not None
    )
    telemetry.fields_missing = len(unresolved) - telemetry.fields_from_llm
    return updated, telemetry
