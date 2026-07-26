"""Pluggable dense embedding providers for the regulatory RAG module.

Three interchangeable providers behind one interface:

- ``SentenceTransformerEmbeddingProvider`` — a real Hugging Face
  sentence-transformer (e.g. BGE / MiniLM), loaded once, CPU-capable, batched,
  normalized. Used when the library and model are available.
- ``HashingEmbeddingProvider`` — an explicitly labelled, deterministic, offline
  fallback (feature-hashed bag of n-grams). It is ``degraded=True`` and does NOT
  pretend to be a transformer; it simply keeps the dense pipeline functional
  offline.
- ``FakeEmbeddingProvider`` — deterministic vectors for unit tests, optionally
  from a programmed mapping so a test can fully control dense ranking.

The provider is created once and cached (``get_embedding_provider``); tests
inject a fake via ``set_embedding_provider``. No external embedding API is ever
called, and a real model is never downloaded during tests.
"""

from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod

import numpy as np

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9]+")


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class EmbeddingProvider(ABC):
    model_name: str
    dimension: int
    normalized: bool
    degraded: bool
    available: bool

    @abstractmethod
    def embed(self, texts: list[str], batch_size: int | None = None) -> np.ndarray:
        """Return a float32 (len(texts), dimension) matrix."""

    def embed_query(self, text: str) -> np.ndarray:
        vectors = self.embed([text])
        return vectors[0] if len(vectors) else np.zeros(self.dimension, dtype=np.float32)


def _ngram_features(text: str) -> list[str]:
    lowered = text.lower()
    words = _WORD.findall(lowered)
    features: list[str] = list(words)
    features.extend(f"{a}_{b}" for a, b in zip(words, words[1:]))
    compact = re.sub(r"\s+", " ", lowered)
    features.extend(compact[i : i + 3] for i in range(0, max(0, len(compact) - 2)))
    return features


class HashingEmbeddingProvider(EmbeddingProvider):
    """Deterministic offline fallback. Explicitly degraded, not a transformer."""

    def __init__(self, dimension: int = 256):
        self.dimension = dimension
        self.model_name = f"hashing-bow-degraded-v1-d{dimension}"
        self.normalized = True
        self.degraded = True
        self.available = True

    def embed(self, texts: list[str], batch_size: int | None = None) -> np.ndarray:
        vectors = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for row, text in enumerate(texts):
            for feature in _ngram_features(text):
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                value = int.from_bytes(digest, "big")
                index = (value >> 1) % self.dimension
                sign = 1.0 if (value & 1) else -1.0
                vectors[row, index] += sign
        return _l2_normalize(vectors)


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic vectors for tests; optional programmed mapping."""

    def __init__(
        self,
        dimension: int = 24,
        mapping: dict[str, list[float]] | None = None,
        model_name: str = "fake-deterministic",
    ):
        self.dimension = dimension
        self.model_name = model_name
        self.normalized = True
        self.degraded = False
        self.available = True
        self._mapping = mapping or {}

    def embed(self, texts: list[str], batch_size: int | None = None) -> np.ndarray:
        rows = []
        for text in texts:
            if text in self._mapping:
                vector = np.zeros(self.dimension, dtype=np.float32)
                supplied = np.asarray(self._mapping[text], dtype=np.float32)
                vector[: min(self.dimension, supplied.size)] = supplied[: self.dimension]
            else:
                vector = np.zeros(self.dimension, dtype=np.float32)
                for feature in _ngram_features(text):
                    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                    value = int.from_bytes(digest, "big")
                    vector[(value >> 1) % self.dimension] += 1.0 if (value & 1) else -1.0
            rows.append(vector)
        matrix = np.vstack(rows) if rows else np.zeros((0, self.dimension), dtype=np.float32)
        return _l2_normalize(matrix)


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    """Real Hugging Face sentence-transformer, loaded once."""

    def __init__(self, model_name: str, device: str, batch_size: int):
        # Import lazily so the dependency is only required when actually used.
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

        self._model = SentenceTransformer(model_name, device=device)
        self.model_name = model_name
        dimension = self._model.get_sentence_embedding_dimension()
        if dimension is None:
            raise ValueError(
                f"Embedding model '{model_name}' did not report a dimension"
            )
        self.dimension = int(dimension)
        self.normalized = True
        self.degraded = False
        self.available = True
        self._batch_size = batch_size

    def embed(self, texts: list[str], batch_size: int | None = None) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        vectors = self._model.encode(
            texts,
            batch_size=batch_size or self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    """Return the cached provider, loading the real model at most once."""
    global _provider
    if _provider is not None:
        return _provider
    settings = get_settings()
    if settings.regulatory_enable_real_models:
        try:
            _provider = SentenceTransformerEmbeddingProvider(
                settings.regulatory_embedding_model,
                settings.regulatory_embedding_device,
                settings.regulatory_embedding_batch_size,
            )
            return _provider
        except Exception:
            logger.exception(
                "Failed to initialize regulatory embedding model %r on device "
                "%r; using the degraded offline hashing provider.",
                settings.regulatory_embedding_model,
                settings.regulatory_embedding_device,
            )
            # Library/model unavailable -> explicit degraded fallback.
            _provider = HashingEmbeddingProvider()
            return _provider
    _provider = HashingEmbeddingProvider()
    return _provider


def set_embedding_provider(provider: EmbeddingProvider | None) -> None:
    """Inject a provider (tests) or clear the cache when passed None."""
    global _provider
    _provider = provider


def reset_embedding_provider() -> None:
    set_embedding_provider(None)
