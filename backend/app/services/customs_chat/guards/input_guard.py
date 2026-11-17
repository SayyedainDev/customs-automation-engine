"""Layer 1: deterministic input checks, before any retrieval or model call.

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 10.1. Runs in under 5ms and
never calls Groq: length limits, Unicode normalisation (so a zero-width
space inside "ignore" cannot defeat the injection regex), and prompt-
injection detection.
"""

from __future__ import annotations

import re
import unicodedata

from app.services.customs_chat.contracts import InputVerdict
from app.services.customs_chat.guards.scope_vocab import INJECTION_RE

MAX_QUESTION_LENGTH = 2000

#: Zero-width and other invisible formatting characters, stripped before any
#: pattern match runs. A literal zero-width space inside "ignore" defeats a
#: naive regex; NFKC normalisation plus this strip closes that gap.
_ZERO_WIDTH = re.compile(
    "[​‌‍⁠﻿­]"  # ZWSP, ZWNJ, ZWJ, WJ, BOM, soft hyphen
)
_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _ZERO_WIDTH.sub("", text)
    return _WHITESPACE.sub(" ", text).strip()


def contains_injection(text: str | None) -> bool:
    """Whether text carries an instruction aimed at the assistant itself."""
    if not text:
        return False
    return bool(INJECTION_RE.search(_normalize(text)))


def check_input(question: str) -> InputVerdict:
    """Layer 1: sanitise and reject before anything downstream sees the text."""
    normalized = _normalize(question)

    if not normalized:
        return InputVerdict(ok=False, reason="empty question", sanitized_question="")

    if len(normalized) > MAX_QUESTION_LENGTH:
        return InputVerdict(
            ok=False,
            reason=f"question exceeds {MAX_QUESTION_LENGTH} characters",
            sanitized_question=normalized[:MAX_QUESTION_LENGTH],
        )

    if contains_injection(normalized):
        # Refused here, on its own terms, even before scope routing - a
        # prompt-injection attempt dressed in customs vocabulary must not
        # reach retrieval or generation just because it also mentions "SRO".
        return InputVerdict(
            ok=False,
            reason="request attempts to override assistant instructions",
            sanitized_question=normalized,
            injection_detected=True,
        )

    return InputVerdict(
        ok=True,
        reason="ok",
        sanitized_question=normalized,
        injection_detected=False,
    )
