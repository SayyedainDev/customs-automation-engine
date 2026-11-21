"""Scenario evidence for resumable customs-audit human review and reprocessing."""

from __future__ import annotations

from datetime import date
from http import HTTPStatus
import json
from pathlib import Path
from uuid import uuid4

from app.schemas.customs_audit import (
    AuditEventsResponse,
    ReviewTaskResponse,
    StartWorkflowRequest,
    SubmitReviewRequest,
    WorkflowStatusResponse,
)
from app.schemas.documents import DocumentUploadResponse
from app.services.customs_audit.state import HumanAction, WorkflowStatus
from app.services.document_upload_service import ALLOWED_FILE_TYPES
from cace_testing.harnesses.database import CaceRuntime
from cace_testing.harnesses.fixtures_paths import synthetic_factory_dir


def _upload_pdf_path(runtime: CaceRuntime, pdf_path: Path) -> DocumentUploadResponse:
    pdf_bytes = pdf_path.read_bytes()
    resp = runtime.client.post(
        "/documents/upload",
        files={"file": (pdf_path.name, pdf_bytes, ALLOWED_FILE_TYPES[".pdf"])},
    )
    return DocumentUploadResponse.model_validate(resp.json())


def test_real_audit_workflow_interrupts_records_events_and_resumes_to_completion(
    cace_runtime: CaceRuntime,
) -> None:
    scenario_dir = synthetic_factory_dir() / "error_arithmetic_mismatch"
    invoice_doc = _upload_pdf_path(
        cace_runtime, scenario_dir / "synthetic_commercial_invoice_text.pdf"
    )
    packing_doc = _upload_pdf_path(
        cace_runtime, scenario_dir / "synthetic_packing_list_text.pdf"
    )
    fixture_request = json.loads(
        (scenario_dir / "multi_line_api_request.json").read_text(encoding="utf-8")
    )

    start_payload = StartWorkflowRequest(
        commercial_invoice_document_id=invoice_doc.document_id,
        packing_list_document_id=packing_doc.document_id,
        shipment_date=date.fromisoformat(str(fixture_request["shipment_date"])),
        additional_uploaded_document_types=list(
            fixture_request["additional_uploaded_document_types"]
        ),
    )
    start_resp = cace_runtime.client.post(
        "/api/v1/customs-audit/workflows",
        json=start_payload.model_dump(mode="json"),
    )
    assert start_resp.status_code == HTTPStatus.OK
    started = WorkflowStatusResponse.model_validate(start_resp.json())
    assert started.status == WorkflowStatus.AWAITING_HUMAN_REVIEW.value
    assert started.requires_human_review is True
    assert started.deterministic_status == "failed"

    review_resp = cace_runtime.client.get(
        f"/api/v1/customs-audit/workflows/{started.workflow_id}/review"
    )
    assert review_resp.status_code == HTTPStatus.OK
    review_task = ReviewTaskResponse.model_validate(review_resp.json())
    assert review_task.status == "open"

    events_resp = cace_runtime.client.get(
        f"/api/v1/customs-audit/workflows/{started.workflow_id}/events"
    )
    assert events_resp.status_code == HTTPStatus.OK
    events_model = AuditEventsResponse.model_validate(events_resp.json())
    assert len(events_model.events) > 0
    assert all(ev.new_state_hash for ev in events_model.events)

    missing_doc_review = SubmitReviewRequest(
        action=HumanAction.PROVIDE_MISSING_DOCUMENT.value,
        provided_document_ids=[str(uuid4())],
        reviewer_reference="reviewer-001",
    )
    rejected_missing = cace_runtime.client.post(
        f"/api/v1/customs-audit/workflows/{started.workflow_id}/review",
        json=missing_doc_review.model_dump(mode="json"),
    )
    assert rejected_missing.status_code == HTTPStatus.CONFLICT

    accept_review = SubmitReviewRequest(
        action=HumanAction.ACCEPT_MANUAL_REVIEW.value,
        reviewer_reference="reviewer-001",
        review_note="Verified offline against original file.",
    )
    resumed_resp = cace_runtime.client.post(
        f"/api/v1/customs-audit/workflows/{started.workflow_id}/review",
        json=accept_review.model_dump(mode="json"),
    )
    assert resumed_resp.status_code == HTTPStatus.OK
    resumed = WorkflowStatusResponse.model_validate(resumed_resp.json())
    assert resumed.status == WorkflowStatus.COMPLETED.value
    assert resumed.requires_human_review is False

    disallowed_retry = cace_runtime.client.post(
        f"/api/v1/customs-audit/workflows/{started.workflow_id}/retry"
    )
    assert disallowed_retry.status_code == HTTPStatus.CONFLICT


def test_unknown_workflow_queries_fail_cleanly_with_not_found(
    cace_runtime: CaceRuntime,
) -> None:
    missing_id = str(uuid4())
    assert (
        cace_runtime.client.get(
            f"/api/v1/customs-audit/workflows/{missing_id}"
        ).status_code
        == HTTPStatus.NOT_FOUND
    )
    assert (
        cace_runtime.client.get(
            f"/api/v1/customs-audit/workflows/{missing_id}/events"
        ).status_code
        == HTTPStatus.NOT_FOUND
    )
