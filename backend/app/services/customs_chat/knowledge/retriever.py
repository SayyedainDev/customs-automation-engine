"""Adapter over the existing hybrid RAG for the customs_chat pipeline.

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md sections 7.1-7.3. Reuses
``search_regulatory_evidence`` unchanged in every stage except one: it is
called with ``min_relevance=0.0`` so the hard ``evidence_not_found`` cutoff
never fires here - every candidate the reranker ordered comes back, each
carrying its true ``lexical_relevance_score``, and this module bands them
into STRONG/WEAK/NONE (``knowledge/banding.py``) instead of getting a single
accept/reject bit. Multiple query variants (from ``understanding/rewriter``)
are each searched and fused by taking, per parent chunk, the best relevance
seen across variants - simple max-fusion rather than a second RRF pass,
which is enough to get the benefit of query expansion without duplicating
the fusion logic retrieval.py already does internally per-query.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.customs_chat.contracts import Citation, Evidence
from app.services.customs_chat.knowledge.banding import band_for_relevance
from app.services.regulatory.retrieval import ScoredEvidence, search_regulatory_evidence
from app.services.regulatory.source_kinds import (
    is_official,
    referenced_official_source,
    resolve_source_kind,
    source_kind_label,
)

#: Passed to search_regulatory_evidence so the hard evidence_not_found gate
#: never fires here - see the module docstring.
_NO_GATE = 0.0
#: How many reranked candidates each query variant contributes before fusion.
_CANDIDATES_PER_QUERY = 15


@dataclass
class _Fused:
    evidence: ScoredEvidence
    key: str


def _reference(chunk) -> str | None:  # type: ignore[no-untyped-def]
    if chunk.section:
        return chunk.section
    if chunk.page_number:
        return f"page {chunk.page_number}"
    return None


def _to_citation(evidence: ScoredEvidence) -> Citation:
    chunk = evidence.chunk
    parent = evidence.parent
    kind = resolve_source_kind(chunk)
    excerpt_text = parent.text if parent and parent.text else chunk.text
    return Citation(
        document=chunk.source_document,
        reference=_reference(chunk),
        source_kind=kind,
        kind_label=source_kind_label(kind),
        is_official=is_official(kind),
        currency_status=chunk.currency_status,
        excerpt=excerpt_text,
        url=chunk.source_url or referenced_official_source(chunk, kind),
    )


def format_citation_for_prompt(citation: Citation, index: int) -> str:
    """``[1] document — reference (kind, currency)\\npassage`` - the shape
    ``generation.prompts.build_answer_user_prompt`` expects. Kept separate
    from the model prompt text itself so a display-only citation change
    never touches prompt wording."""
    header_bits = [citation.document]
    if citation.reference:
        header_bits.append(citation.reference)
    header = " — ".join(header_bits)
    tags = citation.kind_label
    if citation.currency_status and citation.currency_status != "current":
        tags += f", {citation.currency_status}"
    return f"[{index}] {header} ({tags})\n{citation.excerpt.strip()}"


def retrieve_evidence(
    db: Session,
    *,
    search_queries: tuple[str, ...],
    pct_code: str | None = None,
    destination_country: str | None = None,
    top_k: int | None = None,
    tau_high: float | None = None,
    tau_low: float | None = None,
) -> Evidence:
    settings = get_settings()
    resolved_top_k = top_k or settings.customs_chat_top_k
    resolved_tau_high = tau_high if tau_high is not None else settings.customs_chat_tau_high
    resolved_tau_low = tau_low if tau_low is not None else settings.customs_chat_tau_low

    fused: dict[str, _Fused] = {}
    degraded = False
    pct_scopes: tuple[str | None, ...] = (pct_code, None) if pct_code else (None,)
    for query in search_queries or ():
        if not query.strip():
            continue
        for active_pct in pct_scopes:
            output = search_regulatory_evidence(
                db,
                query=query,
                pct_code=active_pct,
                destination_country=destination_country,
                top_k=resolved_top_k,
                rerank_top_n=_CANDIDATES_PER_QUERY,
                min_relevance=_NO_GATE,
            )
            degraded = degraded or output.degraded_mode
            for item in output.results:
                key = item.parent.chunk_id
                existing = fused.get(key)
                if existing is None or (
                    item.lexical_relevance_score,
                    item.cross_encoder_score,
                ) > (
                    existing.evidence.lexical_relevance_score,
                    existing.evidence.cross_encoder_score,
                ):
                    fused[key] = _Fused(evidence=item, key=key)

    ordered = sorted(
        fused.values(),
        key=lambda f: (f.evidence.lexical_relevance_score, f.evidence.cross_encoder_score),
        reverse=True,
    )[:resolved_top_k]

    if not ordered:
        return Evidence(passages=(), band="NONE", top_relevance=0.0, citations=(), degraded_mode=degraded)

    top_relevance = ordered[0].evidence.lexical_relevance_score
    band = band_for_relevance(top_relevance, tau_high=resolved_tau_high, tau_low=resolved_tau_low)

    if band == "NONE":
        # Deliberately discarded rather than shown to the model: passing
        # near-zero-relevance passages as "sources" is how a Ring 2 answer
        # accidentally cites something that does not actually support it.
        return Evidence(passages=(), band="NONE", top_relevance=top_relevance, citations=(), degraded_mode=degraded)

    # Filter out trailing near-zero noise passages that slipped in because
    # search_regulatory_evidence was called with min_relevance=0.0.
    relevant = [
        f for f in ordered if f.evidence.lexical_relevance_score >= resolved_tau_low
    ] or ordered[:1]

    citations = tuple(_to_citation(f.evidence) for f in relevant)
    passages = tuple(c.excerpt for c in citations)
    return Evidence(
        passages=passages,
        band=band,
        top_relevance=top_relevance,
        citations=citations,
        degraded_mode=degraded,
    )
