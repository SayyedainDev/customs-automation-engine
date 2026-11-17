"""Conversation persistence: ``chat_conversations`` / ``chat_turns``.

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 6.4 (schema) and section 14
(evaluation - every telemetry field written here is what the feedback loop
and the regression gate read back). Separate from
``services/assistant``'s ``AssistantConversation``/``AssistantMessage``: see
``models/customs_chat.py`` for why.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customs_chat import ChatConversation, ChatTurn


def get_or_create_conversation(
    db: Session,
    conversation_id: UUID | None,
    *,
    shipment_id: UUID | None = None,
) -> ChatConversation:
    if conversation_id is not None:
        existing = db.get(ChatConversation, conversation_id)
        if existing is not None:
            return existing
    conversation = ChatConversation(
        id=conversation_id or uuid4(),
        shipment_id=shipment_id,
    )
    db.add(conversation)
    db.flush()
    return conversation


def load_history_text(db: Session, conversation_id: UUID, *, max_turns: int) -> str | None:
    """The last ``max_turns`` turns, oldest first, as ``Role: content`` lines
    - the shape ``generation.prompts.build_answer_user_prompt`` expects for
    its CONVERSATION SO FAR block. ``max_turns`` counts turns (one per
    message, not per exchange), matching ``CUSTOMS_CHAT_HISTORY_TURNS``."""
    if max_turns <= 0:
        return None
    rows = (
        db.execute(
            select(ChatTurn)
            .where(ChatTurn.conversation_id == conversation_id)
            .order_by(ChatTurn.created_at.desc())
            .limit(max_turns)
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    ordered = list(reversed(rows))
    lines = [
        f"{'User' if turn.role == 'user' else 'Assistant'}: {turn.content}"
        for turn in ordered
    ]
    return "\n".join(lines)


def record_turn(
    db: Session,
    *,
    conversation_id: UUID,
    role: str,
    content: str,
    message_id: UUID | None = None,
    mode: str | None = None,
    scope_intent: str | None = None,
    evidence_band: str | None = None,
    citations: list[dict[str, Any]] | None = None,
    latency_ms: float | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    guard_violations: list[str] | None = None,
) -> ChatTurn:
    turn = ChatTurn(
        id=message_id or uuid4(),
        conversation_id=conversation_id,
        role=role,
        content=content,
        mode=mode,
        scope_intent=scope_intent,
        evidence_band=evidence_band,
        citations=citations,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model=model,
        prompt_version=prompt_version,
        guard_violations=guard_violations,
    )
    db.add(turn)
    conversation = db.get(ChatConversation, conversation_id)
    if conversation is not None:
        conversation.turn_count = (conversation.turn_count or 0) + 1
    db.commit()
    return turn


def record_feedback(db: Session, message_id: UUID, rating: str) -> bool:
    turn = db.get(ChatTurn, message_id)
    if turn is None:
        return False
    turn.user_feedback = rating
    db.commit()
    return True
