"""Layer 2: is this question inside CACE's customs/trade domain?

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 10.2. Two stages:

* Stage A (this module's ``deterministic_scope``) - the widened two-signal
  vocabulary from ``scope_vocab.py``, ~5ms, no model call, resolves the
  large majority of traffic.
* Stage B (``classify_scope`` falling through to the LLM) - a Groq fast-model
  classifier, called ONLY when Stage A returns ``None`` (its "I am not
  confident enough to decide alone" signal).

The fix this module makes over the old ``domain_guard.check_domain``: a
customs anchor word co-occurring with an out-of-domain intent no longer
auto-accepts. "For customs purposes, who won the FIFA World Cup 2026?"
matches the anchor "customs" AND the out-of-domain pattern for "world cup" -
today's flat allowlist admits it; here that combination is exactly what
forces escalation to Stage B instead of a silent accept.
"""

from __future__ import annotations

import re
import unicodedata

from groq import Groq

from app.core.config import get_settings
from app.services.customs_chat import groq_support
from app.services.customs_chat.contracts import ScopeIntent, ScopeVerdict
from app.services.customs_chat.generation.prompts import (
    SCOPE_CLASSIFIER_SYSTEM_PROMPT_V1,
    build_scope_classifier_user_prompt,
)
from app.services.customs_chat.guards.scope_vocab import (
    ANCHOR_STEMS,
    DOC_CONCEPT,
    OUT_OF_DOMAIN_RE,
    TARIFF_CODE,
    TRADE_CONTEXT,
)
from app.services.customs_chat.telemetry import log_stage


_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def _normalize(question: str) -> str:
    text = unicodedata.normalize("NFKC", question or "")
    text = _ZERO_WIDTH_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _stem_hits(haystack: str, vocabulary: tuple[str, ...]) -> list[str]:
    """Match multi-word terms literally, short terms exactly, others as
    prefixes. Short terms are anchored at both ends because a three-letter
    prefix match is mostly false positives ("coo" inside "cooking"); longer
    terms match as a prefix so one entry covers export/exporter/exported."""
    hits: list[str] = []
    for term in vocabulary:
        if " " in term or "-" in term or "/" in term:
            if term in haystack:
                hits.append(term)
            continue
        pattern = (
            rf"\b{re.escape(term)}\b" if len(term) <= 4 else rf"\b{re.escape(term)}"
        )
        if re.search(pattern, haystack):
            hits.append(term)
    return hits


def _guess_intent(text: str) -> ScopeIntent:
    """A light heuristic default for telemetry. Deliberately not load-bearing:
    no downstream branch in phase-1 pipeline.py depends on this being exactly
    right, because the textile-checklist tool call is deferred to a later
    phase (see docs section 16, question 6)."""
    if TARIFF_CODE.search(text):
        return "tariff_lookup"
    if re.search(r"\bwhat (is|does|are)\b|\bexplain\b|\bdefine\b", text):
        return "customs_knowledge"
    if re.search(r"\bwhat (do|documents?) (i|do i) need\b|\bam i compliant\b|\bcan i export\b", text):
        return "compliance_check"
    return "customs_knowledge"


