"""Construction and app-level caching of the customs-audit service."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from sqlalchemy.orm import Session

from app.services.customs_audit.checkpointer import build_checkpointer
from app.services.customs_audit.deps import WorkflowDeps, build_default_deps
from app.services.customs_audit.graph import build_customs_audit_graph
from app.services.customs_audit.workflow_service import CustomsAuditService


def build_service(
    session_factory: Any,
    *,
    deps: WorkflowDeps | None = None,
    checkpointer: Any | None = None,
) -> CustomsAuditService:
    resolved_deps = deps or build_default_deps(session_factory)
    graph = build_customs_audit_graph(resolved_deps, checkpointer or build_checkpointer())
    return CustomsAuditService(graph)


@lru_cache(maxsize=1)
def get_app_customs_audit_service() -> CustomsAuditService:
    from app.core.database import get_engine

    def session_factory() -> Session:
        return Session(get_engine())

    return build_service(session_factory)
