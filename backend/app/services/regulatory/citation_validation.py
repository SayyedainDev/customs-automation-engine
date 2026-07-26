"""Validate RAG answer citations against the actually-retrieved evidence.

A generated answer may only cite sources that were retrieved, with matching page
numbers and SRO numbers, and may not introduce an SRO identifier that is not in
the retrieved context. If any check fails, the caller downgrades the explanation
to ``manual_review_required`` (the deterministic compliance status is never
touched regardless).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from app.services.regulatory.retrieval import ScoredEvidence

_SRO_IN_TEXT = re.compile(r"\b\d{2,4}\s*\(\s*[IiVvXx]+\s*\)\s*/\s*\d{4}\b")


@dataclass
class DraftCitation:
    source_document: str | None
    sro_number: str | None = None
    page_number: int | None = None
    source_url: str | None = None
    validation_status: str | None = None
    evidence_text: str | None = None


@dataclass
class CitationValidation:
    ok: bool
    reason: str


def _normalize_sro(value: str) -> str:
    return re.sub(r"[^0-9ivx]", "", value.casefold())


def validate_rag_output(
    *,
    retrieved: list[ScoredEvidence],
    answer: str | None,
    draft_citations: list[DraftCitation],
) -> CitationValidation:
    retrieved_docs = {item.chunk.source_document for item in retrieved}
    retrieved_sros = {
        _normalize_sro(item.chunk.sro_number)
        for item in retrieved
        if item.chunk.sro_number
    }
    pages_by_doc: dict[str, set[int | None]] = defaultdict(set)
    for item in retrieved:
        pages_by_doc[item.chunk.source_document].add(item.chunk.page_number)
        pages_by_doc[item.parent.source_document].add(item.parent.page_number)

    if not draft_citations:
        return CitationValidation(False, "no citations were produced for a grounded answer")

    for citation in draft_citations:
        if not citation.source_document or citation.source_document not in retrieved_docs:
            return CitationValidation(
                False,
                f"cited source '{citation.source_document}' is not in the retrieved evidence",
            )
        if citation.sro_number:
            if _normalize_sro(citation.sro_number) not in retrieved_sros:
                return CitationValidation(
                    False, f"cited SRO '{citation.sro_number}' is not supported by retrieved evidence"
                )
        if citation.page_number is not None:
            if citation.page_number not in pages_by_doc.get(citation.source_document, set()):
                return CitationValidation(
                    False,
                    f"cited page {citation.page_number} does not match the retrieved metadata "
                    f"for '{citation.source_document}'",
                )

    # No SRO identifier may appear in the answer unless it was retrieved.
    if answer:
        for match in _SRO_IN_TEXT.finditer(answer):
            if _normalize_sro(match.group(0)) not in retrieved_sros:
                return CitationValidation(
                    False,
                    f"answer references unsupported SRO '{match.group(0)}' not in retrieved evidence",
                )
    return CitationValidation(True, "all citations verified against retrieved evidence")
