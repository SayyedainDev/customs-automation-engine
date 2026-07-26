"""Graph nodes for the customs-audit workflow.

Each node is a closure over the injected ``WorkflowDeps``. Nodes only read/write
the JSON-serializable graph state and append audit events to it; project-table
persistence and DB-side audit writes happen in the workflow service after each
graph step. The deterministic compliance result is frozen once and never changed
by any downstream node.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

from langgraph.types import interrupt

from app.core.exceptions import (
    DocumentNotFoundError,
    ShipmentExtractionInputError,
    StoredDocumentNotFoundError,
    StructuredExtractionProviderError,
    StructuredExtractionProviderUnavailableError,
)
from app.schemas.multi_line_extraction import MultiLineShipmentRequest
from app.services.customs_audit.consensus import compute_consensus
from app.services.customs_audit.deps import WorkflowDeps
from app.services.customs_audit.explanation import generate_explanation_entry
from app.services.customs_audit.query import query_for_check
from app.services.customs_audit.safety import (
    detect_injection,
    validate_auditor_report,
    validate_broker_report,
)
from app.services.customs_audit.state import (
    ActorType,
    AuditorReport,
    BrokerReport,
    HumanAction,
    HumanReviewRequest,
    ProvenanceLabel,
    WorkflowStatus,
    utcnow_iso,
)

NodeFn = Callable[[dict[str, Any]], dict[str, Any]]


def _state_hash(state: dict[str, Any]) -> str:
    keys = ("deterministic_compliance_result", "broker_report", "auditor_report", "consensus_result")
    payload = {k: state.get(k) for k in keys}
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:32]


def _event(
    *,
    event_type: str,
    node_name: str,
    actor: ActorType,
    payload: dict[str, Any] | None = None,
    actor_reference: str | None = None,
    new_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "node_name": node_name,
        "actor_type": actor.value,
        "actor_reference": actor_reference,
        "new_state_hash": new_hash,
        "event_payload": payload or {},
        "created_at": utcnow_iso(),
    }


def _append(state: dict[str, Any], key: str, items: list[Any]) -> list[Any]:
    return list(state.get(key) or []) + items


def make_nodes(deps: WorkflowDeps) -> dict[str, NodeFn]:
    def load_shipment_documents(state: dict[str, Any]) -> dict[str, Any]:
        events = [
            _event(
                event_type="workflow_started",
                node_name="load_shipment_documents",
                actor=ActorType.SYSTEM,
                payload={"invoice": state.get("commercial_invoice_document_id")},
            )
        ]
        with deps.session_factory() as db:
            rule_version = deps.rule_data_version_fn()
            try:
                vector_version = deps.vector_index_version_fn(db)
            except Exception:
                vector_version = None
        return {
            "workflow_status": WorkflowStatus.RUNNING.value,
            "updated_at": utcnow_iso(),
            "rule_data_version": rule_version,
            "vector_index_version": vector_version,
            "audit_events": _append(state, "audit_events", events),
        }

    def broker_agent(state: dict[str, Any]) -> dict[str, Any]:
        request = MultiLineShipmentRequest(
            commercial_invoice_document_id=UUID(state["commercial_invoice_document_id"]),
            packing_list_document_id=(
                UUID(state["packing_list_document_id"])
                if state.get("packing_list_document_id")
                else None
            ),
            shipment_date=state.get("shipment_date"),
            letter_of_credit_date=state.get("letter_of_credit_date"),
            additional_uploaded_document_types=state.get("additional_uploaded_document_types", []),
            supporting_documents=state.get("supporting_documents", []),
        )
        try:
            with deps.session_factory() as db:
                extraction_result = deps.run_pipeline(db, request)
        except StructuredExtractionProviderUnavailableError:
            # A refused provider request assessed no document and no shipment.
            # Let the HTTP boundary return 503 instead of checkpointing a
            # document-related workflow outcome.
            raise
        except (
            DocumentNotFoundError,
            StoredDocumentNotFoundError,
            ShipmentExtractionInputError,
            StructuredExtractionProviderError,
        ) as exc:
            error = {"node": "broker_agent", "type": type(exc).__name__, "message": str(exc)}
            events = [
                _event(
                    event_type="technical_failure",
                    node_name="broker_agent",
                    actor=ActorType.SYSTEM,
                    payload=error,
                )
            ]
            return {
                "extraction_result": None,
                "errors": _append(state, "errors", [error]),
                "critical_anomalies": _append(state, "critical_anomalies", ["technical_extraction_failure"]),
                "manual_review_reasons": _append(state, "manual_review_reasons", ["technical extraction failure"]),
                "audit_events": _append(state, "audit_events", events),
                "updated_at": utcnow_iso(),
            }

        deterministic_status = str(extraction_result.get("overall_status"))
        injection_hits = detect_injection(json.dumps(extraction_result, default=str))
        try:
            report = deps.broker_agent.build_report(extraction_result, deterministic_status)
            violations = validate_broker_report(report, deterministic_status)
        except StructuredExtractionProviderUnavailableError:
            raise
        except Exception as exc:  # agent output schema/validation failure
            report = BrokerReport(observed_deterministic_status=deterministic_status)
            violations = [f"broker_schema_validation_failure: {exc}"]
        report_dict = report.model_dump(mode="json")

        events = [
            _event(
                event_type="broker_report_created",
                node_name="broker_agent",
                actor=ActorType.BROKER,
                payload={"report_confidence": report.report_confidence, "violations": violations},
            )
        ]
        if injection_hits:
            events.append(
                _event(
                    event_type="injection_detected_in_document",
                    node_name="broker_agent",
                    actor=ActorType.SYSTEM,
                    payload={"phrases": injection_hits, "handling": "treated_as_data_not_obeyed"},
                )
            )
        items = extraction_result.get("items", [])
        destination = (
            ((extraction_result.get("invoice") or {}).get("destination_country") or {}).get("value")
        )
        return {
            "extraction_result": extraction_result,
            "destination_country": destination,
            "page_reviews": extraction_result.get("page_reviews", []),
            "matched_items": items,
            "cross_document_checks": extraction_result.get("shipment_level_checks", []),
            "shipment_inputs": [
                {"item_reference": i.get("item_reference"), "shipment_input": i.get("shipment_input")}
                for i in items
            ],
            "broker_report": report_dict,
            "critical_anomalies": _append(
                state, "critical_anomalies", ["broker_output_violation"] if violations else []
            ),
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def deterministic_compliance(state: dict[str, Any]) -> dict[str, Any]:
        extraction_result = state.get("extraction_result")
        if not extraction_result:
            return {
                "deterministic_compliance_result": None,
                "audit_events": _append(
                    state, "audit_events",
                    [_event(event_type="deterministic_unavailable", node_name="deterministic_compliance", actor=ActorType.SYSTEM)],
                ),
            }
        frozen = {
            "version": 1,
            "source": "deterministic_engine",
            "overall_status": extraction_result.get("overall_status"),
            "is_compliant": extraction_result.get("is_compliant"),
            "rule_data_version": extraction_result.get("rule_data_version"),
            "item_statuses": [
                {"item_reference": i.get("item_reference"), "status": i.get("status")}
                for i in extraction_result.get("items", [])
            ],
            "frozen_at": utcnow_iso(),
        }
        events = [
            _event(
                event_type="deterministic_status_frozen",
                node_name="deterministic_compliance",
                actor=ActorType.SYSTEM,
                payload={"overall_status": frozen["overall_status"]},
            )
        ]
        return {
            "deterministic_compliance_result": frozen,
            "deterministic_result_history": _append(state, "deterministic_result_history", [frozen]),
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def auditor_agent(state: dict[str, Any]) -> dict[str, Any]:
        extraction_result = state.get("extraction_result")
        if not extraction_result:
            auditor = AuditorReport(
                compliance_status_confirmed=False,
                recommended_workflow_action="technical_failure",  # type: ignore[arg-type]
                critical_anomalies=["technical_extraction_failure"],
                observed_deterministic_status=None,
            )
            return {
                "auditor_report": auditor.model_dump(mode="json"),
                "audit_events": _append(
                    state, "audit_events",
                    [_event(event_type="auditor_skipped", node_name="auditor_agent", actor=ActorType.AUDITOR)],
                ),
            }

        deterministic_status = str(extraction_result.get("overall_status"))
        broker = BrokerReport.model_validate(state.get("broker_report") or {})

        # Gather evidence for failed / manual_review government checks.
        evidence_by_check: dict[str, list[dict[str, Any]]] = {}
        all_evidence: list[dict[str, Any]] = []
        checks = list(extraction_result.get("shipment_level_checks", []))
        for item in extraction_result.get("items", []):
            for check in (item.get("compliance") or {}).get("checks", []):
                checks.append(check)
            checks.extend(item.get("item_checks", []))
        with deps.session_factory() as db:
            for check in checks:
                if check.get("status") not in {"failed", "manual_review"}:
                    continue
                if not check.get("source_document") and not check.get("sro_number"):
                    continue
                check_id = str(check.get("check_id"))
                if check_id in evidence_by_check:
                    continue
                query = query_for_check(check, extraction_result)
                evidence = deps.evidence_provider(db, check.get("pct_code"), query)
                evidence_by_check[check_id] = evidence
                all_evidence.extend(evidence)

        try:
            report = deps.auditor_agent.build_report(
                broker, extraction_result, deterministic_status, evidence_by_check
            )
            retrieved_sros = {e.get("sro_number") for e in all_evidence if e.get("sro_number")}
            violations = validate_auditor_report(report, deterministic_status, retrieved_sros)  # type: ignore[arg-type]
        except StructuredExtractionProviderUnavailableError:
            raise
        except Exception as exc:  # agent output schema/validation failure
            report = AuditorReport(
                compliance_status_confirmed=True,
                observed_deterministic_status=deterministic_status,
                critical_anomalies=["auditor_schema_validation_failure"],
            )
            violations = [f"auditor_schema_validation_failure: {exc}"]
        injection_hits = detect_injection(
            " ".join(str(e.get("evidence_text", "")) for e in all_evidence)
        )

        events = [
            _event(
                event_type="auditor_report_created",
                node_name="auditor_agent",
                actor=ActorType.AUDITOR,
                payload={
                    "recommended_action": report.recommended_workflow_action.value,
                    "evidence_support": report.evidence_support_status,
                    "violations": violations,
                },
            )
        ]
        if injection_hits:
            events.append(
                _event(
                    event_type="injection_detected_in_evidence",
                    node_name="auditor_agent",
                    actor=ActorType.SYSTEM,
                    payload={"phrases": injection_hits, "handling": "treated_as_data_not_obeyed"},
                )
            )
        return {
            "auditor_report": report.model_dump(mode="json"),
            "retrieved_evidence": all_evidence,
            "critical_anomalies": _append(
                state, "critical_anomalies", ["auditor_output_violation"] if violations else []
            ),
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def compare_agent_reports(state: dict[str, Any]) -> dict[str, Any]:
        extraction_result = state.get("extraction_result")
        if not extraction_result:
            # Technical failure: terminal FAILED state (retryable), not a human
            # review. Retry re-drives it; it never bypasses a human-review gate.
            consensus = {
                "consensus_reached": False,
                "requires_human_review": False,
                "reason": "technical extraction failure; no deterministic result",
                "deterministic_status": None,
                "agreed_fields": [],
                "disagreed_fields": ["deterministic_status"],
                "unresolved_issues": ["technical_extraction_failure"],
            }
            return {
                "consensus_result": consensus,
                "manual_review_reasons": _append(state, "manual_review_reasons", ["technical extraction failure"]),
                "audit_events": _append(
                    state, "audit_events",
                    [_event(event_type="consensus_computed", node_name="compare_agent_reports", actor=ActorType.SYSTEM, payload={"requires_human_review": False, "technical_failure": True})],
                ),
            }

        broker = BrokerReport.model_validate(state.get("broker_report") or {})
        auditor = AuditorReport.model_validate(state.get("auditor_report") or {})
        deterministic_status = str(extraction_result.get("overall_status"))
        consensus_obj = compute_consensus(
            broker, auditor, deterministic_status,
            human_review_required=deps.human_review_required,
        )
        reasons = list(consensus_obj.unresolved_issues)
        if deterministic_status == "manual_review":
            reasons.append("deterministic status is manual_review")
        events = [
            _event(
                event_type="consensus_computed",
                node_name="compare_agent_reports",
                actor=ActorType.SYSTEM,
                payload={
                    "consensus_reached": consensus_obj.consensus_reached,
                    "requires_human_review": consensus_obj.requires_human_review,
                },
                new_hash=_state_hash(state),
            )
        ]
        return {
            "consensus_result": consensus_obj.model_dump(mode="json"),
            "manual_review_reasons": _append(state, "manual_review_reasons", reasons),
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def interrupt_for_human_review(state: dict[str, Any]) -> dict[str, Any]:
        consensus = state.get("consensus_result") or {}
        extraction_result = state.get("extraction_result") or {}
        request = HumanReviewRequest(
            reason=consensus.get("reason", "human review required"),
            disputed_fields=list(dict.fromkeys(
                consensus.get("disagreed_fields", []) + list(state.get("manual_review_reasons", []))
            )),
            source_document_pages=[
                {"document_type": r.get("document_type"), "page_number": r.get("page_number"), "requires_ocr": r.get("requires_ocr")}
                for r in extraction_result.get("page_reviews", [])
            ],
            deterministic_status=consensus.get("deterministic_status"),
            evidence_passages=state.get("retrieved_evidence", [])[:5],
            allowed_actions=[
                HumanAction.CONFIRM_EXTRACTED_VALUE,
                HumanAction.CORRECT_EXTRACTED_VALUE,
                HumanAction.PROVIDE_MISSING_DOCUMENT,
                HumanAction.ACCEPT_MANUAL_REVIEW,
                HumanAction.REQUEST_REPROCESSING,
                HumanAction.REJECT_SUBMISSION,
                HumanAction.ADD_REVIEW_NOTE,
            ],
            created_at=utcnow_iso(),
            review_status="open",
        )
        # Pause here. On resume, `decision` is the submitted human decision dict.
        decision = interrupt(request.model_dump(mode="json"))
        return {
            "workflow_status": WorkflowStatus.RESUMING.value,
            "human_review_request": request.model_dump(mode="json"),
            "human_review_decision": decision,
            "updated_at": utcnow_iso(),
        }

    def human_decision_received(state: dict[str, Any]) -> dict[str, Any]:
        decision = state.get("human_review_decision") or {}
        events = [
            _event(
                event_type="human_decision_received",
                node_name="human_decision_received",
                actor=ActorType.HUMAN,
                actor_reference=decision.get("reviewer_reference"),
                payload={"action": decision.get("action")},
            )
        ]
        corrections = decision.get("corrections", [])
        history = _append(state, "human_correction_history", corrections)
        return {
            "human_correction_history": history,
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def resume_workflow(state: dict[str, Any]) -> dict[str, Any]:
        decision = state.get("human_review_decision") or {}
        action = decision.get("action")
        updates: dict[str, Any] = {"updated_at": utcnow_iso()}
        events: list[dict[str, Any]] = []

        if action == HumanAction.REJECT_SUBMISSION.value:
            updates["workflow_status"] = WorkflowStatus.REJECTED.value
            events.append(_event(event_type="submission_rejected", node_name="resume_workflow", actor=ActorType.HUMAN, actor_reference=decision.get("reviewer_reference")))
            updates["audit_events"] = _append(state, "audit_events", events)
            return updates

        if action == HumanAction.CORRECT_EXTRACTED_VALUE.value and decision.get("corrections"):
            # The correction is preserved in human_correction_history, but the
            # frozen status cannot be recomputed safely from item inputs alone:
            # that would discard shipment-level and supporting-document checks.
            # Keep the authoritative result until a full pipeline rerun can
            # apply the correction to every deterministic check.
            deterministic = state.get("deterministic_compliance_result") or {}
            events.append(
                _event(
                    event_type="human_correction_recorded_status_preserved",
                    node_name="resume_workflow",
                    actor=ActorType.SYSTEM,
                    payload={
                        "correction_count": len(decision["corrections"]),
                        "preserved_version": deterministic.get("version"),
                        "preserved_status": deterministic.get("overall_status"),
                        "reason": "full authoritative pipeline rerun required",
                    },
                )
            )
        else:
            events.append(
                _event(
                    event_type="human_action_recorded",
                    node_name="resume_workflow",
                    actor=ActorType.HUMAN,
                    actor_reference=decision.get("reviewer_reference"),
                    payload={"action": action},
                )
            )
        updates["audit_events"] = _append(state, "audit_events", events)
        return updates

    def build_final_report(state: dict[str, Any]) -> dict[str, Any]:
        deterministic = state.get("deterministic_compliance_result")
        history = state.get("deterministic_result_history") or []
        original = history[0] if history else deterministic
        rejected = (state.get("workflow_status") == WorkflowStatus.REJECTED.value)
        technical_failure = deterministic is None and not rejected
        final = {
            "workflow_id": state.get("workflow_id"),
            "thread_id": state.get("thread_id"),
            "document_ids": {
                "commercial_invoice": state.get("commercial_invoice_document_id"),
                "packing_list": state.get("packing_list_document_id"),
                "additional": state.get("additional_document_ids", []),
            },
            "shipment_summary": {
                "destination_country": state.get("destination_country"),
                "shipment_date": state.get("shipment_date"),
                "item_count": len(state.get("matched_items", [])),
            },
            "extracted_items": [
                {"item_reference": i.get("item_reference"), "product_name": i.get("product_name"), "pct_code": i.get("pct_code"), "provenance": "machine_extracted/llm_structured/ocr"}
                for i in state.get("matched_items", [])
            ],
            "broker_findings": state.get("broker_report"),
            "auditor_findings": state.get("auditor_report"),
            "consensus_result": state.get("consensus_result"),
            "deterministic_compliance_status": (
                "rejected" if rejected else ("technical_failure" if technical_failure else (deterministic or {}).get("overall_status"))
            ),
            "deterministic_result_current_version": (deterministic or {}).get("version"),
            "original_deterministic_status": (original or {}).get("overall_status"),
            "individual_compliance_checks_label": "deterministic_check",
            "retrieved_legal_evidence": state.get("retrieved_evidence", []),
            "explanation_results": state.get("explanation_results", []),
            "human_decisions": state.get("human_correction_history", []),
            "human_review_decision": state.get("human_review_decision"),
            "unresolved_issues": (state.get("consensus_result") or {}).get("unresolved_issues", []),
            "manual_review_reasons": state.get("manual_review_reasons", []),
            "rule_data_version": state.get("rule_data_version"),
            "vector_index_version": state.get("vector_index_version"),
            "workflow_history": [
                {"event_type": e.get("event_type"), "node_name": e.get("node_name"), "actor_type": e.get("actor_type"), "created_at": e.get("created_at")}
                for e in state.get("audit_events", [])
            ],
            "provenance_legend": [label.value for label in ProvenanceLabel],
            "created_at": state.get("created_at"),
            "completed_at": utcnow_iso(),
        }
        if rejected:
            status = WorkflowStatus.REJECTED.value
        elif technical_failure:
            status = WorkflowStatus.FAILED.value
        else:
            status = WorkflowStatus.COMPLETED.value
        events = [_event(event_type="final_report_built", node_name="build_final_report", actor=ActorType.SYSTEM, payload={"status": status})]
        return {
            "final_report": final,
            "workflow_status": status,
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def generate_explanation(state: dict[str, Any]) -> dict[str, Any]:
        final_report = dict(state.get("final_report") or {})
        if state.get("workflow_status") in {
            WorkflowStatus.REJECTED.value,
            WorkflowStatus.FAILED.value,
        }:
            entry = {
                "explanation": (
                    "No explanation was generated: the workflow ended without "
                    "a deterministic compliance verdict."
                ),
                "explanation_source": "template_fallback",
                "fingerprint": None,
                "cache_hit": False,
            }
        else:
            entry = generate_explanation_entry(
                state=state,
                final_report=final_report,
                narrator=deps.explanation_narrator,
                model_label=deps.explanation_model_label,
            )
        explanation_results = _append(state, "explanation_results", [entry])
        final_report["explanation"] = entry["explanation"]
        final_report["explanation_source"] = entry["explanation_source"]
        final_report["explanation_results"] = explanation_results
        events = [
            _event(
                event_type="explanation_generated",
                node_name="generate_explanation",
                actor=ActorType.SYSTEM,
                payload={
                    "source": entry["explanation_source"],
                    "cache_hit": entry.get("cache_hit", False),
                },
            )
        ]
        return {
            "explanation_results": explanation_results,
            "final_report": final_report,
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def persist_audit_record(state: dict[str, Any]) -> dict[str, Any]:
        events = [
            _event(
                event_type="workflow_finalized",
                node_name="persist_audit_record",
                actor=ActorType.SYSTEM,
                new_hash=_state_hash(state),
                payload={"status": state.get("workflow_status")},
            )
        ]
        return {
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    return {
        "load_shipment_documents": load_shipment_documents,
        "broker_agent": broker_agent,
        "deterministic_compliance": deterministic_compliance,
        "auditor_agent": auditor_agent,
        "compare_agent_reports": compare_agent_reports,
        "interrupt_for_human_review": interrupt_for_human_review,
        "human_decision_received": human_decision_received,
        "resume_workflow": resume_workflow,
        "build_final_report": build_final_report,
        "generate_explanation": generate_explanation,
        "persist_audit_record": persist_audit_record,
    }


def route_after_compare(state: dict[str, Any]) -> str:
    consensus = state.get("consensus_result") or {}
    if consensus.get("requires_human_review"):
        return "interrupt_for_human_review"
    return "build_final_report"
