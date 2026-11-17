"""API contract for the general Pakistan Customs Assistant.

Deliberately smaller than ``schemas/assistant.py``'s ``RegulatoryChatResponse``
(16 fields, most of them textile-compliance artefacts). See
docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 11.3: checklist data, when the
textile compliance tool fires, belongs in its own optional field rather than
the base contract.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Ring = Literal["GROUNDED", "GENERAL", "OUT_OF_SCOPE", "REFUSAL"]
EvidenceBand = Literal["STRONG", "WEAK", "NONE", "not_applicable"]
Confidence = Literal["high", "medium", "low"]


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None
    shipment_id: UUID | None = None
    stream: bool = True


class SourceSchema(BaseModel):
    document: str
    reference: str | None = None
    source_kind: str
    kind_label: str
    is_official: bool
    currency_status: str | None = None
    excerpt: str
    url: str | None = None


class ChecklistDocumentSchema(BaseModel):
    display_name: str
    requirement: str
    condition: str | None = None


class ChecklistToolSchema(BaseModel):
    """Present only when a checklist ask about a supported textile product
    fired the deterministic compliance engine (docs section 13.5). The
    engine's own list, rendered verbatim - never derived from ``answer``."""

    pct_code: str
    product: str
    destination: str
    required_documents: list[ChecklistDocumentSchema]
    conditional_documents: list[ChecklistDocumentSchema]


class TariffLookupSchema(BaseModel):
    """Present only when the question named an HS-code-shaped number (docs
    section 7.5) - an exact row lookup, never a retrieved/summarised passage."""

    query_code: str
    matched: bool
    exact: bool
    hs_code: str | None = None
    description: str | None = None
    customs_duty_pct: float | None = None
    source_page: int | None = None
    tariff_year: str | None = None
    needs_review: bool = False
    candidates: list[str] = Field(default_factory=list)
    cascading_tax_breakdown: dict[str, str] | None = None


class ChatMessageResponse(BaseModel):
    """The non-streaming shape. The SSE stream carries the same fields split
    across ``meta`` / ``token`` / ``sources`` / ``done`` events - see the API
    route docstring."""

    conversation_id: UUID
    message_id: UUID
    answer: str
    mode: Ring
    evidence_band: EvidenceBand
    scope_intent: str
    sources: list[SourceSchema] = Field(default_factory=list)
    confidence: Confidence = "medium"
    follow_ups: list[str] = Field(default_factory=list)
    disclaimer: str | None = None
    latency_ms: float
    checklist_tool: ChecklistToolSchema | None = None
    tariff_lookup: TariffLookupSchema | None = None


class ConversationTurnSchema(BaseModel):
    id: UUID
    role: str
    content: str
    mode: str | None = None
    evidence_band: str | None = None
    sources: list[SourceSchema] = Field(default_factory=list)


class ConversationHistoryResponse(BaseModel):
    id: UUID
    shipment_id: UUID | None = None
    turns: list[ConversationTurnSchema]


class FeedbackRequest(BaseModel):
    message_id: UUID
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=1000)


class CoverageResponse(BaseModel):
    """What the corpus actually covers right now - see docs section 11.2:
    lets a user discover the corpus's limits from one page instead of one
    refusal at a time."""

    indexed_documents: list[str]
    document_types: list[str]
    trade_directions_covered: list[str]
    known_gaps: list[str]
    corpus_snapshot_date: str | None = None
