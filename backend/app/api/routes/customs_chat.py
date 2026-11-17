"""API for the general Pakistan Customs Assistant.

Supersedes ``/api/v1/assistant/regulatory/chat`` for conversational
questions - see docs/CUSTOMS_CHATBOT_ARCHITECTURE.md sections 11 and 13.
The old assistant router (``api/routes/assistant.py``) is untouched: its
``/guidance`` and ``/shipments/{id}/chat`` endpoints still serve the
deterministic textile compliance engine, which this does not replace.

On streaming: ``POST /messages`` with ``stream: true`` (the default) opens
an SSE connection. The pipeline call in ``pipeline.answer_customs_question``
is blocking and already fully guarded (see ``generation/answerer.py`` for
why real token-level streaming is deferred past phase 1) - this endpoint
paces the finished, safe answer over the wire in word-chunks so the client
still gets a live-typing UI, rather than one large response at the end.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.models.customs_chat import ChatConversation, ChatTurn
from app.schemas.customs_chat import (
    ChatMessageRequest,
    ChatMessageResponse,
    ChecklistDocumentSchema,
    ChecklistToolSchema,
    ConversationHistoryResponse,
    ConversationTurnSchema,
    CoverageResponse,
    FeedbackRequest,
    SourceSchema,
    TariffLookupSchema,
)
from app.services.customs_chat import config as chat_config
from app.services.customs_chat.contracts import ChatTurnResult
from app.services.customs_chat.memory.history import record_feedback
from app.services.customs_chat.pipeline import answer_customs_question
from app.services.assistant.scopes import get_knowledge_corpus_scope
from app.services.regulatory.source_kinds import get_corpus_snapshot_date

router = APIRouter(prefix="/api/v1/customs-chat", tags=["Customs Assistant"])

#: Documents this build cannot yet answer well - a static reflection of
#: docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 2.4's acquisition list, shown
#: on /coverage so a user can discover the corpus's limits from one page
#: rather than one refusal at a time.
_KNOWN_GAPS = [
    "Import Policy Order 2022 (imports are not yet covered as a dedicated policy source)",
    "Full Pakistan Customs Tariff as retrievable narrative text (only textile chapters "
    "50-63 are chunked for retrieval; the full tariff is separately available as an "
    "exact HS-code lookup, not narrative text, once tariff_lines is loaded)",
    "Baggage Rules and passenger/vehicle-import allowances",
    "Customs Valuation Rules and Valuation Rulings",
    "Courier and postal import rules",
    "Sales tax and income tax at the import stage",
    "Duty drawback and Export Facilitation Scheme SROs (on disk, not yet indexed)",
]


def _citations_to_schema(citations) -> list[SourceSchema]:  # type: ignore[no-untyped-def]
    return [
        SourceSchema(
            document=c.document,
            reference=c.reference,
            source_kind=c.source_kind,
            kind_label=c.kind_label,
            is_official=c.is_official,
            currency_status=c.currency_status,
            excerpt=c.excerpt,
            url=c.url,
        )
        for c in citations
    ]


def _checklist_tool_to_schema(tool) -> ChecklistToolSchema | None:  # type: ignore[no-untyped-def]
    if tool is None:
        return None
    return ChecklistToolSchema(
        pct_code=tool.pct_code,
        product=tool.product,
        destination=tool.destination,
        required_documents=[
            ChecklistDocumentSchema(display_name=d.display_name, requirement=d.requirement, condition=d.condition)
            for d in tool.required_documents
        ],
        conditional_documents=[
            ChecklistDocumentSchema(display_name=d.display_name, requirement=d.requirement, condition=d.condition)
            for d in tool.conditional_documents
        ],
    )


def _tariff_lookup_to_schema(result) -> TariffLookupSchema | None:  # type: ignore[no-untyped-def]
    if result is None:
        return None
    return TariffLookupSchema(
        query_code=result.query_code,
        matched=result.matched,
        exact=result.exact,
        hs_code=result.hs_code,
        description=result.description,
        customs_duty_pct=result.customs_duty_pct,
        source_page=result.source_page,
        tariff_year=result.tariff_year,
        needs_review=result.needs_review,
        candidates=list(result.candidates),
        cascading_tax_breakdown=result.cascading_tax_breakdown,
    )


def _to_response(result: ChatTurnResult) -> ChatMessageResponse:
    return ChatMessageResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        answer=result.answer,
        mode=result.mode,
        evidence_band=result.evidence_band,
        scope_intent=result.scope_intent,
        sources=_citations_to_schema(result.citations),
        confidence=result.confidence,
        follow_ups=list(result.follow_ups),
        disclaimer=result.disclaimer,
        latency_ms=result.latency_ms,
        checklist_tool=_checklist_tool_to_schema(result.checklist_tool),
        tariff_lookup=_tariff_lookup_to_schema(result.tariff_lookup),
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


#: Word-count per paced SSE token event. Small enough to still read as a
#: live typing effect, large enough that a long answer is not hundreds of
#: individual SSE frames.
_STREAM_CHUNK_WORDS = 6


def _stream_live_turn(
    db: Session, payload: ChatMessageRequest
) -> Generator[str, None, None]:
    yield _sse("stage", {"stage": "input_guard"})
    result = answer_customs_question(
        db,
        message=payload.message,
        conversation_id=payload.conversation_id,
        shipment_id=payload.shipment_id,
    )
    for stage in result.stages:
        if stage != "input_guard":
            yield _sse("stage", {"stage": stage})
    yield from _stream_result(result, include_stages=False)


def _stream_result(
    result: ChatTurnResult, *, include_stages: bool = True
) -> Generator[str, None, None]:
    if include_stages:
        for stage in result.stages:
            yield _sse("stage", {"stage": stage})
    yield _sse(
        "meta",
        {
            "conversation_id": str(result.conversation_id),
            "message_id": str(result.message_id),
            "scope_intent": result.scope_intent,
            "mode": result.mode,
            "evidence_band": result.evidence_band,
            "stages": list(result.stages),
        },
    )
    words = result.answer.split(" ")
    for start in range(0, len(words), _STREAM_CHUNK_WORDS):
        chunk = " ".join(words[start : start + _STREAM_CHUNK_WORDS])
        if start + _STREAM_CHUNK_WORDS < len(words):
            chunk += " "
        yield _sse("token", {"text": chunk})
    yield _sse("sources", {"sources": [s.model_dump() for s in _citations_to_schema(result.citations)]})
    checklist_schema = _checklist_tool_to_schema(result.checklist_tool)
    tariff_schema = _tariff_lookup_to_schema(result.tariff_lookup)
    if checklist_schema is not None or tariff_schema is not None:
        yield _sse(
            "tool_result",
            {
                "checklist_tool": checklist_schema.model_dump() if checklist_schema else None,
                "tariff_lookup": tariff_schema.model_dump() if tariff_schema else None,
            },
        )
    yield _sse(
        "done",
        {
            "mode": result.mode,
            "confidence": result.confidence,
            "follow_ups": list(result.follow_ups),
            "disclaimer": result.disclaimer,
            "latency_ms": result.latency_ms,
        },
    )


@router.post("/messages")
def post_message(payload: ChatMessageRequest, db: Session = Depends(get_db_session)):
    if payload.conversation_id:
        conv = db.get(ChatConversation, payload.conversation_id)
        if conv and conv.shipment_id and payload.shipment_id and conv.shipment_id != payload.shipment_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Conversation is bound to a different shipment.",
            )

    if not payload.stream:
        result = answer_customs_question(
            db,
            message=payload.message,
            conversation_id=payload.conversation_id,
            shipment_id=payload.shipment_id,
        )
        return _to_response(result)

    return StreamingResponse(_stream_live_turn(db, payload), media_type="text/event-stream")


@router.get("/conversations/{conversation_id}", response_model=ConversationHistoryResponse)
def get_conversation(conversation_id: UUID, db: Session = Depends(get_db_session)):
    conversation = db.get(ChatConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    turns = (
        db.execute(
            select(ChatTurn)
            .where(ChatTurn.conversation_id == conversation_id)
            .order_by(ChatTurn.created_at)
        )
        .scalars()
        .all()
    )
    return ConversationHistoryResponse(
        id=conversation.id,
        shipment_id=conversation.shipment_id,
        turns=[
            ConversationTurnSchema(
                id=turn.id,
                role=turn.role,
                content=turn.content,
                mode=turn.mode,
                evidence_band=turn.evidence_band,
                sources=[SourceSchema(**c) for c in (turn.citations or [])],
            )
            for turn in turns
        ],
    )


@router.get("/suggestions")
def get_suggestions():
    return {"suggestions": list(chat_config.STARTER_SUGGESTIONS)}


@router.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
def post_feedback(payload: FeedbackRequest, db: Session = Depends(get_db_session)):
    found = record_feedback(db, payload.message_id, payload.rating)
    if not found:
        raise HTTPException(status_code=404, detail="Message not found")


@router.get("/coverage", response_model=CoverageResponse)
def get_coverage(db: Session = Depends(get_db_session)):
    scope = get_knowledge_corpus_scope(db)
    snapshot = get_corpus_snapshot_date(db)
    return CoverageResponse(
        indexed_documents=list(scope.source_documents),
        document_types=list(scope.document_types),
        trade_directions_covered=["export", "import (partial - Customs Act/Rules only)"],
        known_gaps=_KNOWN_GAPS,
        corpus_snapshot_date=snapshot.isoformat() if snapshot else None,
    )
