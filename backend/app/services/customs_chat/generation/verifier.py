"""The optional LLM groundedness check - docs section 10.4, second check.

Runs only for GROUNDED-mode answers and only when
``CUSTOMS_CHAT_ENABLE_VERIFIER`` is set. The deterministic checks in
``guards/output_guard.py`` are the hard, always-on gate; this catches
subtler unsupported claims that pattern-matching cannot - a paraphrase that
overstates what a passage actually says, for instance - at the cost of one
extra fast-model call.
"""

from __future__ import annotations

from dataclasses import dataclass

from groq import Groq

from app.core.config import get_settings
from app.services.customs_chat import groq_support
from app.services.customs_chat.generation.prompts import (
    OUTPUT_VERIFIER_SYSTEM_PROMPT_V1,
    build_output_verifier_user_prompt,
)
from app.services.customs_chat.telemetry import log_stage


@dataclass(frozen=True)
class VerifierResult:
    ran: bool
    supported: bool
    grounded_ratio: float | None
    unsupported_claims: tuple[str, ...] = ()


def verify_groundedness(
    *,
    answer: str,
    evidence_text: str,
    client: Groq | None = None,
    enabled: bool = True,
) -> VerifierResult:
    if not enabled or not evidence_text.strip():
        return VerifierResult(ran=False, supported=True, grounded_ratio=None)

    try:
        settings = get_settings()
        groq_client = client or groq_support.get_groq_client()
        payload = groq_support.call_json(
            groq_client,
            model=settings.customs_chat_fast_model,
            system_prompt=OUTPUT_VERIFIER_SYSTEM_PROMPT_V1,
            user_prompt=build_output_verifier_user_prompt(sources=evidence_text, answer=answer),
            max_completion_tokens=200,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 - an unavailable verifier must not block a clean answer
        # An unavailable verifier is not treated as a failed verification -
        # the deterministic output_guard checks already ran and are the hard
        # gate; this is a second opinion, and "no second opinion available"
        # must not block an otherwise-clean answer.
        log_stage("output_verifier", error=str(exc)[:200])
        return VerifierResult(ran=False, supported=True, grounded_ratio=None)

    supported = bool(payload.get("supported", True))
    ratio = payload.get("grounded_ratio")
    ratio = float(ratio) if isinstance(ratio, (int, float)) else None
    claims = payload.get("unsupported_claims")
    unsupported = tuple(str(c) for c in claims) if isinstance(claims, list) else ()

    result = VerifierResult(ran=True, supported=supported, grounded_ratio=ratio, unsupported_claims=unsupported)
    log_stage("output_verifier", supported=supported, grounded_ratio=ratio)
    return result
