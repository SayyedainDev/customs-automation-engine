"""Persistence for CACE Assistant conversations and messages."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    shipment_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("customs_audit_workflows.id"), nullable=True, index=True
    )
    mode: Mapped[str] = mapped_column(String(64))  # pre_submission_guidance | shipment_assistant
    pct_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("assistant_conversations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    text: Mapped[str] = mapped_column(Text)
    answer_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