def deterministic_scope(question: str) -> ScopeVerdict | None:
    """Stage A. Returns a decided ``ScopeVerdict`` when confident, or ``None``
    to escalate to the LLM classifier."""
    text = _normalize(question)
    if not text:
        return ScopeVerdict(
            in_scope=False,
            intent="out_of_scope",
            confidence=1.0,
            decided_by="fast",
            reason="empty question",
        )

    out_of_domain = bool(OUT_OF_DOMAIN_RE.search(text))
    anchors = _stem_hits(text, ANCHOR_STEMS)

    if out_of_domain and anchors:
        # Ambiguous on its face - could be a real customs question that
        # happens to name an unrelated topic, or a deflection attempt
        # wearing customs vocabulary. Not a call the deterministic stage
        # should make alone.
        return None

    if out_of_domain:
        return ScopeVerdict(
            in_scope=False,
            intent="out_of_scope",
            confidence=0.95,
            decided_by="fast",
            reason="matched an out-of-domain request pattern",
        )

    if anchors:
        return ScopeVerdict(
            in_scope=True,
            intent=_guess_intent(text),
            confidence=0.95,
            decided_by="fast",
            reason=f"matched domain anchor term(s): {', '.join(anchors[:4])}",
        )

    context = _stem_hits(text, TRADE_CONTEXT)
    concepts = _stem_hits(text, DOC_CONCEPT)
    if TARIFF_CODE.search(text):
        context = context + ["tariff-code"]

    if context and concepts:
        return ScopeVerdict(
            in_scope=True,
            intent=_guess_intent(text),
            confidence=0.85,
            decided_by="fast",
            reason="matched trade context and document/regulatory concept",
        )

    if context or concepts:
        # Exactly one weak signal, no anchor, no out-of-domain override.
        # Genuinely ambiguous - let the classifier decide rather than guess.
        return None

    return ScopeVerdict(
        in_scope=False,
        intent="out_of_scope",
        confidence=0.9,
        decided_by="fast",
        reason="no customs, trade or regulatory signal found",
    )


def _classify_with_llm(question: str, client: Groq) -> ScopeVerdict:
    settings = get_settings()
    payload = groq_support.call_json(
        client,
        model=settings.customs_chat_fast_model,
        system_prompt=SCOPE_CLASSIFIER_SYSTEM_PROMPT_V1,
        user_prompt=build_scope_classifier_user_prompt(question),
        max_completion_tokens=120,
        temperature=0.0,
    )
    intent = payload.get("intent")
    if intent not in (
        "customs_knowledge",
        "document_help",
        "tariff_lookup",
        "compliance_check",
        "meta",
        "out_of_scope",
    ):
        intent = "customs_knowledge" if payload.get("in_scope") else "out_of_scope"
    in_scope = bool(payload.get("in_scope")) and intent != "out_of_scope"
    confidence = payload.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = 0.6
    return ScopeVerdict(
        in_scope=in_scope,
        intent=intent,  # type: ignore[arg-type]
        confidence=float(confidence),
        decided_by="llm",
        reason=str(payload.get("reason") or "llm classification"),
    )


def classify_scope(
    question: str,
    *,
    client: Groq | None = None,
    enable_llm: bool = True,
) -> ScopeVerdict:
    """The full Layer 2 decision: Stage A, then Stage B only if Stage A could
    not decide.

    If the LLM stage is unavailable (no key, rate limited, outage) for a
    question the deterministic stage found genuinely ambiguous, the safe
    default is out-of-scope: letting an unresolved ambiguous question
    through unchecked is the greater risk, and a real customs question that
    is refused once can simply be asked again in clearer words.
    """
    decided = deterministic_scope(question)
    if decided is not None:
        log_stage("scope_router", decided_by="fast", in_scope=decided.in_scope, intent=decided.intent)
        return decided

    if not enable_llm:
        return ScopeVerdict(
            in_scope=False,
            intent="out_of_scope",
            confidence=0.5,
            decided_by="fast",
            reason="ambiguous and LLM scope classifier disabled",
        )

    try:
        groq_client = client or groq_support.get_groq_client()
        verdict = _classify_with_llm(question, groq_client)
        log_stage("scope_router", decided_by="llm", in_scope=verdict.in_scope, intent=verdict.intent)
        return verdict
    except Exception as exc:  # noqa: BLE001 - any Groq failure defaults to out-of-scope
        log_stage("scope_router", decided_by="llm", error=str(exc)[:200])
        return ScopeVerdict(
            in_scope=False,
            intent="out_of_scope",
            confidence=0.4,
            decided_by="fast",
            reason="ambiguous question and the scope classifier was unavailable",
        )
