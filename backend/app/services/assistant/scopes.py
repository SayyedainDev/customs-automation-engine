"""The three scopes CACE actually has, kept deliberately separate.

Before this module one allowlist - the five supported PCT codes - stood in for
all three of the boundaries below, so an informational question about Form-E was
refused for the same reason a compliance verdict on an unsupported code was.
They answer different questions and must not be collapsed again:

A. Knowledge corpus scope - every active regulatory source that has been
   indexed. Controls what the assistant may *search and summarise*. Read from
   the database, because it grows whenever a document is ingested.

B. Deterministic compliance scope - the five textile PCT codes. Controls what
   the engine may *decide*: document requirements, pass/fail/manual-review,
   shipment readiness. Fixed, and intentionally not widened here.

C. Conversation domain scope - the subjects the assistant will discuss at all.
   Controls whether a question is answered rather than what the answer is.
   Implemented by ``domain_guard.check_domain``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from app.models.regulatory import RegulatoryChunk
from app.services.assistant.foundation import SUPPORTED_PCT_PRODUCTS

#: Scope B. Re-exported under an unambiguous name so call sites read as a
#: statement about compliance decisions, not about knowledge.
DETERMINISTIC_COMPLIANCE_PCT_CODES = tuple(sorted(SUPPORTED_PCT_PRODUCTS))

UNSUPPORTED_PCT_NOTICE = (
    "I found relevant information in the indexed regulatory corpus, but CACE’s "
    "deterministic compliance engine currently validates only five textile PCT "
    "codes. This answer is informational and is not a compliance decision."
)

NO_EVIDENCE_MESSAGE = (
    "I could not find sufficiently relevant evidence in the current CACE "
    "regulatory corpus."
)

GENERAL_LIMITATION = (
    "CACE is a single-user prototype. Answers are drawn from a fixed snapshot of "
    "the indexed regulatory corpus, may not reflect the most recent amendments, "
    "and are not official customs or legal advice."
)

INFORMATIONAL_LIMITATION = (
    "This is an informational answer about indexed regulatory sources. It is not "
    "a compliance decision and does not confirm that any shipment will clear "
    "customs."
)


@dataclass(frozen=True)
class KnowledgeCorpusScope:
    """Scope A, as it exists in the database right now."""

    source_documents: tuple[str, ...]
    document_types: tuple[str, ...]
    chunk_count: int

    @property
    def is_empty(self) -> bool:
        return self.chunk_count == 0


def get_knowledge_corpus_scope(db: Session) -> KnowledgeCorpusScope:
    """Every active indexed source - not filtered by the five PCT codes."""
    documents = [
        value
        for value in db.execute(
            select(distinct(RegulatoryChunk.source_document))
        ).scalars()
        if value
    ]
    types = [
        value
        for value in db.execute(
            select(distinct(RegulatoryChunk.document_type))
        ).scalars()
        if value
    ]
    count = len(
        list(
            db.execute(
                select(RegulatoryChunk.chunk_id).where(
                    RegulatoryChunk.is_parent.is_(False)
                )
            ).scalars()
        )
    )
    return KnowledgeCorpusScope(
        source_documents=tuple(sorted(documents)),
        document_types=tuple(sorted(types)),
        chunk_count=count,
    )


def is_deterministic_compliance_scope(pct_code: str | None) -> bool:
    """Scope B membership. Deliberately narrow; do not widen."""
    return bool(pct_code) and pct_code in SUPPORTED_PCT_PRODUCTS


def supported_compliance_scope_labels() -> list[str]:
    """Human-readable statement of scope B for API responses."""
    return [
        f"{code} — {SUPPORTED_PCT_PRODUCTS[code]}"
        for code in DETERMINISTIC_COMPLIANCE_PCT_CODES
    ]
