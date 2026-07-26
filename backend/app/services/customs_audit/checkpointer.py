"""LangGraph checkpointer factory.

Production path: PostgreSQL (reuses ``CUSTOMS_DATABASE_URL``). If PostgreSQL is
explicitly selected, configuration or connection failures are raised instead of
silently changing durability semantics. SQLite remains an explicit local option.
In-memory checkpointing is only used for tests.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.core.config import get_settings


def _sqlite_saver(path: str) -> Any:
    from langgraph.checkpoint.sqlite import SqliteSaver

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    return saver


def build_checkpointer() -> Any:
    """Return a checkpointer for the configured backend (never memory here)."""
    settings = get_settings()
    backend = settings.langgraph_checkpoint_backend.lower()

    if backend == "postgres":
        if not settings.database_url:
            raise RuntimeError(
                "CUSTOMS_DATABASE_URL is required for PostgreSQL checkpointing"
            )
        import psycopg
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row

        # The saver owns this connection through the cached graph/service
        # lifetime. A temporary ``from_conn_string`` context would close as
        # soon as the factory returned and make the first graph call fail.
        connection = psycopg.connect(
            settings.database_url.replace("+psycopg", ""),
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        saver = PostgresSaver(connection)
        saver.setup()
        return saver

    if backend == "memory":
        # Explicitly requested (e.g. ephemeral dev); production should not use this.
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()

    if backend == "sqlite":
        return _sqlite_saver(settings.langgraph_checkpoint_path)
    raise ValueError(
        "LANGGRAPH_CHECKPOINT_BACKEND must be postgres, sqlite, or memory"
    )


def build_memory_checkpointer() -> Any:
    """In-memory checkpointer for tests only."""
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()
