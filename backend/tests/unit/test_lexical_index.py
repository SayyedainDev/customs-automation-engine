"""The lexical stage must be a stored index, not rebuilt per request."""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.services.regulatory import retrieval as retrieval_module
from app.services.regulatory.lexical_index import (
    _to_tsquery_input,
    postgres_fts_available,
)
from app.services.regulatory.retrieval import (
    RETRIEVAL_MODE,
    RETRIEVAL_MODE_PERSISTENT,
    search_regulatory_evidence,
)
from app.services.regulatory.vector_cache import (
    get_vector_matrix,
    reset_vector_matrix_cache,
)
from tests.unit.test_regulatory_retrieval import build_corpus


def test_sqlite_falls_back_and_says_so(isolated_database: Engine) -> None:
    """SQLite has no tsvector; the fallback is explicit, not accidental."""
    with Session(isolated_database) as db:
        assert postgres_fts_available(db) is False
        build_corpus(db)
        output = search_regulatory_evidence(db, query="certificate of origin", top_k=3)
    assert output.retrieval_mode == RETRIEVAL_MODE
    assert RETRIEVAL_MODE != RETRIEVAL_MODE_PERSISTENT


def test_persistent_path_builds_no_in_memory_index(
    isolated_database: Engine, monkeypatch
) -> None:
    """When the stored index serves the query, BM25 is never constructed.

    This is the property the persistent index exists for: the previous design
    tokenized and indexed the entire corpus on every request.
    """
    built: list[int] = []

    class _Tripwire(retrieval_module.BM25):
        def __init__(self, corpus_tokens, *args, **kwargs):
            built.append(len(corpus_tokens))
            super().__init__(corpus_tokens, *args, **kwargs)

    monkeypatch.setattr(retrieval_module, "BM25", _Tripwire)
    monkeypatch.setattr(retrieval_module, "postgres_fts_available", lambda db: True)
    monkeypatch.setattr(
        retrieval_module,
        "_persistent_candidates",
        lambda db, **kw: (["coo:p0:c0"], {"coo:p0:c0": 0.9}, {"coo:p0:c0": 0.8}),
    )
    with Session(isolated_database) as db:
        build_corpus(db)
        output = search_regulatory_evidence(db, query="certificate of origin", top_k=3)
    assert built == [], "an in-memory BM25 index was rebuilt on the persistent path"
    assert output.retrieval_mode == RETRIEVAL_MODE_PERSISTENT


def test_fallback_path_still_builds_its_index(isolated_database: Engine, monkeypatch) -> None:
    """The tripwire above is meaningful: the fallback really does rebuild."""
    built: list[int] = []

    class _Tripwire(retrieval_module.BM25):
        def __init__(self, corpus_tokens, *args, **kwargs):
            built.append(len(corpus_tokens))
            super().__init__(corpus_tokens, *args, **kwargs)

    monkeypatch.setattr(retrieval_module, "BM25", _Tripwire)
    with Session(isolated_database) as db:
        build_corpus(db)
        search_regulatory_evidence(db, query="certificate of origin", top_k=3)
    assert built and built[0] > 0


def test_vector_matrix_is_cached_across_calls(isolated_database: Engine) -> None:
    """The embedding matrix is loaded once per index generation."""
    reset_vector_matrix_cache()
    with Session(isolated_database) as db:
        build_corpus(db)
        first = get_vector_matrix(db, embedding_model="m", dimension=4)
        second = get_vector_matrix(db, embedding_model="m", dimension=4)
    assert first is second
    reset_vector_matrix_cache()


def test_tsquery_input_strips_operators() -> None:
    """A PCT code must not turn into a phrase operator."""
    assert _to_tsquery_input("PCT 5205.2100 yarn") == "PCT 5205 2100 yarn"
    assert _to_tsquery_input("  ") == ""
