"""Persistence for the LangGraph customs-audit workflow (Phase 3C).

Three tables hold the queryable workflow metadata, human-review tasks and an
immutable audit trail. The full resumable graph state lives in the LangGraph
checkpointer (see ``services/customs_audit/checkpointer.py``); these tables are
the project-owned, queryable projection of that state plus the audit log.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CustomsAuditWorkflow(Base):
    __tablename__ = "customs_audit_workflows"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    thread_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(48), index=True)
    current_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invoice_document_id: Mapped[UUID | None] = mapped_column(
        Uuid, nullable=True, index=True
    )
    packing_list_document_id: Mapped[UUID | None] = mapped_column(
        Uuid, nullable=True, index=True
    )
    deterministic_status: Mapped[str | None] = mapped_column(String(48), nullable=True)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    rule_data_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    vector_index_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    final_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    errors: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CustomsHumanReviewTask(Base):
    __tablename__ = "customs_human_review_tasks"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("customs_audit_workflows.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)  # open | resolved
    reason: Mapped[str] = mapped_column(Text)
    disputed_fields: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    requested_actions: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    deterministic_status: Mapped[str | None] = mapped_column(String(48), nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CustomsAuditEvent(Base):
    __tablename__ = "customs_audit_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("customs_audit_workflows.id"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    node_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32))  # system|broker|auditor|human
    actor_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    previous_state_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    new_state_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    event_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
