"""Layer 4: deterministic checks on a generated answer before it ships.

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 10.4. Runs on every generated
answer, in every mode, with no model call - the mechanical backstop behind
the system prompt's own rules. The LLM groundedness verifier
(``generation/verifier.py``) is a separate, optional, GROUNDED-mode-only
second check; ``pipeline.py`` combines both into one retry/downgrade
decision.
"""

from __future__ import annotations

import re
import unicodedata

from app.services.customs_chat.contracts import OutputVerdict, Ring
from app.services.customs_chat.guards import claims
from app.services.customs_chat.guards.input_guard import contains_injection
from app.services.customs_chat.guards.scope_vocab import OUT_OF_DOMAIN_RE

_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def _normalize_output(answer: str) -> str:
    text = unicodedata.normalize("NFKC", answer or "")
    text = _ZERO_WIDTH_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip().casefold()

#: A short, distinctive fragment from each section of the system prompt.
#: Matching any of these in an answer means the model echoed its own
#: instructions rather than answering - a prompt-leak attempt succeeded.
_PROMPT_LEAK_MARKERS = (
    "who you are",
    "who you help",
    "what you never talk about",
    "how you refuse",
    "the one rule that overrides everything",
    "plain english - this is a hard requirement",
    "things you must never say",
    "you are customs assistant, a helpful guide",
)

_BANNED_LEGALESE = re.compile(
    r"\bpursuant\b|\bthereof\b|\bhereinafter\b|\bnotwithstanding\b|\baforesaid\b"
    r"|\bherein\b|\bwhereas\b|\bthe said (goods|shipment|document)\b"
    r"|\binter alia\b|\bmutatis mutandis\b|\bab initio\b|\bprima facie\b",
    re.IGNORECASE,
)


def _prompt_leak(answer: str) -> bool:
    lowered = _normalize_output(answer)
    return any(marker in lowered for marker in _PROMPT_LEAK_MARKERS)


def _scope_drift(answer: str) -> bool:
    """A lightweight signal that generation wandered onto an out-of-domain
    subject mid-answer - not a full scope re-classification (too slow, and
    every legitimate answer legitimately uses ordinary English), just the
    same out-of-domain intent patterns Layer 2 uses on input."""
    return bool(OUT_OF_DOMAIN_RE.search(_normalize_output(answer)))


def check_output(
    answer: str,
    *,
    mode: Ring,
    evidence_text: str,
    attempt: int = 0,
) -> OutputVerdict:
    """One deterministic pass over a generated answer.

    ``attempt`` is 0 on the first generation and 1+ on a regeneration, so the
    same violation escalates from "try again" to "downgrade" rather than
    looping forever.
    """
    violations: list[str] = []

    violations.extend(claims.forbidden_claim_violations(answer))

    if mode == "GENERAL":
        if claims.general_mode_numbers(answer):
            violations.append("ungrounded number in general-mode answer")
    elif mode == "GROUNDED":
        if claims.ungrounded_numbers(answer, evidence_text):
            violations.append("number not present in the supplied evidence")

    if contains_injection(answer):
        violations.append("answer echoes an embedded instruction")

    if _prompt_leak(answer):
        violations.append("answer leaks system prompt content")

    if _scope_drift(answer):
        violations.append("answer drifted onto an out-of-domain subject")

    if _BANNED_LEGALESE.search(_normalize_output(answer)):
        violations.append("answer uses banned legalese")

    if not violations:
        return OutputVerdict(ok=True, violations=(), action="pass")

    severe = {
        "answer echoes an embedded instruction",
        "answer leaks system prompt content",
        "answer drifted onto an out-of-domain subject",
    }
    if severe & set(violations):
        return OutputVerdict(ok=False, violations=tuple(violations), action="refuse")

    if attempt == 0:
        return OutputVerdict(ok=False, violations=tuple(violations), action="regenerate")

    return OutputVerdict(ok=False, violations=tuple(violations), action="downgrade")
