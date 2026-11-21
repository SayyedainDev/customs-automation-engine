"""The customs_chat orchestrator - the only entry point other code should
import from this package.

Wires the stages described in docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 4:

    input guard -> scope router -> query understanding -> retrieval
    -> evidence banding -> generation -> output guard (+ optional verifier)
    -> persistence

Every stage is independently unit-testable (see ``contracts.py``); this
module's only job is sequencing them and deciding, from each stage's
verdict, what happens next. It never renders user-facing prose itself
outside the fixed strings in ``config.py`` - an actual answer is always
either Groq's own text or one of those fixed refusal/outage messages, never
a template standing in for a real answer (see the API route docstring for
why that distinction matters).
"""

from __future__ import annotations

from collections.abc import Callable
import time
from uuid import UUID, uuid4

from groq import Groq
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    StructuredExtractionConfigurationError,
    StructuredExtractionProviderError,
)
from app.services.assistant.shipment_assistant import answer_shipment_question
from app.services.regulatory.source_kinds import is_official, source_kind_label
from app.services.customs_chat import config as chat_config
from app.services.customs_chat.contracts import (
    ChatTurnResult,
    ChecklistTool,
    Citation,
    Evidence,
    Ring,
    TariffLookupResult,
)
from app.services.customs_chat.generation import answerer, verifier
from app.services.customs_chat.generation.prompts import PROMPT_VERSION
from app.services.customs_chat.guards import input_guard, output_guard, scope_router
from app.services.customs_chat.knowledge import checklist_tool, retriever
from app.services.customs_chat.knowledge import tariff_lookup as tariff_lookup_mod
from app.services.customs_chat.memory import history
from app.services.customs_chat.telemetry import log_guard_violation, log_stage
from app.services.customs_chat.understanding import rewriter

MAX_GENERATION_ATTEMPTS = 3


def _no_evidence(evidence: Evidence) -> Evidence:
    return Evidence(
        passages=(),
        band="NONE",
        top_relevance=evidence.top_relevance,
        citations=(),
        degraded_mode=evidence.degraded_mode,
    )


def _confidence_for(mode: Ring, band: str) -> str:
    if mode == "GROUNDED":
        return "high" if band == "STRONG" else "medium"
    if mode == "GENERAL":
        return "low"
    return "low"


def _finalize(
    db: Session,
    *,
    conversation_id: UUID,
    message_id: UUID,
    question: str,
    answer: str,
    mode: Ring,
    evidence_band: str,
    scope_intent: str,
    citations: tuple[Citation, ...],
    disclaimer: str | None,
    latency_ms: float,
    guard_violations: tuple[str, ...],
    model: str | None,
    prompt_version: str | None,
    checklist_result: ChecklistTool | None = None,
    tariff_result: TariffLookupResult | None = None,
    follow_ups: tuple[str, ...] | None = None,
    stages: tuple[str, ...] = (),
) -> ChatTurnResult:
    citation_dicts = [
        {
            "document": c.document,
            "reference": c.reference,
            "source_kind": c.source_kind,
            "kind_label": c.kind_label,
            "is_official": c.is_official,
            "currency_status": c.currency_status,
            "excerpt": c.excerpt,
            "url": c.url,
        }
        for c in citations
    ]
    history.record_turn(db, conversation_id=conversation_id, role="user", content=question)
    history.record_turn(
        db,
        conversation_id=conversation_id,
        role="assistant",
        content=answer,
        message_id=message_id,
        mode=mode,
        scope_intent=scope_intent,
        evidence_band=evidence_band,
        citations=citation_dicts or None,
        latency_ms=latency_ms,
        model=model,
        prompt_version=prompt_version,
        guard_violations=list(guard_violations) or None,
    )
    resolved_follow_ups = (
        follow_ups if follow_ups is not None else chat_config.follow_ups_for(scope_intent)
    )
    return ChatTurnResult(
        conversation_id=conversation_id,
        message_id=message_id,
        answer=answer,
        mode=mode,
        evidence_band=evidence_band,  # type: ignore[arg-type]
        scope_intent=scope_intent,  # type: ignore[arg-type]
        citations=citations,
        confidence=_confidence_for(mode, evidence_band),  # type: ignore[arg-type]
        follow_ups=resolved_follow_ups,
        disclaimer=disclaimer,
        latency_ms=latency_ms,
        guard_violations=guard_violations,
        model=model,
        checklist_tool=checklist_result,
        tariff_lookup=tariff_result,
        stages=stages,
    )


