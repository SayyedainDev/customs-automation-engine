"""STRONG / WEAK / NONE evidence banding - replaces the old hard evidence
gate. docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 7.1.

Bands on ``ScoredEvidence.lexical_relevance_score`` rather than the raw
cross-encoder score the architecture doc originally proposed. Reason for the
deviation: that score's scale is not stable across reranker backends (a real
``cross-encoder/ms-marco-MiniLM-L-6-v2`` emits roughly [-11, 11] logits; the
offline ``LexicalReranker`` fallback emits token overlap in roughly [0, 1.5]
- see ``services/regulatory/reranker.py``), so one fixed threshold cannot
correctly gate both. ``lexical_relevance_score`` is the metric
``services/regulatory/retrieval.py`` already computes specifically to be
reranker-independent, and its own comments already report the calibration
this reuses: ~0.75-0.80 for a genuinely relevant chunk, 0.20 as the noise
floor below which even a metadata bonus should not manufacture relevance.
"""

from __future__ import annotations

from app.services.customs_chat.contracts import EvidenceBand


def band_for_relevance(top_relevance: float, *, tau_high: float, tau_low: float) -> EvidenceBand:
    if top_relevance >= tau_high:
        return "STRONG"
    if top_relevance >= tau_low:
        return "WEAK"
    return "NONE"
