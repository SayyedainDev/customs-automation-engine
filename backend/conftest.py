"""Pytest-wide runtime configuration.

The project is served by ``uvicorn[standard]``, which uses uvloop on supported
platforms.  Use that same event-loop implementation in tests so Starlette's
thread-backed ``TestClient`` and FastAPI's synchronous route/dependency workers
exercise the deployed runtime instead of the host Python selector loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator

import pytest


@pytest.fixture(scope="session", autouse=True)
def use_server_event_loop_policy() -> Generator[None, None, None]:
    """Match Uvicorn's event-loop policy when uvloop is installed."""

    try:
        import uvloop
    except ImportError:
        # uvloop is unavailable on some platforms. Their native asyncio loop
        # remains the supported fallback used by Uvicorn as well.
        yield
        return

    previous_policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(previous_policy)
