from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.core.exceptions import StructuredExtractionProviderUnavailableError
from app.schemas.customs_audit import (
    AuditEventsResponse,
    ReviewTaskResponse,
    StartWorkflowRequest,
    SubmitReviewRequest,
    WorkflowStatusResponse,
)
from app.services.customs_audit.factory import get_app_customs_audit_service
from app.services.customs_audit.state import HumanAction, utcnow_iso
from app.services.customs_audit.workflow_service import (
    CustomsAuditService,
    WorkflowNotFoundError,
    WorkflowStateError,
)

router = APIRouter(prefix="/api/v1/customs-audit", tags=["Customs audit workflow"])


def get_customs_audit_service() -> CustomsAuditService:
    return get_app_customs_audit_service()


def _uuid_or_404(service: CustomsAuditService, db: Session, workflow_id: str):
    from uuid import UUID

    try:
        return UUID(workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="workflow not found") from exc


def _decision_from_request(payload: SubmitReviewRequest) -> dict[str, Any]:
    corrections: list[dict[str, Any]] = []
    if payload.action == HumanAction.CORRECT_EXTRACTED_VALUE.value and payload.field_path:
        corrections.append(
            {
                "field_path": payload.field_path,
                "original_value": payload.original_value,
                "corrected_value": payload.corrected_value,
                "reviewer_reference": payload.reviewer_reference,
                "reason": payload.reason or "human correction",
                "source": payload.source,
                "timestamp": utcnow_iso(),
            }
        )
    return {
        "action": payload.action,
        "corrections": corrections,
        "provided_document_ids": payload.provided_document_ids,
        "reviewer_reference": payload.reviewer_reference,
        "reason": payload.reason,
        "review_note": payload.review_note,
        "timestamp": utcnow_iso(),
    }


@router.post("/workflows", response_model=WorkflowStatusResponse)
async def start_workflow(
    payload: StartWorkflowRequest,
    db: Session = Depends(get_db_session),
    service: CustomsAuditService = Depends(get_customs_audit_service),
) -> WorkflowStatusResponse:
    request = {
        "commercial_invoice_document_id": payload.commercial_invoice_document_id,
        "packing_list_document_id": payload.packing_list_document_id,
        "additional_document_ids": payload.additional_document_ids,
        "shipment_date": payload.shipment_date.isoformat() if payload.shipment_date else None,
        "letter_of_credit_date": (
            payload.letter_of_credit_date.isoformat() if payload.letter_of_credit_date else None
        ),
        "additional_uploaded_document_types": payload.additional_uploaded_document_types,
        "supporting_documents": [
            reference.model_dump(mode="json") for reference in payload.supporting_documents
        ],
    }
    try:
        result = await service.start_workflow(db, request)
    except StructuredExtractionProviderUnavailableError as exc:
        # A live Broker/Auditor narration request that was refused is a blocked
        # workflow, not a successful deterministic fallback and not a defect in
        # the trader's documents.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The customs-audit model provider is unavailable or rate limited; "
                "the workflow did not complete."
            ),
        ) from exc
    return WorkflowStatusResponse.model_validate(result)


@router.get("/workflows/{workflow_id}", response_model=WorkflowStatusResponse)
async def get_workflow_status(
    workflow_id: str,
    db: Session = Depends(get_db_session),
    service: CustomsAuditService = Depends(get_customs_audit_service),
) -> WorkflowStatusResponse:
    wid = _uuid_or_404(service, db, workflow_id)
    try:
        return WorkflowStatusResponse.model_validate(service.get_status(db, wid))
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workflow not found") from exc


@router.get("/workflows/{workflow_id}/review", response_model=ReviewTaskResponse)
async def get_review_task(
    workflow_id: str,
    db: Session = Depends(get_db_session),
    service: CustomsAuditService = Depends(get_customs_audit_service),
) -> ReviewTaskResponse:
    wid = _uuid_or_404(service, db, workflow_id)
    task = service.get_review(db, wid)
    if task is None:
        raise HTTPException(status_code=404, detail="no open human-review task")
    return ReviewTaskResponse.model_validate(task)


@router.post("/workflows/{workflow_id}/review", response_model=WorkflowStatusResponse)
async def submit_review(
    workflow_id: str,
    payload: SubmitReviewRequest,
    db: Session = Depends(get_db_session),
    service: CustomsAuditService = Depends(get_customs_audit_service),
) -> WorkflowStatusResponse:
    wid = _uuid_or_404(service, db, workflow_id)
    try:
        result = await service.submit_review(db, wid, _decision_from_request(payload))
        return WorkflowStatusResponse.model_validate(result)
    except StructuredExtractionProviderUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The customs-audit model provider is unavailable or rate limited; "
                "the workflow did not complete."
            ),
        ) from exc
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workflow not found") from exc
    except WorkflowStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/events", response_model=AuditEventsResponse)
async def get_audit_events(
    workflow_id: str,
    db: Session = Depends(get_db_session),
    service: CustomsAuditService = Depends(get_customs_audit_service),
) -> AuditEventsResponse:
    wid = _uuid_or_404(service, db, workflow_id)
    try:
        events = service.get_events(db, wid)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workflow not found") from exc
    return AuditEventsResponse.model_validate({"workflow_id": workflow_id, "events": events})


@router.post("/workflows/{workflow_id}/retry", response_model=WorkflowStatusResponse)
async def retry_workflow(
    workflow_id: str,
    db: Session = Depends(get_db_session),
    service: CustomsAuditService = Depends(get_customs_audit_service),
) -> WorkflowStatusResponse:
    wid = _uuid_or_404(service, db, workflow_id)
    try:
        result = await service.retry(db, wid)
        return WorkflowStatusResponse.model_validate(result)
    except StructuredExtractionProviderUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The customs-audit model provider is unavailable or rate limited; "
                "the workflow did not complete."
            ),
        ) from exc
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workflow not found") from exc
    except WorkflowStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
