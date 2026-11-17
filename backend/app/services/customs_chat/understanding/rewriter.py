"""Query understanding: turn a chat question into search queries.

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 7.2. Replaces the 14-regex
``_CONCEPTS`` expansion table in ``services/assistant/regulatory_chat.py``,
which existed only to compensate for that module's hard lexical evidence
gate and could not generalise beyond the phrasings someone had already
special-cased. This calls Groq's fast model once, in JSON mode, to:

* resolve a follow-up question against conversation history
  ("what about for China?" -> the standalone question it actually means);
* produce 2-3 short keyword queries in the vocabulary official documents
  use, which the retriever fuses with RRF;
* extract soft-filter entities (HS code, product, country, trade direction).

Falls back to a trivial rewrite (the question itself, unchanged, as the only
search query) whenever Groq is unavailable or disabled - retrieval still
runs, just without query expansion or history resolution.
"""

from __future__ import annotations

import re
from typing import Any

from groq import Groq

from app.core.config import get_settings
from app.services.customs_chat import groq_support
from app.services.customs_chat.contracts import UnderstoodQuery
from app.services.customs_chat.generation.prompts import (
    QUERY_REWRITER_SYSTEM_PROMPT_V1,
    build_query_rewriter_user_prompt,
)
from app.services.customs_chat.telemetry import log_stage

_ENTITY_KEYS = ("hs_code", "product", "country", "trade_direction", "document_type")

#: Words that signal a question is referring back to something earlier in
#: the conversation ("what about for China?", "is that required?"). Their
#: absence is one half of the "skip the rewriter" latency shortcut below -
#: on its own this is a weak signal (plenty of ordinary sentences use "that"
#: with no forward reference), which is exactly why it only applies to a
#: short first-turn question with no history to resolve against in the
#: first place, never to a mid-conversation one.
_REFERENTIAL_WORDS = re.compile(
    r"\b(it|that|this|they|those|these|them)\b|^\s*what about\b|^\s*and\b|^\s*also\b",
    re.IGNORECASE,
)

#: Above this length a question is more likely to bundle several asks
#: together, which is exactly what the rewriter's multi-query expansion
#: helps with - the shortcut below is for the common short, single-ask case.
_STANDALONE_MAX_LENGTH = 140


def looks_already_standalone(question: str) -> bool:
    """docs section 8's "skip the rewriter for the first turn of a
    conversation when the question is already standalone and short" latency
    shortcut. Deliberately conservative: a false "not standalone" just costs
    one extra Groq call (the rewriter still runs); a false "standalone"
    would skip rewriting a question that needed it, so both thresholds here
    lean toward calling the rewriter when in doubt.
    """
    text = (question or "").strip()
    if not text or len(text) > _STANDALONE_MAX_LENGTH:
        return False
    return not _REFERENTIAL_WORDS.search(text)


def _trivial_rewrite(question: str) -> UnderstoodQuery:
    return UnderstoodQuery(
        standalone_question=question.strip(),
        search_queries=(question.strip(),),
        entities={key: None for key in _ENTITY_KEYS},
        requires_history=False,
    )


def rewrite_query(
    question: str,
    *,
    history: str | None = None,
    client: Groq | None = None,
    enabled: bool = True,
) -> UnderstoodQuery:
    if not enabled or not question.strip():
        return _trivial_rewrite(question)

    try:
        settings = get_settings()
        groq_client = client or groq_support.get_groq_client()
        payload = groq_support.call_json(
            groq_client,
            model=settings.customs_chat_fast_model,
            system_prompt=QUERY_REWRITER_SYSTEM_PROMPT_V1,
            user_prompt=build_query_rewriter_user_prompt(question=question, history=history),
            max_completion_tokens=300,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 - any Groq failure falls back to trivial rewrite
        log_stage("query_rewriter", error=str(exc)[:200])
        return _trivial_rewrite(question)

    standalone = str(payload.get("standalone_question") or question).strip()
    raw_queries = payload.get("search_queries")
    queries = [str(q).strip() for q in raw_queries if str(q).strip()] if isinstance(raw_queries, list) else []
    if not queries:
        queries = [standalone]

    entities_field = payload.get("entities")
    raw_entities: dict[str, Any] = entities_field if isinstance(entities_field, dict) else {}
    entities: dict[str, str | None] = {}
    for key in _ENTITY_KEYS:
        value = raw_entities.get(key)
        entities[key] = str(value).strip() if isinstance(value, str) and value.strip() else None

    result = UnderstoodQuery(
        standalone_question=standalone,
        search_queries=tuple(queries[:3]),
        entities=entities,
        requires_history=bool(payload.get("requires_history")),
    )
    log_stage("query_rewriter", queries=list(result.search_queries), entities=entities)
    return result
