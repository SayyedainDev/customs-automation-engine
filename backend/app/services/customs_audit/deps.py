"""Dependency container for the customs-audit graph.

All I/O boundaries (DB sessions, the extraction/compliance pipeline, evidence
retrieval, the Broker/Auditor agents) are injected so unit tests can supply
deterministic fakes and never touch Groq, model downloads, Tesseract or the
network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.schemas.multi_line_extraction import MultiLineShipmentRequest
from app.services.customs_audit.agents import (
    AuditorAgent,
    BrokerAgent,
    DeterministicAuditorAgent,
    DeterministicBrokerAgent,
    GroqNarrator,
    NarratorFn,
)

SessionFactory = Callable[[], Session]
PipelineFn = Callable[[Session, MultiLineShipmentRequest], dict[str, Any]]
EvidenceProvider = Callable[[Session, str | None, str], list[dict[str, Any]]]


@dataclass
class WorkflowDeps:
    session_factory: SessionFactory
    run_pipeline: PipelineFn
    evidence_provider: EvidenceProvider
    broker_agent: BrokerAgent
    auditor_agent: AuditorAgent
    human_review_required: bool
    rule_data_version_fn: Callable[[], str]
    vector_index_version_fn: Callable[[Session], str]
    #: Optional Groq-backed narrator for the final-report explanation node.
    #: None means every explanation uses the deterministic template.
    explanation_narrator: NarratorFn | None = None
    explanation_model_label: str = ""


def default_run_pipeline(db: Session, request: MultiLineShipmentRequest) -> dict[str, Any]:
    from app.services.multi_line_shipment_service import (
        extract_match_and_check_multi_line_shipment,
    )

    response = extract_match_and_check_multi_line_shipment(db, request)
    return response.model_dump(mode="json")


def default_evidence_provider(
    db: Session, pct_code: str | None, query: str
) -> list[dict[str, Any]]:
    """Run the real hybrid RAG pipeline and return citation-ready evidence.

    Called for a check regardless of its deterministic status (passed,
    failed, or manual_review) - the caller decides what to do with an empty
    result (``search_regulatory_evidence`` already returns ``[]`` when nothing
    clears the deterministic relevance floor); this function never invents a
    citation to fill that gap.
    """
    from app.services.regulatory.retrieval import search_regulatory_evidence

    output = search_regulatory_evidence(
        db, query=query, pct_code=pct_code, top_k=3, verified_only=False
    )
    return [
        {
            "source_document": item.chunk.source_document,
            "source_document_id": item.chunk.document_checksum,
            "source_url": item.chunk.source_url,
            "sro_number": item.chunk.sro_number,
            "page_number": item.chunk.page_number,
            "section": item.chunk.section,
            "validation_status": item.chunk.validation_status,
            "evidence_text": item.parent.text,
            "retrieval_score": item.rrf_score,
            "rerank_score": item.cross_encoder_score,
        }
        for item in output.results
    ]


def default_rule_data_version() -> str:
    from app.services.compliance.rule_loader import load_compliance_rules

    return load_compliance_rules().rule_data_version


def default_vector_index_version(db: Session) -> str:
    from app.services.regulatory.vector_store import get_vector_index_meta

    meta = get_vector_index_meta(db)
    version = meta.get("index_version")
    return version if isinstance(version, str) else "vec-index-v1"


def build_default_deps(session_factory: SessionFactory) -> WorkflowDeps:
    from app.core.config import get_settings

    settings = get_settings()
    broker_narrator = None
    auditor_narrator = None
    if settings.langgraph_enable_live_agents:
        if settings.groq_api_key is None:
            raise RuntimeError(
                "GROQ_API_KEY is required when LANGGRAPH_ENABLE_LIVE_AGENTS=true"
            )
        api_key = settings.groq_api_key.get_secret_value()
        broker_narrator = GroqNarrator(
            api_key=api_key,
            model=settings.langgraph_broker_model or settings.groq_model,
        )
        auditor_narrator = GroqNarrator(
            api_key=api_key,
            model=settings.langgraph_auditor_model or settings.groq_model,
        )

    # The final-report explanation is a core capstone feature, not an
    # opt-in extra: it runs whenever Groq is configured at all, independent
    # of LANGGRAPH_ENABLE_LIVE_AGENTS.
    explanation_narrator = None
    explanation_model_label = ""
    if settings.groq_api_key is not None:
        explanation_model_label = settings.langgraph_explanation_model or settings.groq_model
        explanation_narrator = GroqNarrator(
            api_key=settings.groq_api_key.get_secret_value(),
            model=explanation_model_label,
        )

    return WorkflowDeps(
        session_factory=session_factory,
        run_pipeline=default_run_pipeline,
        evidence_provider=default_evidence_provider,
        broker_agent=DeterministicBrokerAgent(narrator=broker_narrator),
        auditor_agent=DeterministicAuditorAgent(narrator=auditor_narrator),
        human_review_required=settings.langgraph_human_review_required,
        rule_data_version_fn=default_rule_data_version,
        vector_index_version_fn=default_vector_index_version,
        explanation_narrator=explanation_narrator,
        explanation_model_label=explanation_model_label,
    )
