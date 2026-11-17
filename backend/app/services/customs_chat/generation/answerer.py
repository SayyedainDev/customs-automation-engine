"""The answer generator: one Groq call under the hardened system prompt.

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 9.1/9.2. Deliberately the only
place that writes user-facing prose - every other stage in the pipeline
either decides (scope, evidence) or checks (guards). No template fallback:
on failure this raises, and ``pipeline.py`` is responsible for turning that
into an honest "temporarily unavailable" response, never into the old
written templates.

A note on streaming: docs section 8 treats token streaming as mandatory for
perceived latency, and ``groq_support.stream_text`` exists to support it.
This module's ``generate_answer`` is deliberately blocking instead, because
the safety-critical checks in ``guards/output_guard.py`` (in particular the
ungrounded-number check) must run on the *complete* answer before any of it
reaches the user - streaming tokens as they arrive and only then discovering
a violation means unsafe content already shipped. Doing both at once needs
the buffered-window verification design docs section 8 lists under Phase 5
("Polish"), not Phase 1. ``api/routes/customs_chat.py`` gets a fully guarded
answer from the pipeline first and paces it over SSE for a live-typing UI,
which is a real but smaller latency cost than true concurrent streaming.
"""

from __future__ import annotations

from groq import Groq

from app.core.config import get_settings
from app.services.assistant.plain_language import strip_evidence_references
from app.services.customs_chat import groq_support
from app.services.customs_chat.contracts import DraftAnswer, Evidence, Ring, UnderstoodQuery
from app.services.customs_chat.generation.prompts import (
    ANSWER_SYSTEM_PROMPT_V1,
    build_answer_user_prompt,
)
from app.services.customs_chat.knowledge.retriever import format_citation_for_prompt


def generate_answer(
    *,
    understood: UnderstoodQuery,
    evidence: Evidence,
    history: str | None,
    mode: Ring,
    client: Groq | None = None,
) -> DraftAnswer:
    settings = get_settings()
    groq_client = client or groq_support.get_groq_client()
    numbered_passages = [
        format_citation_for_prompt(citation, index + 1)
        for index, citation in enumerate(evidence.citations)
    ]
    user_prompt = build_answer_user_prompt(
        standalone_question=understood.standalone_question,
        history=history,
        numbered_passages=numbered_passages,
        evidence_band=evidence.band,
    )
    text = groq_support.call_text(
        groq_client,
        model=settings.customs_chat_strong_model,
        system_prompt=ANSWER_SYSTEM_PROMPT_V1,
        user_prompt=user_prompt,
        max_completion_tokens=settings.customs_chat_max_completion_tokens,
        temperature=settings.customs_chat_temperature,
    )
    cleaned_text = strip_evidence_references(text.strip()).strip()
    return DraftAnswer(text=cleaned_text, mode=mode, model=settings.customs_chat_strong_model)
