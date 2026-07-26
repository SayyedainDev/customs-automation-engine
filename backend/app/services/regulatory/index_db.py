"""Session helper for the regulatory evidence index.

Uses the configured PostgreSQL database when ``CUSTOMS_DATABASE_URL`` is set
(production / the same DB the API uses). When it is not set (offline demos,
scripts), it falls back to a local SQLite index file so ingestion, retrieval and
evaluation can run without a database server. The API endpoints always use the
normal request session (``get_db_session``); this helper is only for scripts.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 - register all tables on Base.metadata
from app.core.config import get_settings
from app.core.database import Base

LOCAL_INDEX_PATH = Path(__file__).resolve().parents[3] / "regulatory_index.sqlite3"

_engine: Engine | None = None


def get_index_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    database_url = get_settings().database_url
    if database_url:
        _engine = create_engine(database_url, pool_pre_ping=True)
    else:
        _engine = create_engine(f"sqlite+pysqlite:///{LOCAL_INDEX_PATH}")
    Base.metadata.create_all(bind=_engine)
    return _engine


def index_session() -> Session:
    factory = sessionmaker(
        bind=get_index_engine(), autoflush=False, expire_on_commit=False
    )
    return factory()
