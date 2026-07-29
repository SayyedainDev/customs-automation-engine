from collections.abc import Generator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import Base, get_db_session
from app.main import app
from app.services.regulatory.embeddings import (
    FakeEmbeddingProvider,
    reset_embedding_provider,
    set_embedding_provider,
)
from app.services.regulatory.reranker import (
    LexicalReranker,
    reset_reranker,
    set_reranker,
)


@pytest.fixture(autouse=True)
def default_extraction_mode_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin EXTRACTION_MODE to "legacy" for this suite by default.

    The live app defaults to "hybrid", but most of this suite predates that
    path and exercises the legacy single-shot/staged-ladder behavior
    specifically. Tests that want the hybrid path opt in explicitly with
    ``monkeypatch.setattr(get_settings(), "extraction_mode", "hybrid")``.
    """
    monkeypatch.setattr(get_settings(), "extraction_mode", "legacy")


@pytest.fixture(autouse=True)
def default_fake_embedding_provider():
    """Default every test to a fast, deterministic, offline embedder.

    Historical-shipment indexing (shipment_search/vector_store.py) now runs
    on every finalized customs-audit workflow, so without this fixture the
    entire test_customs_audit.py suite would transitively call the real
    sentence-transformer singleton on every run. Tests that need specific
    embedding behavior already inject their own provider as a function
    parameter (bypassing this global one) or set their own via
    ``set_embedding_provider``, which simply overrides this default.
    """
    set_embedding_provider(FakeEmbeddingProvider())
    yield
    reset_embedding_provider()


@pytest.fixture(autouse=True)
def default_offline_reranker():
    """Pin the reranker the same way the embedder is pinned.

    Without this the suite silently inherits ``REGULATORY_ENABLE_REAL_MODELS``
    from the developer's ``.env``: with it set, ``get_reranker()`` downloads and
    loads the real cross-encoder mid-test, so results depended on local
    environment and model cache rather than on the code under test. Tests that
    care about a specific reranker still inject their own.
    """
    set_reranker(LexicalReranker())
    yield
    reset_reranker()


@pytest.fixture(autouse=True)
def isolated_database() -> Generator[Engine, None, None]:
    """Use a fresh in-memory database for every test."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    def override_db_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    yield engine
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