def answer_customs_question(
    db: Session,
    *,
    message: str,
    conversation_id: UUID | None = None,
    shipment_id: UUID | None = None,
    client: Groq | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> ChatTurnResult:
    started = time.perf_counter()
    settings = get_settings()
    message_id = uuid4()

    def elapsed_ms() -> float:
        return round((time.perf_counter() - started) * 1000, 2)

    stages: list[str] = []

    def emit_stage(stage: str) -> None:
        if stage not in stages:
            stages.append(stage)
            if on_stage is not None:
                on_stage(stage)

    # --- Layer 1: input guard (applies before any routing) -----------------
    emit_stage("input_guard")
    if shipment_id is None and conversation_id is not None:
        from app.models.customs_chat import ChatConversation

        existing_conv = db.get(ChatConversation, conversation_id)
        if existing_conv is not None and existing_conv.shipment_id is not None:
            shipment_id = existing_conv.shipment_id

    input_verdict = input_guard.check_input(message)
    if not input_verdict.ok:
        log_stage("pipeline.input_guard", ok=False, reason=input_verdict.reason)
        conversation = history.get_or_create_conversation(db, conversation_id, shipment_id=shipment_id)
        return _finalize(
            db,
            conversation_id=conversation.id,
            message_id=message_id,
            question=message,
            answer=chat_config.OUT_OF_SCOPE_MESSAGE,
            mode="REFUSAL",
            evidence_band="not_applicable",
            scope_intent="out_of_scope",
            citations=(),
            disclaimer=None,
            latency_ms=elapsed_ms(),
            guard_violations=("injection_detected",) if input_verdict.injection_detected else (input_verdict.reason,),
            model=None,
            prompt_version=None,
            stages=tuple(stages),
        )

    question = input_verdict.sanitized_question

    # --- Shipment-scoped delegation ------------------------------------------
    # A shipment conversation is answered by services/assistant/shipment_assistant.py,
    # which retrieves over that shipment's own documents and frozen audit result,
    # while persisting the conversation and turns into chat_conversations / chat_turns
    # so GET /conversations/{id} and POST /feedback work seamlessly.
    if shipment_id is not None:
        emit_stage("shipment_context_lookup")
        conversation = history.get_or_create_conversation(
            db, conversation_id, shipment_id=shipment_id
        )
        emit_stage("shipment_evidence_retrieval")
        shipment_response = answer_shipment_question(
            db, shipment_id, question, conversation.id
        )
        citations = tuple(
            Citation(
                document=source.document_name or source.source_document or source.display_name or "Shipment document",
                reference=f"page {source.page_number}" if source.page_number else None,
                source_kind=source.source_kind,
                kind_label=source_kind_label(source.source_kind),
                is_official=is_official(source.source_kind),
                currency_status=None,
                excerpt=source.snippet or "",
                url=None,
            )
            for source in shipment_response.sources
        )
        return _finalize(
            db,
            conversation_id=conversation.id,
            message_id=shipment_response.message_id,
            question=question,
            answer=shipment_response.answer,
            mode="GROUNDED",
            evidence_band="STRONG" if citations else "NONE",
            scope_intent="document_help",
            citations=citations,
            disclaimer=None,
            latency_ms=elapsed_ms(),
            guard_violations=(),
            model=None,
            prompt_version=None,
            follow_ups=tuple(shipment_response.suggested_questions),
            stages=tuple(stages),
        )

    conversation = history.get_or_create_conversation(db, conversation_id, shipment_id=None)

    # --- Layer 2: scope router -------------------------------------------
    emit_stage("scope_router")
    scope_verdict = scope_router.classify_scope(
        question,
        client=client,
        enable_llm=settings.customs_chat_enable_llm_scope_classifier,
    )
    if not scope_verdict.in_scope:
        return _finalize(
            db,
            conversation_id=conversation.id,
            message_id=message_id,
            question=question,
            answer=chat_config.OUT_OF_SCOPE_MESSAGE,
            mode="OUT_OF_SCOPE",
            evidence_band="not_applicable",
            scope_intent="out_of_scope",
            citations=(),
            disclaimer=None,
            latency_ms=elapsed_ms(),
            guard_violations=(),
            model=None,
            prompt_version=None,
            stages=tuple(stages),
        )

    # --- Query understanding ---------------------------------------------
    emit_stage("query_rewriter")
    history_text = history.load_history_text(
        db, conversation.id, max_turns=settings.customs_chat_history_turns
    )
    # Latency shortcut (docs section 8): a short, pronoun-free first-turn
    # question has nothing for the rewriter to resolve against history and
    # rarely benefits from query expansion enough to justify the extra Groq
    # round trip. Only applies with no history at all - a mid-conversation
    # question always goes through the rewriter, since that is exactly where
    # "what about for China?" needs it.
    skip_rewrite_for_speed = history_text is None and rewriter.looks_already_standalone(question)
    understood = rewriter.rewrite_query(
        question,
        history=history_text,
        client=client,
        enabled=settings.customs_chat_enable_query_rewriter and not skip_rewrite_for_speed,
    )

    # --- Tool: deterministic textile compliance checklist -------------------
    # docs section 13.5: the deterministic compliance engine decides the
    # checklist; this only recognises the ask and asks Groq to explain the
    # fixed result. Checked before retrieval - a checklist question about a
    # supported product must never fall through to a generic RAG answer.
    checklist_call = checklist_tool.maybe_run_checklist_tool(
        db,
        standalone_question=understood.standalone_question,
        destination=understood.entities.get("country"),
        client=client,
        on_match=lambda: emit_stage("checklist_tool"),
    )
    if checklist_call is not None:
        emit_stage("checklist_tool")
        tool_result, explanation, checklist_citations = checklist_call
        fallback_lead_in = (
            f"Here is the document checklist for {tool_result.product} "
            f"(PCT {tool_result.pct_code}) shipped to {tool_result.destination}."
        )
        lead_in = explanation or fallback_lead_in
        emit_stage("output_guard")
        checklist_guard = output_guard.check_output(lead_in, mode="GENERAL", evidence_text="", attempt=0)
        if not checklist_guard.ok:
            # The checklist itself (below) is always correct regardless of
            # what the explanation says - fall back to the fixed lead-in
            # rather than discard the actually-correct tool result.
            log_guard_violation("pipeline.checklist_tool", checklist_guard.violations)
            lead_in = fallback_lead_in
        return _finalize(
            db,
            conversation_id=conversation.id,
            message_id=message_id,
            question=question,
            answer=lead_in,
            mode="GROUNDED",
            evidence_band="STRONG",
            scope_intent="compliance_check",
            citations=checklist_citations,
            disclaimer=None,
            latency_ms=elapsed_ms(),
            guard_violations=() if checklist_guard.ok else tuple(checklist_guard.violations),
            model=None,
            prompt_version=PROMPT_VERSION if explanation else None,
            checklist_result=tool_result,
            stages=tuple(stages),
        )

    # --- Tool: structured tariff lookup (never RAG) -------------------------
    # docs section 7.5. Any HS-code-shaped number in the question is looked
    # up directly rather than retrieved and summarised - table structure a
    # chunk-and-retrieve pipeline destroys is exactly what carries the
    # meaning here. Always resolves to a definite answer (found, not found,
    # or "narrow it down") rather than falling through to generic retrieval,
    # which would otherwise search for a bare number and likely find nothing.
    tariff_call: TariffLookupResult | None = None
    detected_code = understood.entities.get("hs_code") or tariff_lookup_mod.extract_hs_code(
        understood.standalone_question
    )
    if detected_code:
        emit_stage("tariff_lookup")
        tariff_call = tariff_lookup_mod.lookup_tariff(db, detected_code)

    if tariff_call is not None:
        emit_stage("tariff_lookup")
        fact_block = tariff_lookup_mod.describe_for_prompt(tariff_call)
        strong_match = tariff_call.matched and tariff_call.exact and not tariff_call.needs_review
        tariff_citation = Citation(
            document=f"Pakistan Customs Tariff {tariff_call.tariff_year or ''}".strip() or "Pakistan Customs Tariff",
            reference=f"page {tariff_call.source_page}" if tariff_call.source_page else None,
            source_kind="official_tariff",
            kind_label="Official tariff",
            is_official=True,
            currency_status="current",
            excerpt=fact_block,
            url=None,
        )
        evidence = Evidence(
            passages=(fact_block,),
            band="STRONG" if strong_match else "WEAK",
            top_relevance=1.0 if strong_match else 0.3,
            citations=(tariff_citation,),
        )
    else:
        # --- Retrieval + evidence banding -----------------------------------
        emit_stage("hybrid_retrieval")
        evidence = retriever.retrieve_evidence(
            db,
            search_queries=understood.search_queries,
            pct_code=understood.entities.get("hs_code"),
            destination_country=understood.entities.get("country"),
            top_k=settings.customs_chat_top_k,
        )
    log_stage(
        "pipeline.retrieval",
        band=evidence.band,
        top_relevance=evidence.top_relevance,
        citations=len(evidence.citations),
        via_tariff_tool=tariff_call is not None,
    )

    if evidence.band == "NONE" and not settings.customs_chat_enable_ring2:
        return _finalize(
            db,
            conversation_id=conversation.id,
            message_id=message_id,
            question=question,
            answer=(
                "I could not find sufficiently relevant evidence in the "
                "indexed regulatory corpus for that question."
            ),
            mode="REFUSAL",
            evidence_band="NONE",
            scope_intent=scope_verdict.intent,
            citations=(),
            disclaimer=None,
            latency_ms=elapsed_ms(),
            guard_violations=(),
            model=None,
            prompt_version=None,
            stages=tuple(stages),
        )

    mode: Ring = "GROUNDED" if evidence.band in ("STRONG", "WEAK") else "GENERAL"
    working_evidence = evidence

    # --- Generation + Layer 4 guard loop -----------------------------------
    draft = None
    provider_unavailable = False
    guard_violations: list[str] = []
    downgraded_by_verifier = False

    for attempt in range(MAX_GENERATION_ATTEMPTS):
        emit_stage("answer_generation")
        try:
            draft = answerer.generate_answer(
                understood=understood,
                evidence=working_evidence,
                history=history_text,
                mode=mode,
                client=client,
            )
        except (StructuredExtractionConfigurationError, StructuredExtractionProviderError) as exc:
            log_stage("pipeline.generation", error=str(exc)[:200])
            provider_unavailable = True
            draft = None
            break

        emit_stage("output_guard")
        evidence_text = "\n\n".join(working_evidence.passages)
        out_verdict = output_guard.check_output(
            draft.text, mode=mode, evidence_text=evidence_text, attempt=attempt
        )
        if out_verdict.violations:
            for violation in out_verdict.violations:
                if violation not in guard_violations:
                    guard_violations.append(violation)
            log_guard_violation("pipeline.output_guard", out_verdict.violations, attempt=attempt, mode=mode)

        if out_verdict.action == "pass":
            if mode == "GROUNDED" and settings.customs_chat_enable_verifier:
                emit_stage("groundedness_verifier")
                verify_result = verifier.verify_groundedness(
                    answer=draft.text,
                    evidence_text=evidence_text,
                    client=client,
                    enabled=True,
                )
                low_grounded = verify_result.ran and (
                    not verify_result.supported
                    or (verify_result.grounded_ratio is not None and verify_result.grounded_ratio < 0.5)
                )
                if low_grounded:
                    guard_violations.append("verifier: low groundedness")
                    mode = "GENERAL"
                    working_evidence = _no_evidence(working_evidence)
                    downgraded_by_verifier = True
                    continue
            break

        if out_verdict.action == "refuse":
            draft = None
            break

        if out_verdict.action == "downgrade":
            if mode == "GROUNDED":
                mode = "GENERAL"
                working_evidence = _no_evidence(working_evidence)
                continue
            draft = None
            break

        # action == "regenerate": loop again with the same mode.
    else:
        draft = None

    latency_ms = elapsed_ms()

    if provider_unavailable:
        # docs section 8, failure handling: never fall back to a template
        # standing in for a real answer. Show what retrieval found, honestly
        # labelled as ungenerated, rather than inventing prose.
        return _finalize(
            db,
            conversation_id=conversation.id,
            message_id=message_id,
            question=question,
            answer=chat_config.GENERATION_UNAVAILABLE_MESSAGE,
            mode="REFUSAL",
            evidence_band=evidence.band,
            scope_intent=scope_verdict.intent,
            citations=evidence.citations,
            disclaimer=None,
            latency_ms=latency_ms,
            guard_violations=tuple(guard_violations),
            model=None,
            prompt_version=None,
            tariff_result=tariff_call,
            stages=tuple(stages),
        )

    if draft is None:
        return _finalize(
            db,
            conversation_id=conversation.id,
            message_id=message_id,
            question=question,
            answer=chat_config.GUARD_REFUSAL_MESSAGE,
            mode="REFUSAL",
            evidence_band="not_applicable",
            scope_intent=scope_verdict.intent,
            citations=(),
            disclaimer=None,
            latency_ms=latency_ms,
            guard_violations=tuple(guard_violations),
            model=None,
            prompt_version=None,
            stages=tuple(stages),
        )

    disclaimer = chat_config.VERIFIER_DOWNGRADE_DISCLAIMER if downgraded_by_verifier else None
    return _finalize(
        db,
        conversation_id=conversation.id,
        message_id=message_id,
        question=question,
        answer=draft.text,
        mode=mode,
        evidence_band=working_evidence.band,
        scope_intent=scope_verdict.intent,
        citations=working_evidence.citations,
        disclaimer=disclaimer,
        latency_ms=latency_ms,
        guard_violations=tuple(guard_violations),
        model=draft.model,
        prompt_version=PROMPT_VERSION,
        tariff_result=tariff_call,
        stages=tuple(stages),
    )
