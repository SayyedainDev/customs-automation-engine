"""Frozen dataclasses for every stage boundary in the customs_chat pipeline.

Each stage (input guard -> scope router -> query understanding -> retrieval
-> generation -> output guard) takes one of these in and returns one out, so
every stage is independently unit-testable without running the rest of the
pipeline. See docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 4.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

#: The three-ring scope model (docs section 5), plus REFUSAL for the
#: mechanical "that's outside what I cover" template and hard errors.
Ring = Literal["GROUNDED", "GENERAL", "OUT_OF_SCOPE", "REFUSAL"]

EvidenceBand = Literal["STRONG", "WEAK", "NONE"]

ScopeIntent = Literal[
    "customs_knowledge",
    "document_help",
    "tariff_lookup",
    "compliance_check",
    "meta",
    "out_of_scope",
]

GuardAction = Literal["pass", "regenerate", "downgrade", "refuse"]


@dataclass(frozen=True)
class InputVerdict:
    ok: bool
    reason: str
    sanitized_question: str
    injection_detected: bool = False


@dataclass(frozen=True)
class ScopeVerdict:
    in_scope: bool
    intent: ScopeIntent
    confidence: float
    decided_by: Literal["fast", "llm"]
    reason: str


@dataclass(frozen=True)
class UnderstoodQuery:
    standalone_question: str
    search_queries: tuple[str, ...]
    entities: dict[str, str | None] = field(default_factory=dict)
    requires_history: bool = False


@dataclass(frozen=True)
class Citation:
    document: str
    reference: str | None
    source_kind: str
    kind_label: str
    is_official: bool
    currency_status: str | None
    excerpt: str
    url: str | None


@dataclass(frozen=True)
class Evidence:
    passages: tuple[str, ...]
    band: EvidenceBand
    top_relevance: float
    citations: tuple[Citation, ...]
    degraded_mode: bool = False


@dataclass(frozen=True)
class DraftAnswer:
    text: str
    mode: Ring
    model: str
    tokens_out: int | None = None


@dataclass(frozen=True)
class OutputVerdict:
    ok: bool
    violations: tuple[str, ...]
    action: GuardAction
    grounded_ratio: float | None = None


@dataclass(frozen=True)
class ChecklistDocument:
    """One line of the deterministic compliance engine's own checklist -
    never generated or altered by a model. See
    ``knowledge/checklist_tool.py``."""

    display_name: str
    requirement: str  # "required" | "conditional"
    condition: str | None = None


@dataclass(frozen=True)
class ChecklistTool:
    """The tool-call result shown alongside the answer when a checklist ask
    about a supported textile product fires - kept in its own optional field
    on ``ChatTurnResult`` rather than the base answer contract (docs section
    11.3)."""

    pct_code: str
    product: str
    destination: str
    required_documents: tuple[ChecklistDocument, ...]
    conditional_documents: tuple[ChecklistDocument, ...]


@dataclass(frozen=True)
class TariffLookupResult:
    """A structured HS-code lookup result - never RAG (docs section 7.5): an
    exact or prefix match against parsed tariff rows, not a retrieved
    passage. See ``knowledge/tariff_lookup.py``."""

    query_code: str
    matched: bool
    exact: bool
    hs_code: str | None = None
    description: str | None = None
    customs_duty_pct: float | None = None
    chapter: str | None = None
    source_page: int | None = None
    tariff_year: str | None = None
    needs_review: bool = False
    candidates: tuple[str, ...] = ()
    cascading_tax_breakdown: dict[str, str] | None = None


@dataclass(frozen=True)
class ChatTurnResult:
    conversation_id: UUID
    message_id: UUID
    answer: str
    mode: Ring
    evidence_band: EvidenceBand | Literal["not_applicable"]
    scope_intent: ScopeIntent
    citations: tuple[Citation, ...]
    confidence: Literal["high", "medium", "low"]
    follow_ups: tuple[str, ...]
    disclaimer: str | None
    latency_ms: float
    guard_violations: tuple[str, ...] = ()
    model: str | None = None
    checklist_tool: ChecklistTool | None = None
    tariff_lookup: TariffLookupResult | None = None
    stages: tuple[str, ...] = ()
