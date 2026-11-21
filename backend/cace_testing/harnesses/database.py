"""Isolated SQLite, FastAPI, and offline ML-provider lifecycle harnesses."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes.customs_audit import get_customs_audit_service
from app.core.config import get_settings
from app.core.database import Base, get_db_session
from app.main import app
from app.services import document_service, document_upload_service
from app.services.customs_audit.checkpointer import build_memory_checkpointer
from app.services.customs_audit.deps import build_default_deps
from app.services.customs_audit.factory import build_service
from app.services.customs_audit.workflow_service import CustomsAuditService
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


@dataclass(frozen=True)
class CaceRuntime:
    """Isolated runtime handles exposed to Spec Tree tests (observations only)."""

    engine: Engine
    client: TestClient
    upload_dir: Path

    @contextmanager
    def open_session(self) -> Iterator[Session]:
        """Yield a bound SQLAlchemy session against the isolated engine."""
        with Session(self.engine) as session:
            yield session


@contextmanager
def isolated_cace_runtime(extraction_mode: str = "hybrid") -> Iterator[CaceRuntime]:
    """Spin up an isolated in-memory database, temp upload store, and offline providers."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    def override_db_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    settings = get_settings()
    prev_overrides = dict(app.dependency_overrides)
    prev_groq_key = settings.groq_api_key
    prev_extraction_mode = settings.extraction_mode
    prev_live_agents = settings.langgraph_enable_live_agents
    prev_upload_dir = document_upload_service.UPLOAD_DIRECTORY
    prev_service_upload_dir = document_service.UPLOAD_DIRECTORY

    with TemporaryDirectory() as tmp_uploads:
        upload_path = Path(tmp_uploads)
        try:
            settings.groq_api_key = None
            settings.extraction_mode = extraction_mode  # type: ignore[assignment]  # validated Literal runtime mode
            settings.langgraph_enable_live_agents = False
            document_upload_service.UPLOAD_DIRECTORY = upload_path
            document_service.UPLOAD_DIRECTORY = upload_path

            set_embedding_provider(FakeEmbeddingProvider())
            set_reranker(LexicalReranker())

            def session_factory() -> Session:
                return Session(engine)

            audit_service = build_service(
                session_factory,
                deps=build_default_deps(session_factory),
                checkpointer=build_memory_checkpointer(),
            )

            async def override_audit_service() -> CustomsAuditService:
                return audit_service

            app.dependency_overrides[get_db_session] = override_db_session
            app.dependency_overrides[get_customs_audit_service] = override_audit_service

            with TestClient(app) as client:
                yield CaceRuntime(engine=engine, client=client, upload_dir=upload_path)
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(prev_overrides)
            settings.groq_api_key = prev_groq_key
            settings.extraction_mode = prev_extraction_mode
            settings.langgraph_enable_live_agents = prev_live_agents
            document_upload_service.UPLOAD_DIRECTORY = prev_upload_dir
            document_service.UPLOAD_DIRECTORY = prev_service_upload_dir
            reset_embedding_provider()
            reset_reranker()
            Base.metadata.drop_all(bind=engine)
            engine.dispose()


@contextmanager
def open_isolated_session() -> Iterator[Session]:
    """Yield an isolated SQLite Session with offline embedding/reranker providers."""
    with isolated_cace_runtime() as runtime, runtime.open_session() as session:
        yield session


@pytest.fixture
def cace_runtime() -> Iterator[CaceRuntime]:
    """Pytest fixture providing an isolated CACE runtime for Spec Tree tests."""
    with isolated_cace_runtime() as runtime:
        yield runtime
