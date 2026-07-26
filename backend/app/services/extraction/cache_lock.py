"""Process-local serialization for per-document structured extraction.

The production API currently runs one uvicorn worker. A keyed lock prevents two
requests in that process from observing the same cache miss and both spending
provider quota. The key is the document UUID rather than a narrower extraction
profile so invoice, packing-list and supporting-document writers cannot race
their read-modify-write updates to the shared ``structured_data`` JSON column.

Every service must refresh and recheck its own profile entry after acquiring the
lock. The lock is deliberately not a cache and carries no legal state.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import threading
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.documents import DocumentUploadRecord


class _LockEntry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users = 0


_registry_guard = threading.Lock()
_document_locks: dict[UUID, _LockEntry] = {}


@contextmanager
def structured_extraction_document_lock(document_id: UUID) -> Iterator[None]:
    """Serialize cache misses and JSON writes for one uploaded document."""
    with _registry_guard:
        entry = _document_locks.get(document_id)
        if entry is None:
            entry = _LockEntry()
            _document_locks[document_id] = entry
        entry.users += 1

    entry.lock.acquire()
    try:
        yield
    finally:
        entry.lock.release()
        with _registry_guard:
            entry.users -= 1
            if entry.users == 0 and _document_locks.get(document_id) is entry:
                del _document_locks[document_id]


def refresh_extraction_cache_record(
    db: Session,
    document: DocumentUploadRecord,
) -> None:
    """Refresh the JSON cache, taking a PostgreSQL row lock when available.

    The process-local lock covers SQLite and the configured single-worker API.
    ``FOR UPDATE`` additionally closes the duplicate-call/lost-update window
    across PostgreSQL-backed worker processes. Callers recheck their nested
    profile immediately after this refresh and before invoking the provider.
    """
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.refresh(document, with_for_update=True)
        return
    db.refresh(document)
