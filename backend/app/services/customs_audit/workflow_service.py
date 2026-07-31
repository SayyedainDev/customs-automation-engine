"""Customs-audit workflow orchestration service.

Bridges the async FastAPI routes and the (sync) LangGraph graph. The graph is
executed off the event loop via ``asyncio.to_thread``; graph nodes open their own
DB sessions through the injected ``session_factory``. This service persists the
queryable projection (workflow row, human-review task, immutable audit events)
into the project tables using the request-scoped session.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customs_audit import (
    CustomsAuditEvent,
    CustomsAuditWorkflow,
    CustomsHumanReviewTask,
)
from app.services.customs_audit.report import build_audit_report
from app.services.customs_audit.state import (
    HumanAction,
    HumanReviewDecision,
    WorkflowStatus,
    utcnow_iso,
)
from app.services.shipment_search.vector_store import index_shipment_summary

logger = logging.getLogger(__name__)

#: Advertised in the HumanAction enum (and validated structurally, so a
#: client cannot bypass them with an arbitrary string) but not implemented in
#: this prototype - document replacement requires a real upload workflow.
#: Rejected here, before the graph is ever invoked, rather than silently
#: "completing" a workflow that never actually attached a new document or
#: reprocessed anything.
_UNSUPPORTED_ACTIONS = frozenset(
    {HumanAction.PROVIDE_MISSING_DOCUMENT.value, HumanAction.REQUEST_REPROCESSING.value}
)


class WorkflowNotFoundError(Exception):
    pass


class WorkflowStateError(Exception):
    pass


class CustomsAuditService:
    def __init__(self, graph: Any):
        self._graph = graph

    # ---- graph execution helpers -------------------------------------- #
    def _config(self, thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    async def _invoke(self, graph_input: Any, thread_id: str) -> dict[str, Any]:
        # LangGraph's compiled graph is synchronous. Calling it directly avoids
        # a cross-thread event-loop wake-up failure observed in the local demo
        # runtime. The API method remains async for FastAPI compatibility.
        return self._graph.invoke(graph_input, self._config(thread_id))

    def _snapshot(self, thread_id: str) -> Any:
        return self._graph.get_state(self._config(thread_id))

    def _interrupt_payload(self, thread_id: str) -> dict[str, Any] | None:
        snapshot = self._snapshot(thread_id)
        interrupts = getattr(snapshot, "interrupts", None) or []
        if snapshot.next and interrupts:
            return interrupts[0].value
        return None

    # ---- public API --------------------------------------------------- #
    async def start_workflow(self, db: Session, request: dict[str, Any]) -> dict[str, Any]:
        workflow_id = uuid4()
        thread_id = f"customs-audit-{workflow_id.hex}"
        created_at = utcnow_iso()
        initial_state: dict[str, Any] = {
            "workflow_id": str(workflow_id),
            "review_revision_id": str(request.get("review_revision_id") or uuid4()),
            "thread_id": thread_id,
            "workflow_status": WorkflowStatus.CREATED.value,
            "created_at": created_at,
            "updated_at": created_at,
            "commercial_invoice_document_id": str(request["commercial_invoice_document_id"]),
            "packing_list_document_id": (
                str(request["packing_list_document_id"])
                if request.get("packing_list_document_id")
                else None
            ),
            "additional_document_ids": [str(x) for x in request.get("additional_document_ids", [])],
            "shipment_date": request.get("shipment_date"),
            "letter_of_credit_date": request.get("letter_of_credit_date"),
            "additional_uploaded_document_types": request.get("additional_uploaded_document_types", []),
            "supporting_documents": request.get("supporting_documents", []),
            "audit_events": [],
        }

        record = CustomsAuditWorkflow(
            id=workflow_id,
            thread_id=thread_id,
            status=WorkflowStatus.RUNNING.value,
            invoice_document_id=_as_uuid(request.get("commercial_invoice_document_id")),
            packing_list_document_id=_as_uuid(request.get("packing_list_document_id")),
        )
        db.add(record)
        db.commit()

        state = await self._invoke(initial_state, thread_id)
        self._sync(db, workflow_id, thread_id, state)
        return self._status_dict(db, workflow_id)

    async def submit_review(
        self, db: Session, workflow_id: UUID, decision: dict[str, Any]
    ) -> dict[str, Any]:
        from langgraph.types import Command

        workflow = self._get_workflow(db, workflow_id)
        if workflow.status != WorkflowStatus.AWAITING_HUMAN_REVIEW.value:
            raise WorkflowStateError(
                f"workflow is not awaiting human review (status={workflow.status})"
            )
        # Validate the human decision structurally.
        validated = HumanReviewDecision.model_validate(
            {**decision, "timestamp": decision.get("timestamp") or utcnow_iso()}
        )
        if validated.action.value in _UNSUPPORTED_ACTIONS:
            raise WorkflowStateError(
                f"the '{validated.action.value}' action is not implemented in this "
                "prototype (document replacement requires a real upload workflow); "
                "use confirm_extracted_value, correct_extracted_value, "
                "accept_manual_review or reject_submission"
            )
        state = await self._invoke(
            Command(resume=validated.model_dump(mode="json")), workflow.thread_id
        )
        # Resolve the open review task.
        task = self._open_task(db, workflow_id)
        if task is not None:
            task.status = "resolved"
            task.decision = validated.model_dump(mode="json")
            task.review_note = validated.review_note
            task.assigned_to = validated.reviewer_reference
            task.resolved_at = datetime.now(timezone.utc)
        self._sync(db, workflow_id, workflow.thread_id, state)
        return self._status_dict(db, workflow_id)

    async def retry(self, db: Session, workflow_id: UUID) -> dict[str, Any]:
        workflow = self._get_workflow(db, workflow_id)
        if workflow.status == WorkflowStatus.AWAITING_HUMAN_REVIEW.value:
            raise WorkflowStateError("retry cannot bypass a pending human-review requirement")
        if workflow.status != WorkflowStatus.FAILED.value:
            raise WorkflowStateError(f"only a technically-failed workflow can be retried (status={workflow.status})")

        # Re-drive from the start on a fresh thread, carrying prior audit history.
        old = self._snapshot(workflow.thread_id).values
        new_thread = f"{workflow.thread_id}-retry-{uuid4().hex[:6]}"
        initial_state = {
            "workflow_id": str(workflow_id),
            "review_revision_id": old.get("review_revision_id") or str(uuid4()),
            "thread_id": new_thread,
            "workflow_status": WorkflowStatus.RESUMING.value,
            "created_at": old.get("created_at") or utcnow_iso(),
            "updated_at": utcnow_iso(),
            "commercial_invoice_document_id": old.get("commercial_invoice_document_id"),
            "packing_list_document_id": old.get("packing_list_document_id"),
            "additional_document_ids": old.get("additional_document_ids", []),
            "shipment_date": old.get("shipment_date"),
            "letter_of_credit_date": old.get("letter_of_credit_date"),
            "additional_uploaded_document_types": old.get("additional_uploaded_document_types", []),
            "supporting_documents": old.get("supporting_documents", []),
            "audit_events": old.get("audit_events", []),
            "retry_count": int(old.get("retry_count", 0)) + 1,
            "deterministic_result_history": (
                old.get("deterministic_result_history") or []
            ),
            "human_correction_history": old.get("human_correction_history") or [],
            # Carried forward so an unchanged deterministic result reuses its
            # prior explanation (same fingerprint) instead of calling Groq
            # again on retry.
            "explanation_results": old.get("explanation_results", []),
        }
        workflow.thread_id = new_thread
        db.commit()
        state = await self._invoke(initial_state, new_thread)
        self._sync(db, workflow_id, new_thread, state)
        return self._status_dict(db, workflow_id)

    def get_status(self, db: Session, workflow_id: UUID) -> dict[str, Any]:
        self._get_workflow(db, workflow_id)
        return self._status_dict(db, workflow_id)

    def get_review(self, db: Session, workflow_id: UUID) -> dict[str, Any] | None:
        task = self._open_task(db, workflow_id)
        if task is None:
            return None
        workflow = self._get_workflow(db, workflow_id)
        # The full structured review task (review_task_id, revision_number,
        # reason_code, title, plain_language_question, disputed_field_details,
        # affected_check_ids) already lives durably in the checkpointed graph
        # state - the same interrupt payload interrupt_for_human_review built
        # - so it is read from there rather than duplicated into new SQL
        # columns. Falls back to the DB row's own fields if the checkpoint is
        # ever unavailable (never a hard failure to view an open review).
        live = self._interrupt_payload(workflow.thread_id) or {}
        return {
            "task_id": str(task.id),
            "workflow_id": str(task.workflow_id),
            "status": task.status,
            "reason": live.get("reason", task.reason),
            "disputed_fields": task.disputed_fields or [],
            "requested_actions": task.requested_actions or [],
            "evidence": task.evidence or [],
            "deterministic_status": task.deterministic_status,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "review_task_id": live.get("review_task_id"),
            "revision_number": live.get("revision_number"),
            "reason_code": live.get("reason_code"),
            "title": live.get("title"),
            "plain_language_question": live.get("plain_language_question"),
            "disputed_field_details": live.get("disputed_field_details", []),
            "affected_check_ids": live.get("affected_check_ids", []),
        }

    def get_events(self, db: Session, workflow_id: UUID) -> list[dict[str, Any]]:
        self._get_workflow(db, workflow_id)
        rows = db.execute(
            select(CustomsAuditEvent)
            .where(CustomsAuditEvent.workflow_id == workflow_id)
            .order_by(CustomsAuditEvent.created_at, CustomsAuditEvent.id)
        ).scalars()
        return [
            {
                "event_type": e.event_type,
                "node_name": e.node_name,
                "actor_type": e.actor_type,
                "actor_reference": e.actor_reference,
                "new_state_hash": e.new_state_hash,
                "event_payload": e.event_payload,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in rows
        ]

    # ---- internals ---------------------------------------------------- #
    def _get_workflow(self, db: Session, workflow_id: UUID) -> CustomsAuditWorkflow:
        workflow = db.get(CustomsAuditWorkflow, workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(str(workflow_id))
        return workflow

    def _open_task(self, db: Session, workflow_id: UUID) -> CustomsHumanReviewTask | None:
        return db.execute(
            select(CustomsHumanReviewTask)
            .where(
                CustomsHumanReviewTask.workflow_id == workflow_id,
                CustomsHumanReviewTask.status == "open",
            )
            .order_by(CustomsHumanReviewTask.created_at.desc())
        ).scalars().first()

    def _sync(self, db: Session, workflow_id: UUID, thread_id: str, state: dict[str, Any]) -> None:
        workflow = self._get_workflow(db, workflow_id)
        interrupt_payload = self._interrupt_payload(thread_id)
        snapshot = self._snapshot(thread_id)
        # The authoritative checkpointed state carries every prior node output,
        # so the user-facing report can be built even while paused for review.
        report_state = getattr(snapshot, "values", None) or state
        user_report = build_audit_report(report_state)

        if interrupt_payload is not None:
            deterministic = report_state.get("deterministic_compliance_result") or {}
            deterministic_history = (
                report_state.get("deterministic_result_history") or []
            )
            original_deterministic = (
                deterministic_history[0]
                if deterministic_history
                else deterministic
            )
            workflow.status = WorkflowStatus.AWAITING_HUMAN_REVIEW.value
            workflow.requires_human_review = True
            workflow.current_node = "interrupt_for_human_review"
            workflow.final_report = {
                "workflow_id": report_state.get("workflow_id"),
                "review_revision_id": report_state.get("review_revision_id"),
                "thread_id": report_state.get("thread_id"),
                "status": WorkflowStatus.AWAITING_HUMAN_REVIEW.value,
                "deterministic_compliance_status": deterministic.get(
                    "overall_status"
                ),
                "deterministic_result_current_version": deterministic.get(
                    "version"
                ),
                "original_deterministic_status": original_deterministic.get(
                    "overall_status"
                ),
                # These findings already live in the durable checkpoint. Expose
                # them while paused so operators and the live evaluator can see
                # exactly why consensus routed the case to a person.
                "broker_findings": report_state.get("broker_report"),
                "auditor_findings": report_state.get("auditor_report"),
                "consensus_result": report_state.get("consensus_result"),
                "user_report": user_report,
            }
        else:
            workflow.status = state.get("workflow_status") or workflow.status
            workflow.current_node = snapshot.next[0] if snapshot.next else "completed"
            # The workflow is no longer waiting on anyone. This flag was only
            # ever set, never cleared, so a completed workflow kept reporting
            # requires_human_review=True and the UI kept showing a review
            # prompt for a decision that had already been made. Whether a
            # review *happened* is recorded in the audit events and the
            # resolved task; this field means "is one outstanding now".
            workflow.requires_human_review = False
            if state.get("final_report") is not None:
                final_report = dict(state["final_report"])
                final_report["review_revision_id"] = state.get("review_revision_id")
                final_report["user_report"] = user_report
                workflow.final_report = final_report
                workflow.completed_at = datetime.now(timezone.utc)
                # Historical semantic search (Phase 9): index a deterministic
                # summary of the now-finalized shipment. Best-effort - an
                # embedding-provider hiccup must never fail audit persistence.
                try:
                    index_shipment_summary(db, workflow)
                except Exception:
                    logger.warning(
                        "Shipment-history indexing failed for workflow %s; the "
                        "compliance result is unaffected, but this shipment "
                        "will not be searchable until indexing succeeds.",
                        workflow.id,
                        exc_info=True,
                    )

        deterministic = state.get("deterministic_compliance_result") or {}
        workflow.deterministic_status = deterministic.get("overall_status")
        workflow.rule_data_version = state.get("rule_data_version")
        workflow.vector_index_version = state.get("vector_index_version")
        workflow.errors = state.get("errors") or None
        workflow.updated_at = datetime.now(timezone.utc)

        self._sync_events(db, workflow_id, state.get("audit_events", []))

        if interrupt_payload is not None and self._open_task(db, workflow_id) is None:
            db.add(
                CustomsHumanReviewTask(
                    id=uuid4(),
                    workflow_id=workflow_id,
                    status="open",
                    reason=interrupt_payload.get("reason", "human review required"),
                    disputed_fields=interrupt_payload.get("disputed_fields", []),
                    requested_actions=interrupt_payload.get("allowed_actions", []),
                    evidence=interrupt_payload.get("evidence_passages", []),
                    deterministic_status=interrupt_payload.get("deterministic_status"),
                )
            )
        db.commit()

    def _sync_events(self, db: Session, workflow_id: UUID, events: list[dict[str, Any]]) -> None:
        existing = db.execute(
            select(CustomsAuditEvent).where(CustomsAuditEvent.workflow_id == workflow_id)
        ).scalars().all()
        for event in events[len(existing):]:
            created_at = _parse_iso(event.get("created_at"))
            db.add(
                CustomsAuditEvent(
                    id=uuid4(),
                    workflow_id=workflow_id,
                    event_type=event.get("event_type", "unknown"),
                    node_name=event.get("node_name"),
                    actor_type=event.get("actor_type", "system"),
                    actor_reference=event.get("actor_reference"),
                    previous_state_hash=event.get("previous_state_hash"),
                    new_state_hash=event.get("new_state_hash"),
                    event_payload=event.get("event_payload"),
                    **({"created_at": created_at} if created_at else {}),
                )
            )

    def _status_dict(self, db: Session, workflow_id: UUID) -> dict[str, Any]:
        workflow = self._get_workflow(db, workflow_id)
        return {
            "workflow_id": str(workflow.id),
            "thread_id": workflow.thread_id,
            "status": workflow.status,
            "current_node": workflow.current_node,
            "deterministic_status": workflow.deterministic_status,
            "requires_human_review": workflow.requires_human_review,
            "rule_data_version": workflow.rule_data_version,
            "vector_index_version": workflow.vector_index_version,
            "final_report": workflow.final_report,
            "errors": workflow.errors,
            "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
            "completed_at": workflow.completed_at.isoformat() if workflow.completed_at else None,
            "review_revision_id": (
                (workflow.final_report or {}).get("review_revision_id")
                if workflow.final_report
                else None
            ),
        }


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
