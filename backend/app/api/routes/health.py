import hashlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.services.extraction.cache_fingerprint import (
    runtime_extraction_cache_capability,
)


router = APIRouter(
    prefix="/health",
    tags=["Health Check"],
)


@router.get("")
def health_check() -> dict[str, str]:
    return {"status": "ok", "message": "API is running"}


@router.get("/extraction-cache")
def extraction_cache_health_check() -> dict[str, Any]:
    """Expose only version metadata so runners can reject a stale server."""
    return runtime_extraction_cache_capability()


@router.get("/customs-audit")
def customs_audit_health_check() -> dict[str, Any]:
    """Prove the configured live workflow can construct its real checkpointer.

    The credential digest is a short, one-way equality token. It lets the
    local live runner prove that its quota check and the API extraction process
    use the same high-entropy Groq credential without exposing the credential.
    """
    from app.core.config import get_settings
    from app.services.customs_audit.factory import get_app_customs_audit_service

    settings = get_settings()
    try:
        get_app_customs_audit_service()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The customs-audit workflow/checkpointer is unavailable.",
        ) from exc

    api_key = (
        settings.groq_api_key.get_secret_value()
        if settings.groq_api_key is not None
        else ""
    )
    credential_fingerprint = (
        "sha256:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
        if api_key
        else None
    )
    return {
        "status": "ok",
        "checkpoint_backend": settings.langgraph_checkpoint_backend.casefold(),
        "checkpoint_ready": True,
        "live_agents_enabled": settings.langgraph_enable_live_agents,
        "broker_model": settings.langgraph_broker_model or settings.groq_model,
        "auditor_model": settings.langgraph_auditor_model or settings.groq_model,
        "extraction_model": settings.groq_model,
        "human_review_required": settings.langgraph_human_review_required,
        "groq_credential_fingerprint": credential_fingerprint,
    }


@router.get("/database")
def database_health_check(
    db: Session = Depends(get_db_session),
) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The database is unavailable.",
        ) from exc
    return {"status": "ok", "database": "connected"}
