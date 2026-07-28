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
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

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
from app.services.customs_audit.dependency_map import (
    InvalidFieldPathError,
    resolve_affected_checks,
)
from app.services.customs_audit.deps import WorkflowDeps
from app.services.customs_audit.evidence import (
    document_evidence_for_check,
    evidence_status_for_regulatory,
    is_regulatory_check,
    is_system_scope_check,
    normalize_regulatory_evidence,
    system_scope_statement,
)
from app.services.customs_audit.explanation import generate_explanation_entry
from app.services.customs_audit.query import query_for_check
from app.services.customs_audit.safety import (
    detect_injection,
    validate_auditor_report,
    validate_broker_report,
)
from app.services.customs_audit.state import (
    CORRECTABLE_BASES,
    MAX_HUMAN_REVIEW_ROUNDS,
    ActorType,
    AuditorReport,
    AuditRevision,
    BrokerReport,
    CorrectionBasis,
    DisputedFieldDetail,
    HumanAction,
    HumanCorrection,
    HumanReviewRequest,
    ProvenanceLabel,
    WorkflowStatus,
    utcnow_iso,
)
from app.services.multi_line_shipment_service import (
    CorrectionValidationError,
    FieldCorrection,
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


# --------------------------------------------------------------------------- #
# Targeted human correction: field-level dispute detection and check-id
# bookkeeping shared by interrupt_for_human_review and apply_human_correction.
# --------------------------------------------------------------------------- #
def _all_check_ids(extraction_result: dict[str, Any]) -> list[str]:
    """Every check_id currently on this shipment, including data-driven
    executable-rule checks (``xr_*``) - the regulatory-family expansion in
    the dependency map needs the *real* rule ids, not just the Python-coded
    checks."""
    ids: list[str] = [
        str(c.get("check_id")) for c in extraction_result.get("shipment_level_checks") or []
    ]
    for item in extraction_result.get("items") or []:
        for check in item.get("item_checks") or []:
            ids.append(str(check.get("check_id")))
        compliance = item.get("compliance") or {}
        for check in compliance.get("checks") or []:
            ids.append(str(check.get("check_id")))
        for check in compliance.get("executable_rule_checks") or []:
            ids.append(str(check.get("check_id")))
    return list(dict.fromkeys(ids))


#: Only the item-comparison checks map to a single, human-confirmable value
#: on each side (invoice vs packing list) - a missing-document or regulatory
#: check is a different kind of problem (see PROVIDE_MISSING_DOCUMENT) and is
#: deliberately not offered as a "disputed field" here.
_FIELD_NAME_BY_CHECK = {
    "item_quantity_match": "quantity",
    "item_net_weight_match": "net_weight",
    "item_gross_weight_match": "gross_weight",
    "item_pct_code_match": "pct_code",
}
_REASON_CODE_BY_CHECK = {
    "item_quantity_match": "quantity_mismatch",
    "item_net_weight_match": "net_weight_mismatch",
    "item_gross_weight_match": "gross_weight_mismatch",
    "item_pct_code_match": "pct_code_mismatch",
}
_REVIEW_TITLE_BY_REASON = {
    "quantity_mismatch": "Confirm shipment quantity",
    "net_weight_mismatch": "Confirm net weight",
    "gross_weight_mismatch": "Confirm gross weight",
    "pct_code_mismatch": "Confirm product code",
}


def _item_index_by_reference(
    extraction_result: dict[str, Any], item_reference: str | None
) -> tuple[int | None, int | None]:
    for item in extraction_result.get("items") or []:
        if item.get("item_reference") == item_reference:
            return item.get("invoice_item_index"), item.get("packing_item_index")
    return None, None


#: Below this confidence, the extractor itself is not sure of the value -
#: that is the software's own uncertainty, not a claim about what the
#: document says, so a human resolving it is correcting an *extraction*
#: problem. Matches the qualitative gap already used elsewhere in this
#: codebase between a comfortably-read field and a shaky one.
_LOW_CONFIDENCE_THRESHOLD = Decimal("0.90")


def _field_correction_basis(field: dict[str, Any]) -> str:
    """Classify *why* one side of a disputed field might be wrong.

    Deliberately conservative: anything that does not clearly indicate the
    software's own uncertainty (a confident, verified, non-OCR reading)
    defaults to ``CONFIRMED_DOCUMENT_MISMATCH`` - fail closed, never assume a
    value is correctable just because a dispute exists.
    """
    method = str(field.get("extraction_method") or "")
    validation_status = field.get("validation_status")
    raw_confidence = field.get("confidence")
    try:
        confidence = Decimal(str(raw_confidence)) if raw_confidence is not None else None
    except (InvalidOperation, ValueError):
        confidence = None

    if field.get("value") is None or method == "not_extracted_ocr_required":
        return CorrectionBasis.PARSER_ERROR.value

    is_uncertain = validation_status == "manual_review" or (
        confidence is not None and confidence < _LOW_CONFIDENCE_THRESHOLD
    )
    if "tesseract_ocr" in method and is_uncertain:
        return CorrectionBasis.AMBIGUOUS_OCR.value
    if is_uncertain:
        return CorrectionBasis.LOW_CONFIDENCE_EXTRACTION.value
    if confidence is None and validation_status != "verified":
        return CorrectionBasis.HUMAN_CONFIRMATION_REQUIRED.value
    # Confident, verified, not OCR-derived: the document itself clearly and
    # unambiguously contains this value - only a corrected document (not a
    # rewritten stored value) can resolve a disagreement here.
    return CorrectionBasis.CONFIRMED_DOCUMENT_MISMATCH.value


def _disputed_field_details_for_check(
    check: dict[str, Any], item_reference: str | None, extraction_result: dict[str, Any]
) -> list[dict[str, Any]]:
    """The invoice-side and packing-side values behind one failed/uncertain
    item-comparison check, each traceable to a document, page, confidence
    and *why* it may be wrong - the precise thing a reviewer is being asked
    to look at, and what later gates whether a correction to it is safe."""
    field_name = _FIELD_NAME_BY_CHECK.get(str(check.get("check_id") or ""))
    if field_name is None or item_reference is None:
        return []
    invoice_index, packing_index = _item_index_by_reference(extraction_result, item_reference)
    details: list[dict[str, Any]] = []
    for doc_key, document_type, items_key, path_root, item_index in (
        ("invoice", "commercial_invoice", "line_items", "invoice.line_items", invoice_index),
        ("packing_list", "packing_list", "items", "packing_list.items", packing_index),
    ):
        if item_index is None:
            continue
        document = extraction_result.get(doc_key) or {}
        item_data = next(
            (i for i in document.get(items_key) or [] if i.get("item_index") == item_index),
            None,
        )
        if item_data is None:
            continue
        field = item_data.get(field_name) or {}
        details.append(
            {
                "field_path": f"{path_root}[{item_index}].{field_name}",
                "document_type": document_type,
                "document_id": None,
                "page": field.get("source_page"),
                "value": field.get("value"),
                "confidence": field.get("confidence"),
                "extraction_method": field.get("extraction_method"),
                "correction_basis": _field_correction_basis(field),
            }
        )
    return details


def _review_title(reason_code: str) -> str:
    return _REVIEW_TITLE_BY_REASON.get(reason_code, "Human review required")


def _plain_language_question(reason_code: str, details: list[dict[str, Any]]) -> str:
    if len(details) >= 2 and reason_code in _REVIEW_TITLE_BY_REASON:
        first, second = details[0], details[1]
        return (
            f"The {first['document_type'].replace('_', ' ')} says {first['value']}, "
            f"while the {second['document_type'].replace('_', ' ')} says {second['value']}. "
            "Which value matches the physical shipment?"
        )
    return "A value could not be safely confirmed automatically. Please review the flagged item."


def _review_task_id(state: dict[str, Any], revision_number: int) -> str:
    """Deterministic, not ``uuid4()``: LangGraph re-runs a node's body from
    the top when it resumes past an ``interrupt()`` call inside it, so
    anything computed before that call must be a pure function of
    (unchanged) state - a random id here would differ between what the
    reviewer was shown and what gets recorded after resume."""
    round_number = int(state.get("human_review_round", 0)) + 1
    seed = f"{state.get('workflow_id')}:{revision_number}:{round_number}"
    return "review-" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def _build_review_task(state: dict[str, Any]) -> HumanReviewRequest:
    consensus = state.get("consensus_result") or {}
    extraction_result = state.get("extraction_result") or {}
    revision_number = len(state.get("deterministic_result_history") or []) or 1

    disputed_details: list[dict[str, Any]] = []
    affected_check_ids: list[str] = []
    reason_code = "manual_review_required"
    present_check_ids = _all_check_ids(extraction_result)
    for item in extraction_result.get("items") or []:
        reference = item.get("item_reference")
        for check in item.get("item_checks") or []:
            if check.get("status") not in ("failed", "manual_review"):
                continue
            details = _disputed_field_details_for_check(check, reference, extraction_result)
            if not details:
                continue
            if not disputed_details:
                reason_code = _REASON_CODE_BY_CHECK.get(
                    str(check.get("check_id") or ""), reason_code
                )
            disputed_details.extend(details)
            for detail in details:
                try:
                    dependency = resolve_affected_checks(detail["field_path"])
                except (InvalidFieldPathError, KeyError):
                    continue
                for check_id in dependency.resolve_check_ids(present_check_ids):
                    if check_id not in affected_check_ids:
                        affected_check_ids.append(check_id)

    return HumanReviewRequest(
        reason=consensus.get("reason", "human review required"),
        disputed_fields=list(
            dict.fromkeys(
                consensus.get("disagreed_fields", []) + list(state.get("manual_review_reasons", []))
            )
        ),
        source_document_pages=[
            {
                "document_type": r.get("document_type"),
                "page_number": r.get("page_number"),
                "requires_ocr": r.get("requires_ocr"),
            }
            for r in extraction_result.get("page_reviews", [])
        ],
        deterministic_status=consensus.get("deterministic_status"),
        evidence_passages=state.get("retrieved_evidence", [])[:5],
        # PROVIDE_MISSING_DOCUMENT / REQUEST_REPROCESSING are deliberately
        # excluded here: document replacement is not implemented in this
        # prototype, and workflow_service.submit_review() explicitly rejects
        # them if a client submits them anyway - never silently "completing"
        # as though a new document had actually been attached.
        allowed_actions=[
            HumanAction.CONFIRM_EXTRACTED_VALUE,
            HumanAction.CORRECT_EXTRACTED_VALUE,
            HumanAction.ACCEPT_MANUAL_REVIEW,
            HumanAction.REJECT_SUBMISSION,
            HumanAction.ADD_REVIEW_NOTE,
        ],
        created_at=utcnow_iso(),
        review_status="open",
        review_task_id=_review_task_id(state, revision_number),
        revision_number=revision_number,
        reason_code=reason_code,
        title=_review_title(reason_code),
        plain_language_question=_plain_language_question(reason_code, disputed_details),
        disputed_field_details=[DisputedFieldDetail(**d) for d in disputed_details],
        affected_check_ids=affected_check_ids,
    )


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
            "triggered_by": "initial",
        }
        events = [
            _event(
                event_type="deterministic_status_frozen",
                node_name="deterministic_compliance",
                actor=ActorType.SYSTEM,
                payload={"overall_status": frozen["overall_status"]},
            )
        ]
        # Revision 1 begins here, at the same instant the original status is
        # frozen - not retroactively, whenever a correction happens to be
        # submitted later (or never, if none ever is). The Broker report is
        # already available (broker_agent runs before this node); the
        # Auditor and consensus are not produced until after this node, so
        # this revision starts "provisional" (frozen=False) and is completed
        # by compare_agent_reports's controlled completion step below -
        # never edited again after that.
        provisional_revision = AuditRevision(
            revision_number=1,
            frozen=False,
            frozen_at=frozen["frozen_at"],
            triggered_by="initial",
            deterministic_result=frozen,
            broker_report=state.get("broker_report"),
            auditor_report=None,
            consensus_result=None,
        )
        events.append(
            _event(
                event_type="audit_revision_frozen",
                node_name="deterministic_compliance",
                actor=ActorType.SYSTEM,
                payload={"version": 1, "overall_status": frozen["overall_status"], "provisional": True},
            )
        )
        return {
            "deterministic_compliance_result": frozen,
            "deterministic_result_history": _append(state, "deterministic_result_history", [frozen]),
            "audit_revisions": _append(state, "audit_revisions", [provisional_revision.model_dump(mode="json")]),
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def _gather_evidence_and_audit(
        extraction_result: dict[str, Any],
        broker_report_dict: dict[str, Any],
        *,
        reuse_regulatory_evidence_by_check: dict[str, list[dict[str, Any]]] | None = None,
        reuse_regulatory_evidence_status_by_check: dict[str, str] | None = None,
        regulatory_checks_to_requery: set[str] | None = None,
    ) -> dict[str, Any]:
        """Evidence-gathering + independent Auditor re-derivation, shared by
        the first pass (auditor_agent) and a post-correction recheck
        (auditor_recheck_revision).

        When ``regulatory_checks_to_requery`` is given (the correction path),
        a regulatory check_id *not* in that set reuses its prior citation
        from ``reuse_regulatory_evidence_by_check`` instead of calling RAG
        again - "do not rerun RAG for a correction that did not change the
        regulatory context". When it is ``None`` (the first pass), every
        regulatory check is queried, exactly as before this helper existed.
        """
        deterministic_status = str(extraction_result.get("overall_status"))
        broker = BrokerReport.model_validate(broker_report_dict or {})

        # Gather evidence for every check the deterministic engine produced -
        # passed, failed, and manual_review alike. Regulatory checks (ones
        # citing a government source/SRO) are backed by a live RAG retrieval
        # regardless of status, so "a certificate of origin is not required
        # here" is cited exactly like "it is required"; missing/failed
        # document-comparison checks (quantity, weight, PCT match) are backed
        # by the extracted values themselves - never a retrieval, because
        # there is nothing to retrieve for a fact already read off the
        # uploaded documents.
        checks_with_context: list[tuple[dict[str, Any], str | None]] = [
            (check, None) for check in extraction_result.get("shipment_level_checks", [])
        ]
        for item in extraction_result.get("items", []):
            reference = item.get("item_reference")
            for check in (item.get("compliance") or {}).get("checks", []):
                checks_with_context.append((check, reference))
            for check in item.get("item_checks", []):
                checks_with_context.append((check, reference))

        evidence_by_check: dict[str, list[dict[str, Any]]] = {}
        regulatory_evidence_by_check: dict[str, list[dict[str, Any]]] = {}
        regulatory_evidence_status_by_check: dict[str, str] = {}
        document_evidence_by_check: dict[str, list[dict[str, Any]]] = {}
        system_scope_statements_by_check: dict[str, str] = {}
        all_evidence: list[dict[str, Any]] = []
        reused_check_ids: list[str] = []
        with deps.session_factory() as db:
            for check, item_reference in checks_with_context:
                check_id = str(check.get("check_id"))
                if check.get("status") == "not_applicable":
                    # A rule that does not apply to this shipment (e.g. a
                    # licence requirement for a different product category)
                    # has nothing to cite or compare - retrieving evidence
                    # for it would waste a lookup and show a meaningless
                    # "no evidence found" chip for a requirement that was
                    # never in play.
                    continue
                if is_system_scope_check(check):
                    # "PCT code supported by the MVP" is a statement about
                    # this software's own configured scope, not a government
                    # requirement - it must never trigger a regulatory
                    # lookup or be presented as a legal citation.
                    if check_id not in system_scope_statements_by_check:
                        system_scope_statements_by_check[check_id] = system_scope_statement(
                            check
                        )
                elif is_regulatory_check(check):
                    if check_id in evidence_by_check:
                        continue
                    if (
                        regulatory_checks_to_requery is not None
                        and check_id not in regulatory_checks_to_requery
                        and reuse_regulatory_evidence_by_check is not None
                        and check_id in reuse_regulatory_evidence_by_check
                    ):
                        regulatory_evidence_by_check[check_id] = (
                            reuse_regulatory_evidence_by_check[check_id]
                        )
                        regulatory_evidence_status_by_check[check_id] = (
                            (reuse_regulatory_evidence_status_by_check or {}).get(
                                check_id, "evidence_unavailable"
                            )
                        )
                        reused_check_ids.append(check_id)
                        continue
                    query = query_for_check(check, extraction_result)
                    raw_evidence = deps.evidence_provider(db, check.get("pct_code"), query)
                    evidence_by_check[check_id] = raw_evidence
                    regulatory_evidence_by_check[check_id] = normalize_regulatory_evidence(
                        raw_evidence
                    )
                    regulatory_evidence_status_by_check[check_id] = (
                        evidence_status_for_regulatory(raw_evidence)
                    )
                    all_evidence.extend(raw_evidence)
                elif check_id not in document_evidence_by_check:
                    document_evidence_by_check[check_id] = document_evidence_for_check(
                        check, extraction_result, item_reference=item_reference
                    )

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
        return {
            "auditor_report": report,
            "all_evidence": all_evidence,
            "regulatory_evidence_by_check": regulatory_evidence_by_check,
            "regulatory_evidence_status_by_check": regulatory_evidence_status_by_check,
            "document_evidence_by_check": document_evidence_by_check,
            "system_scope_statements_by_check": system_scope_statements_by_check,
            "violations": violations,
            "injection_hits": injection_hits,
            "reused_regulatory_check_ids": reused_check_ids,
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

        result = _gather_evidence_and_audit(extraction_result, state.get("broker_report") or {})
        report = result["auditor_report"]
        violations = result["violations"]

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
        if result["injection_hits"]:
            events.append(
                _event(
                    event_type="injection_detected_in_evidence",
                    node_name="auditor_agent",
                    actor=ActorType.SYSTEM,
                    payload={
                        "phrases": result["injection_hits"],
                        "handling": "treated_as_data_not_obeyed",
                    },
                )
            )
        return {
            "auditor_report": report.model_dump(mode="json"),
            "retrieved_evidence": result["all_evidence"],
            "regulatory_evidence_by_check": result["regulatory_evidence_by_check"],
            "regulatory_evidence_status_by_check": result["regulatory_evidence_status_by_check"],
            "document_evidence_by_check": result["document_evidence_by_check"],
            "system_scope_statements_by_check": result["system_scope_statements_by_check"],
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

        # Controlled completion of revision 1: the Auditor and consensus are
        # only available now (this node runs after auditor_agent). This is
        # the one and only place revision 1's provisional entry (see
        # deterministic_compliance) is ever updated - it is complete and
        # frozen=True before interrupt_for_human_review can possibly run, so
        # a human correction never needs to (and never does) create it.
        existing_revisions = list(state.get("audit_revisions") or [])
        audit_revisions = existing_revisions
        if existing_revisions and existing_revisions[0].get("revision_number") == 1:
            provisional = existing_revisions[0]
            completed_revision = AuditRevision(
                revision_number=1,
                frozen=True,
                frozen_at=provisional.get("frozen_at") or utcnow_iso(),
                triggered_by="initial",
                deterministic_result=provisional.get("deterministic_result") or {},
                broker_report=state.get("broker_report"),
                auditor_report=state.get("auditor_report"),
                consensus_result=consensus_obj.model_dump(mode="json"),
            )
            audit_revisions = [completed_revision.model_dump(mode="json"), *existing_revisions[1:]]
            events.append(
                _event(
                    event_type="audit_revision_frozen",
                    node_name="compare_agent_reports",
                    actor=ActorType.SYSTEM,
                    payload={"version": 1, "overall_status": deterministic_status, "provisional": False},
                )
            )

        return {
            "consensus_result": consensus_obj.model_dump(mode="json"),
            "manual_review_reasons": _append(state, "manual_review_reasons", reasons),
            "audit_revisions": audit_revisions,
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def interrupt_for_human_review(state: dict[str, Any]) -> dict[str, Any]:
        request = _build_review_task(state)
        events = [
            _event(
                event_type="human_review_task_created",
                node_name="interrupt_for_human_review",
                actor=ActorType.SYSTEM,
                payload={
                    "review_task_id": request.review_task_id,
                    "revision_number": request.revision_number,
                    "reason_code": request.reason_code,
                    "affected_check_ids": request.affected_check_ids,
                },
            ),
            _event(
                event_type="workflow_interrupted",
                node_name="interrupt_for_human_review",
                actor=ActorType.SYSTEM,
                payload={"review_task_id": request.review_task_id},
            ),
        ]
        # Pause here. On resume, `decision` is the submitted human decision dict.
        decision = interrupt(request.model_dump(mode="json"))
        return {
            "workflow_status": WorkflowStatus.RESUMING.value,
            "human_review_request": request.model_dump(mode="json"),
            "human_review_decision": decision,
            "human_review_round": state.get("human_review_round", 0) + 1,
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def human_decision_received(state: dict[str, Any]) -> dict[str, Any]:
        decision = state.get("human_review_decision") or {}
        action = decision.get("action")
        events = [
            _event(
                event_type="human_decision_received",
                node_name="human_decision_received",
                actor=ActorType.HUMAN,
                actor_reference=decision.get("reviewer_reference"),
                payload={"action": action},
            ),
            _event(
                event_type="human_response_received",
                node_name="human_decision_received",
                actor=ActorType.HUMAN,
                actor_reference=decision.get("reviewer_reference"),
                payload={
                    "action": action,
                    "review_task_id": (state.get("human_review_request") or {}).get(
                        "review_task_id"
                    ),
                },
            ),
        ]
        # A confirm/correct action's corrections are validated and applied by
        # apply_human_correction (which appends the richer, dependency-mapped
        # record); recording the raw submitted payload here too would create
        # a duplicate, unvalidated entry in the same history list.
        return {
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def resume_workflow(state: dict[str, Any]) -> dict[str, Any]:
        """The legacy-actions path: reject / accept-manual-review / add-note.
        PROVIDE_MISSING_DOCUMENT / REQUEST_REPROCESSING never reach the graph
        at all (workflow_service.submit_review() rejects them first). A
        confirm/correct action never reaches this node either -
        route_human_action sends it to apply_human_correction instead."""
        decision = state.get("human_review_decision") or {}
        action = decision.get("action")
        updates: dict[str, Any] = {"updated_at": utcnow_iso()}
        events: list[dict[str, Any]] = []

        if action == HumanAction.REJECT_SUBMISSION.value:
            updates["workflow_status"] = WorkflowStatus.REJECTED.value
            # Kept separate from deterministic_compliance_result.overall_status,
            # which a human disposition never overwrites - see build_final_report.
            updates["human_disposition"] = "rejected_current_submission"
            events.append(_event(event_type="submission_rejected", node_name="resume_workflow", actor=ActorType.HUMAN, actor_reference=decision.get("reviewer_reference")))
            updates["audit_events"] = _append(state, "audit_events", events)
            return updates

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

    # ----------------------------------------------------------------- #
    # Targeted correction path: confirm_extracted_value / correct_extracted_value.
    # ----------------------------------------------------------------- #
    def apply_human_correction(state: dict[str, Any]) -> dict[str, Any]:
        decision = state.get("human_review_decision") or {}
        review_task = state.get("human_review_request") or {}
        reviewer = decision.get("reviewer_reference")
        raw_corrections = decision.get("corrections") or []
        extraction_result = state.get("extraction_result") or {}
        revision_from = len(state.get("deterministic_result_history") or []) or 1
        disputed_details_by_path = {
            d.get("field_path"): d for d in review_task.get("disputed_field_details") or []
        }
        present_check_ids = _all_check_ids(extraction_result)

        #: The exact response required when a reviewer attempts to overwrite
        #: a value the uploaded document itself states clearly and
        #: confidently: the disagreement is between two documents, not a
        #: software reading error, so no stored value may be edited to make
        #: it go away. Document replacement is not implemented in this
        #: prototype, so the workflow simply finalizes in whatever
        #: deterministic status the engine already reached (see
        #: build_final_report) - no new status is invented.
        _DOCUMENT_CONFLICT_MESSAGE = (
            "The uploaded document itself contains the conflicting value. "
            "Upload a corrected document and run the audit again."
        )

        def _fail(reason: str, *, event_type: str = "correction_validation_failed") -> dict[str, Any]:
            events = [
                _event(
                    event_type=event_type,
                    node_name="apply_human_correction",
                    actor=ActorType.SYSTEM,
                    actor_reference=reviewer,
                    payload={"reason": reason},
                )
            ]
            return {
                "correction_validation_errors": _append(
                    state, "correction_validation_errors", [reason]
                ),
                "correction_applied": False,
                "audit_events": _append(state, "audit_events", events),
                "updated_at": utcnow_iso(),
            }

        if not raw_corrections:
            return _fail("no corrections were submitted with a confirm/correct action")

        field_corrections: list[FieldCorrection] = []
        correction_records: list[dict[str, Any]] = []
        all_affected_check_ids: list[str] = []
        regulatory_context_changed = False
        for raw in raw_corrections:
            field_path = raw.get("field_path")
            detail = disputed_details_by_path.get(field_path)
            if disputed_details_by_path and detail is None:
                return _fail(f"field_path not part of the active review task: {field_path}")
            if detail is not None:
                basis = str(detail.get("correction_basis") or CorrectionBasis.CONFIRMED_DOCUMENT_MISMATCH.value)
                if basis not in CORRECTABLE_BASES:
                    return _fail(
                        _DOCUMENT_CONFLICT_MESSAGE, event_type="correction_rejected_document_conflict"
                    )
            if not raw.get("reason"):
                return _fail(f"a reason is required to correct {field_path}")
            try:
                dependency = resolve_affected_checks(field_path)
            except (InvalidFieldPathError, KeyError) as exc:
                return _fail(f"{field_path}: {exc}")

            resolved_ids = dependency.resolve_check_ids(present_check_ids)
            for check_id in resolved_ids:
                if check_id not in all_affected_check_ids:
                    all_affected_check_ids.append(check_id)
            regulatory_context_changed = (
                regulatory_context_changed or dependency.regulatory_context_changed
            )
            field_corrections.append(
                FieldCorrection(
                    field_path=field_path,
                    corrected_value=raw.get("corrected_value"),
                    reason=str(raw.get("reason")),
                )
            )
            correction_records.append(
                HumanCorrection(
                    field_path=field_path,
                    original_value=raw.get("original_value"),
                    corrected_value=raw.get("corrected_value"),
                    reviewer_reference=raw.get("reviewer_reference") or reviewer or "unknown",
                    reason=str(raw.get("reason")),
                    source=raw.get("source"),
                    timestamp=raw.get("timestamp") or utcnow_iso(),
                    correction_id=str(uuid4()),
                    review_task_id=review_task.get("review_task_id"),
                    revision_from=revision_from,
                    source_document_id=raw.get("source_document_id"),
                    source_page=raw.get("source_page"),
                    affected_check_ids=resolved_ids,
                ).model_dump(mode="json")
            )

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
        assert deps.recheck_pipeline is not None, "recheck_pipeline dependency is required"
        try:
            new_extraction_result = deps.recheck_pipeline(
                extraction_result, request, field_corrections
            )
        except CorrectionValidationError as exc:
            return _fail(str(exc))

        # Revision 1 already exists, complete and frozen, by the time this
        # node can ever run: deterministic_compliance opens it and
        # compare_agent_reports completes it, and both always run before
        # interrupt_for_human_review - which is the only way a correction
        # decision reaches this node at all. It is never created here.
        audit_revisions = list(state.get("audit_revisions") or [])
        assert audit_revisions and audit_revisions[0].get("revision_number") == 1, (
            "revision 1 must already be frozen before a human correction can be applied"
        )

        events = [
            _event(
                event_type="human_correction_applied",
                node_name="apply_human_correction",
                actor=ActorType.HUMAN,
                actor_reference=reviewer,
                payload={
                    "correction_count": len(field_corrections),
                    "field_paths": [c.field_path for c in field_corrections],
                },
            ),
            _event(
                event_type="affected_checks_identified",
                node_name="apply_human_correction",
                actor=ActorType.SYSTEM,
                payload={
                    "affected_check_ids": all_affected_check_ids,
                    "regulatory_context_changed": regulatory_context_changed,
                },
            ),
            _event(
                event_type="checks_recomputed",
                node_name="apply_human_correction",
                actor=ActorType.SYSTEM,
                payload={
                    "new_overall_status": new_extraction_result.get("overall_status"),
                    "affected_check_ids": all_affected_check_ids,
                },
            ),
        ]
        return {
            "extraction_result": new_extraction_result,
            "matched_items": new_extraction_result.get("items", []),
            "cross_document_checks": new_extraction_result.get("shipment_level_checks", []),
            "human_correction_history": _append(
                state, "human_correction_history", correction_records
            ),
            "audit_revisions": audit_revisions,
            "affected_check_ids": all_affected_check_ids,
            "correction_applied": True,
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def freeze_corrected_revision(state: dict[str, Any]) -> dict[str, Any]:
        extraction_result = state.get("extraction_result") or {}
        history = state.get("deterministic_result_history") or []
        frozen = {
            "version": len(history) + 1,
            "source": "deterministic_engine",
            "overall_status": extraction_result.get("overall_status"),
            "is_compliant": extraction_result.get("is_compliant"),
            "rule_data_version": extraction_result.get("rule_data_version"),
            "item_statuses": [
                {"item_reference": i.get("item_reference"), "status": i.get("status")}
                for i in extraction_result.get("items", [])
            ],
            "frozen_at": utcnow_iso(),
            "triggered_by": "human_correction",
        }
        events = [
            _event(
                event_type="audit_revision_frozen",
                node_name="freeze_corrected_revision",
                actor=ActorType.SYSTEM,
                payload={"version": frozen["version"], "overall_status": frozen["overall_status"]},
            )
        ]
        return {
            "deterministic_compliance_result": frozen,
            "deterministic_result_history": _append(state, "deterministic_result_history", [frozen]),
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def auditor_recheck_revision(state: dict[str, Any]) -> dict[str, Any]:
        """Full independent re-derivation against the corrected data (see
        _gather_evidence_and_audit's docstring for why this is a full rerun,
        not a partial one) - RAG is only re-queried for checks the
        correction is known to have changed the regulatory context for."""
        extraction_result = state.get("extraction_result") or {}
        deterministic_status = str(extraction_result.get("overall_status"))

        # The Broker is also rerun (not just the Auditor): compute_consensus's
        # first check compares broker.observed_deterministic_status against
        # the new status, and a stale Broker report would always disagree
        # with a status it never actually observed.
        broker_report = deps.broker_agent.build_report(extraction_result, deterministic_status)
        broker_violations = validate_broker_report(broker_report, deterministic_status)

        affected = set(state.get("affected_check_ids") or [])
        result = _gather_evidence_and_audit(
            extraction_result,
            broker_report.model_dump(mode="json"),
            reuse_regulatory_evidence_by_check=state.get("regulatory_evidence_by_check"),
            reuse_regulatory_evidence_status_by_check=state.get(
                "regulatory_evidence_status_by_check"
            ),
            regulatory_checks_to_requery=affected,
        )
        auditor_report = result["auditor_report"]

        events = [
            _event(
                event_type="auditor_revision_reviewed",
                node_name="auditor_recheck_revision",
                actor=ActorType.AUDITOR,
                payload={
                    "revision_reviewed": (state.get("deterministic_compliance_result") or {}).get(
                        "version"
                    ),
                    "observed_deterministic_status": auditor_report.observed_deterministic_status,
                    "recommended_action": auditor_report.recommended_workflow_action.value,
                    "reused_regulatory_evidence_for": result["reused_regulatory_check_ids"],
                    "violations": result["violations"] + broker_violations,
                },
            )
        ]
        return {
            "broker_report": broker_report.model_dump(mode="json"),
            "auditor_report": auditor_report.model_dump(mode="json"),
            "retrieved_evidence": result["all_evidence"],
            "regulatory_evidence_by_check": result["regulatory_evidence_by_check"],
            "regulatory_evidence_status_by_check": result["regulatory_evidence_status_by_check"],
            "document_evidence_by_check": result["document_evidence_by_check"],
            "system_scope_statements_by_check": result["system_scope_statements_by_check"],
            "critical_anomalies": _append(
                state,
                "critical_anomalies",
                ["auditor_output_violation"] if result["violations"] else [],
            ),
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

    def recompute_consensus_after_correction(state: dict[str, Any]) -> dict[str, Any]:
        extraction_result = state.get("extraction_result") or {}
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

        round_number = int(state.get("human_review_round", 0))
        limit_reached = (
            consensus_obj.requires_human_review and round_number >= MAX_HUMAN_REVIEW_ROUNDS
        )

        events = [
            _event(
                event_type="consensus_recomputed",
                node_name="recompute_consensus_after_correction",
                actor=ActorType.SYSTEM,
                payload={
                    "consensus_reached": consensus_obj.consensus_reached,
                    "requires_human_review": consensus_obj.requires_human_review,
                    "human_review_round": round_number,
                },
            )
        ]
        if limit_reached:
            events.append(
                _event(
                    event_type="human_review_limit_reached",
                    node_name="recompute_consensus_after_correction",
                    actor=ActorType.SYSTEM,
                    payload={
                        "human_review_round": round_number,
                        "max_human_review_rounds": MAX_HUMAN_REVIEW_ROUNDS,
                    },
                )
            )

        # Append the now-complete revision bundle (deterministic + broker +
        # auditor + consensus, all for the same corrected data) - revision 1
        # was already captured retroactively in apply_human_correction.
        deterministic = state.get("deterministic_compliance_result") or {}
        audit_revisions = list(state.get("audit_revisions") or [])
        audit_revisions.append(
            AuditRevision(
                revision_number=deterministic.get("version") or (len(audit_revisions) + 1),
                frozen_at=deterministic.get("frozen_at") or utcnow_iso(),
                triggered_by="human_correction",
                correction_id=(
                    (state.get("human_correction_history") or [{}])[-1].get("correction_id")
                ),
                deterministic_result=deterministic,
                broker_report=state.get("broker_report"),
                auditor_report=state.get("auditor_report"),
                consensus_result=consensus_obj.model_dump(mode="json"),
            ).model_dump(mode="json")
        )

        return {
            "consensus_result": consensus_obj.model_dump(mode="json"),
            "manual_review_reasons": _append(state, "manual_review_reasons", reasons),
            "audit_revisions": audit_revisions,
            "audit_events": _append(state, "audit_events", events),
            "updated_at": utcnow_iso(),
        }

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
            "regulatory_evidence_by_check": state.get("regulatory_evidence_by_check", {}),
            "regulatory_evidence_status_by_check": state.get(
                "regulatory_evidence_status_by_check", {}
            ),
            "document_evidence_by_check": state.get("document_evidence_by_check", {}),
            "system_scope_statements_by_check": state.get(
                "system_scope_statements_by_check", {}
            ),
            "explanation_results": state.get("explanation_results", []),
            "human_decisions": state.get("human_correction_history", []),
            "human_review_decision": state.get("human_review_decision"),
            "audit_revisions": state.get("audit_revisions", []),
            "human_disposition": state.get("human_disposition"),
            "correction_validation_errors": state.get("correction_validation_errors", []),
            "human_review_round": state.get("human_review_round", 0),
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
                "explanation_rejection_reason": None,
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
        final_report["explanation_rejection_reason"] = entry.get(
            "explanation_rejection_reason"
        )
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
        "apply_human_correction": apply_human_correction,
        "freeze_corrected_revision": freeze_corrected_revision,
        "auditor_recheck_revision": auditor_recheck_revision,
        "recompute_consensus_after_correction": recompute_consensus_after_correction,
        "build_final_report": build_final_report,
        "generate_explanation": generate_explanation,
        "persist_audit_record": persist_audit_record,
    }


def route_after_compare(state: dict[str, Any]) -> str:
    consensus = state.get("consensus_result") or {}
    if consensus.get("requires_human_review"):
        return "interrupt_for_human_review"
    return "build_final_report"


def route_human_action(state: dict[str, Any]) -> str:
    """confirm/correct go through the targeted-correction chain; every other
    action (reject, accept, note, provide-document, reprocess) keeps the
    existing resume_workflow behaviour unchanged."""
    decision = state.get("human_review_decision") or {}
    action = decision.get("action")
    if action in (
        HumanAction.CORRECT_EXTRACTED_VALUE.value,
        HumanAction.CONFIRM_EXTRACTED_VALUE.value,
    ):
        return "apply_human_correction"
    return "resume_workflow"


def route_after_apply_correction(state: dict[str, Any]) -> str:
    """A correction that failed validation never reaches the recompute
    chain - it goes straight to the final report with the prior revision's
    status untouched (see apply_human_correction's ``_fail`` helper)."""
    if state.get("correction_applied"):
        return "freeze_corrected_revision"
    return "build_final_report"


def route_after_correction_consensus(state: dict[str, Any]) -> str:
    """Loop back for another review only while genuinely uncertain and the
    round cap has not been reached (MAX_HUMAN_REVIEW_ROUNDS) - otherwise the
    workflow finalizes in whatever state the deterministic engine and
    consensus actually produced."""
    consensus = state.get("consensus_result") or {}
    round_number = int(state.get("human_review_round", 0))
    if consensus.get("requires_human_review") and round_number < MAX_HUMAN_REVIEW_ROUNDS:
        return "interrupt_for_human_review"
    return "build_final_report"
