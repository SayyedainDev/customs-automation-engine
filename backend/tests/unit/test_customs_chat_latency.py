"""Tests for the two latency optimisations in
docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 8: skipping the query rewriter
for an already-standalone first-turn question, and caching query
embeddings by provider identity."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.customs_chat.understanding.rewriter import looks_already_standalone
from app.services.regulatory.embeddings import FakeEmbeddingProvider
from app.services.regulatory.retrieval import _QUERY_EMBEDDING_CACHE, _cached_embed_query


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What is Form-E?", True),
        ("What documents do I need to export cotton yarn?", True),
        ("What about for China?", False),
        ("Is that required for my shipment?", False),
        ("What about this one?", False),
        ("Do they need a Certificate of Origin too?", False),
        ("And what about imports?", False),
        ("x" * 200, False),  # too long
        ("", False),
    ],
)
def test_looks_already_standalone(question, expected):
    assert looks_already_standalone(question) is expected


def test_embedding_cache_hits_for_same_provider_and_text():
    provider = FakeEmbeddingProvider()
    _QUERY_EMBEDDING_CACHE.clear()

    first = _cached_embed_query(provider, "what is form-e")
    second = _cached_embed_query(provider, "what is form-e")

    assert np.array_equal(first, second)
    assert (id(provider), "what is form-e") in _QUERY_EMBEDDING_CACHE


def test_embedding_cache_does_not_leak_across_provider_instances():
    """Two different provider instances (as every test's own
    ``set_embedding_provider(FakeEmbeddingProvider())`` creates) must never
    share a cached vector, even if they'd report the same model_name."""
    _QUERY_EMBEDDING_CACHE.clear()
    provider_a = FakeEmbeddingProvider(mapping={"shared text": np.ones(4, dtype=np.float32)})
    provider_b = FakeEmbeddingProvider(mapping={"shared text": np.zeros(4, dtype=np.float32)})

    vector_a = _cached_embed_query(provider_a, "shared text")
    vector_b = _cached_embed_query(provider_b, "shared text")

    assert not np.array_equal(vector_a, vector_b)


def test_embedding_cache_is_bounded():
    _QUERY_EMBEDDING_CACHE.clear()
    provider = FakeEmbeddingProvider()
    from app.services.regulatory.retrieval import _QUERY_EMBEDDING_CACHE_MAX

    for i in range(_QUERY_EMBEDDING_CACHE_MAX + 50):
        _cached_embed_query(provider, f"query number {i}")

    assert len(_QUERY_EMBEDDING_CACHE) <= _QUERY_EMBEDDING_CACHE_MAX
