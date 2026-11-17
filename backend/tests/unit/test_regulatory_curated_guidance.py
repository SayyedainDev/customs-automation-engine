"""End-to-end ingestion test for the curated plain-English guidance pack
(docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 6.3), against the real files
on disk under ``regulatory_data/curated/guidance/`` and a real (in-memory)
database - not a mock of the directory. Uses the suite's standard fake
embedder/reranker so nothing downloads a model or hits the network.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.regulatory import RegulatoryChunk
from app.services.regulatory.embeddings import FakeEmbeddingProvider, reset_embedding_provider, set_embedding_provider
from app.services.regulatory.ingestion import ingest_source
from app.services.regulatory.reranker import LexicalReranker, reset_reranker, set_reranker
from app.services.regulatory.retrieval import search_regulatory_evidence
from app.services.regulatory.sources import discover_sources


@pytest.fixture(autouse=True)
def _offline_models():
    set_embedding_provider(FakeEmbeddingProvider())
    set_reranker(LexicalReranker())
    yield
    reset_embedding_provider()
    reset_reranker()


@pytest.fixture()
def engine() -> Generator[Engine, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


def _curated_source():
    sources = {s.key: s for s in discover_sources()}
    return sources["customs_chat_curated_guidance"]


def test_curated_source_is_registered_and_points_at_a_real_directory():
    source = _curated_source()
    assert source.kind == "curated_guidance_dir"
    assert source.chunk_strategy == "faq"
    assert source.path.is_dir()
    assert list(source.path.glob("*.md")), "expected at least one curated guidance file on disk"


def test_ingests_every_curated_file_as_one_parent_and_one_child(engine):
    source = _curated_source()
    file_count = len(list(source.path.glob("*.md")))
    with Session(engine) as db:
        result = ingest_source(db, source, rule_data_version="test-rules-v1")
        db.commit()
        assert result.status == "ingested"
        assert result.parent_chunks == file_count
        assert result.child_chunks == file_count  # "faq": one child per parent, not further split

        chunks = db.execute(select(RegulatoryChunk)).scalars().all()
        assert len(chunks) == file_count * 2
        # Every ingested row carries the curated, non-official provenance -
        # never displayable as an official regulation.
        for chunk in chunks:
            assert chunk.source_kind == "curated_rule_summary"
            assert chunk.validation_status == "partially_verified"


def test_a_curated_entry_is_retrievable_by_its_own_question_wording(engine):
    """The whole point of the curated pack: an entry should retrieve for a
    question that closely echoes its own wording, without needing the exact
    corpus vocabulary a raw statute uses."""
    source = _curated_source()
    with Session(engine) as db:
        ingest_source(db, source, rule_data_version="test-rules-v1")
        db.commit()

        output = search_regulatory_evidence(db, query="what is form-e", verified_only=True)
        assert output.status != "evidence_not_found"
        top = output.results[0]
        assert "form-e" in top.chunk.text.lower() or "form e" in top.chunk.text.lower()


def test_re_ingesting_unchanged_files_is_idempotent(engine):
    source = _curated_source()
    with Session(engine) as db:
        first = ingest_source(db, source, rule_data_version="test-rules-v1")
        db.commit()
        second = ingest_source(db, source, rule_data_version="test-rules-v1")
        db.commit()
    assert first.status == "ingested"
    assert second.status == "skipped_idempotent"
