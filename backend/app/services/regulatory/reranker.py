"""Pluggable rerankers for the regulatory RAG module.

- ``CrossEncoderReranker`` — a real Hugging Face cross-encoder, loaded once,
  CPU-capable, batched. Used when available.
- ``LexicalReranker`` — an explicitly labelled degraded fallback (term overlap +
  exact PCT/SRO boosts). ``degraded=True``; it never claims to be a cross-encoder.
- ``FakeReranker`` — deterministic scores for tests, optionally from a programmed
  mapping keyed by document text.

Selected once and cached; tests inject a fake via ``set_reranker``.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from app.core.config import get_settings

_WORD = re.compile(r"[a-z0-9]+")
_PCT = re.compile(r"\b(\d{4})\.?(\d{4})\b")
_SRO = re.compile(r"(\d{2,4})\s*\(?\s*i\s*\)?\s*[/\-]?\s*(\d{4})", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(_WORD.findall(lowered))
    for match in _PCT.finditer(lowered):
        tokens.add("pct" + match.group(1) + match.group(2))
    for match in _SRO.finditer(lowered):
        tokens.add(f"sro{match.group(1)}i{match.group(2)}")
    return tokens


class Reranker(ABC):
    model_name: str
    degraded: bool
    available: bool

    @abstractmethod
    def score(self, query: str, documents: list[str]) -> list[float]:
        """Return one relevance score per document (batched internally)."""


class LexicalReranker(Reranker):
    """Explicitly labelled degraded fallback (not a cross-encoder)."""

    model_name = "lexical-overlap-degraded-v1"
    degraded = True
    available = True

    def score(self, query: str, documents: list[str]) -> list[float]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return [0.0] * len(documents)
        scores: list[float] = []
        for document in documents:
            document_tokens = _tokens(document)
            overlap = len(query_tokens & document_tokens) / len(query_tokens)
            # Boost exact PCT / SRO identifier co-occurrence.
            identifier_boost = 0.0
            for token in query_tokens:
                if token.startswith(("pct", "sro")) and token in document_tokens:
                    identifier_boost += 0.5
            scores.append(overlap + identifier_boost)
        return scores


class FakeReranker(Reranker):
    """Deterministic scores for tests; optional programmed mapping."""

    degraded = False
    available = True

    def __init__(
        self,
        mapping: dict[str, float] | None = None,
        model_name: str = "fake-reranker",
    ):
        self.model_name = model_name
        self._mapping = mapping or {}

    def score(self, query: str, documents: list[str]) -> list[float]:
        scores = []
        for document in documents:
            if document in self._mapping:
                scores.append(float(self._mapping[document]))
            else:
                # Deterministic default based on token overlap.
                query_tokens = _tokens(query)
                document_tokens = _tokens(document)
                scores.append(
                    len(query_tokens & document_tokens) / max(len(query_tokens), 1)
                )
        return scores


class CrossEncoderReranker(Reranker):
    """Real Hugging Face cross-encoder, loaded once."""

    degraded = False
    available = True

    def __init__(self, model_name: str, device: str):
        from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]

        self._model = CrossEncoder(model_name, device=device)
        self.model_name = model_name

    def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        pairs = [[query, document] for document in documents]
        predictions = self._model.predict(pairs, convert_to_numpy=True)
        return [float(value) for value in predictions]


_reranker: Reranker | None = None


def get_reranker() -> Reranker:
    """Return the cached reranker, loading the real model at most once."""
    global _reranker
    if _reranker is not None:
        return _reranker
    settings = get_settings()
    if settings.regulatory_enable_real_models:
        try:
            _reranker = CrossEncoderReranker(
                settings.regulatory_reranker_model,
                settings.regulatory_rerank_device,
            )
            return _reranker
        except Exception:
            _reranker = LexicalReranker()
            return _reranker
    _reranker = LexicalReranker()
    return _reranker


def set_reranker(reranker: Reranker | None) -> None:
    global _reranker
    _reranker = reranker


def reset_reranker() -> None:
    set_reranker(None)
