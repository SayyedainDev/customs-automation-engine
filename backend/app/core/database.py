from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Base class shared by all SQLAlchemy database models."""


@lru_cache
def get_engine() -> Engine:
    database_url = get_settings().database_url
    if not database_url:
        raise RuntimeError(
            "CUSTOMS_DATABASE_URL is not configured. Copy .env.example to .env "
            "and provide your PostgreSQL connection URL."
        )
    return create_engine(database_url, pool_pre_ping=True)


def get_db_session() -> Generator[Session, None, None]:
    """Provide one database session per request and always close it."""
    session_factory = sessionmaker(
        bind=get_engine(),
        autoflush=False,
        expire_on_commit=False,
    )
    with session_factory() as session:
        yield session


def create_tables() -> None:
    """Create tables for local development.

    A migration tool such as Alembic should replace this once schemas evolve.
    """
    from app import models  # noqa: F401 - imports models into Base.metadata

    Base.metadata.create_all(bind=get_engine())
