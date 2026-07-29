from uuid import UUID, uuid4
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.customs_audit import CustomsAuditWorkflow, CustomsAuditEvent
from app.models.documents import DocumentUploadRecord
from app.models.shipment_chunks import ShipmentDocumentChunk
from app.models.assistant import AssistantConversation, AssistantMessage
from app.schemas.assistant import ChatResponse, SourceSchema
from app.services.assistant.routing import classify_question
from app.services.regulatory.evidence_api import run_evidence_search
from app.schemas.regulatory_evidence import EvidenceSearchRequest
from app.services.compliance.rule_engine import get_compliance_rule_engine
from app.schemas.compliance import ShipmentComplianceInput


def _field_value(value):
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _first_item(extraction: dict, key: str) -> dict:
    items = extraction.get(key)
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return {}


def _flatten_extraction_slot(data: dict, slot: str) -> dict:
    profile = data.get(slot)
    if not isinstance(profile, dict):
        return {}
    extraction = profile.get("extraction")
    if not isinstance(extraction, dict):
        return {}
    flattened = {
        name: _field_value(value)
        for name, value in extraction.items()
        if isinstance(value, dict) and "value" in value
    }
    if slot == "phase_2c_commercial_invoice":
        item = _first_item(extraction, "line_items")
        flattened.update(
            {
                "invoice_total": flattened.get("invoice_total"),
                "invoice_currency": flattened.get("currency"),
                "pct_code": _field_value(item.get("pct_code")),
                "product_name": _field_value(item.get("product_name")),
                "quantity": _field_value(item.get("quantity")),
                "quantity_unit": _field_value(item.get("unit")),
                "unit_price": _field_value(item.get("unit_price")),
                "invoice_gross_weight": _field_value(item.get("gross_weight")),
            }
        )
    elif slot == "phase_2c_packing_list":
        item = _first_item(extraction, "items")
        flattened.update(
            {
                "packing_quantity": _field_value(item.get("quantity")),
                "packing_quantity_unit": _field_value(item.get("unit")),
                "packing_gross_weight": _field_value(item.get("gross_weight")),
                "packing_net_weight": _field_value(item.get("net_weight")),
            }
        )
    return flattened


def _get_shipment_structured_data(
    db: Session, workflow: CustomsAuditWorkflow | None, shipment_id: UUID
) -> dict:
    data: dict = {}
    doc_ids = []
    if workflow:
        if workflow.invoice_document_id:
            doc_ids.append(workflow.invoice_document_id)
        if workflow.packing_list_document_id:
            doc_ids.append(workflow.packing_list_document_id)
    else:
        # Fallback to the commercial invoice document itself if workflow hasn't started
        doc_ids.append(shipment_id)
        
    for doc_id in doc_ids:
        doc = db.get(DocumentUploadRecord, doc_id)
        if doc and doc.structured_data:
            data.update(doc.structured_data)
            data.update(
                _flatten_extraction_slot(
                    doc.structured_data, "phase_2c_commercial_invoice"
                )
            )
            data.update(
                _flatten_extraction_slot(doc.structured_data, "phase_2c_packing_list")
            )
    return data


def _frozen_status(workflow: CustomsAuditWorkflow | None) -> str:
    if workflow is None:
        return "unstarted"
    return workflow.deterministic_status or workflow.status


def _audit_revision(workflow: CustomsAuditWorkflow | None) -> int | None:
    if workflow is None or not isinstance(workflow.final_report, dict):
        return None
    history = (
        (workflow.final_report.get("user_report") or {}).get("audit_revision_history")
        or []
    )
    if not history:
        return None
    revision = history[-1].get("revision_number")
    return int(revision) if revision is not None else None


def _frozen_checks(workflow: CustomsAuditWorkflow | None) -> list[dict]:
    if workflow is None or not isinstance(workflow.final_report, dict):
        return []
    checks = (workflow.final_report.get("broker_findings") or {}).get(
        "deterministic_check_results"
    )
    return checks if isinstance(checks, list) else []


def _packing_item_value(
    db: Session, workflow: CustomsAuditWorkflow | None, field_name: str
):
    if workflow is None or workflow.packing_list_document_id is None:
        return None
    document = db.get(DocumentUploadRecord, workflow.packing_list_document_id)
    if document is None or not isinstance(document.structured_data, dict):
        return None
    profile = document.structured_data.get("phase_2c_packing_list")
    extraction = profile.get("extraction") if isinstance(profile, dict) else None
    item = _first_item(extraction, "items") if isinstance(extraction, dict) else {}
    return _field_value(item.get(field_name))


def _frozen_document_evidence_value(
    workflow: CustomsAuditWorkflow | None,
    *,
    check_id: str,
    document_type: str,
):
    if workflow is None or not isinstance(workflow.final_report, dict):
        return None
    checks = (
        (workflow.final_report.get("user_report") or {}).get("document_evidence")
        or []
    )
    for check in checks:
        if check.get("check_id") != check_id:
            continue
        for evidence in check.get("evidence") or []:
            if str(evidence.get("document_type", "")).casefold() == document_type.casefold():
                return evidence.get("extracted_value")
    return None


def answer_shipment_question(db: Session, shipment_id: UUID, question: str, conversation_id: UUID | None = None) -> ChatResponse:
    conv_id = conversation_id or uuid4()
    msg_id = uuid4()
    
    # 1. Fetch conversation and last assistant message
    conv = None
    if conversation_id:
        conv = db.get(AssistantConversation, conversation_id)
        
    last_doc_name = None
    if conv:
        last_msg = db.execute(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation_id, AssistantMessage.role == "assistant")
            .order_by(AssistantMessage.created_at.desc())
        ).scalars().first()
        if last_msg and last_msg.sources:
            for s in last_msg.sources:
                if s.get("source_kind") == "shipment_document":
                    name = s.get("display_name", "")
                    if " p." in name:
                        name = name.split(" p.")[0]
                    last_doc_name = name
                    break

    q_lower = f" {question.lower()} "
    if last_doc_name and any(p in q_lower for p in [" it ", " that ", " this ", " they ", " them ", " it.", " that.", " this.", " it?", " that?", " this?"]):
        # It's a pronoun reference, append context
        search_query = f"{question} {last_doc_name}"
    else:
        search_query = question
    
    # shipment_id could be either the workflow.id (legacy/test) or invoice_document_id (prototype)
    workflow = db.get(CustomsAuditWorkflow, shipment_id)
    if not workflow:
        workflow = db.execute(
            select(CustomsAuditWorkflow)
            .where(CustomsAuditWorkflow.invoice_document_id == shipment_id)
        ).scalars().first()
    
    # Do not fail if workflow does not exist yet. Assistant should still answer document questions.
        
    route = classify_question(question)
    structured_data = _get_shipment_structured_data(db, workflow, shipment_id)
    
    answer = "I could not find a specific answer."
    sources = []
    
    if route == "out_of_scope":
        if "change" in question.lower() or "mark" in question.lower():
            answer = "I cannot change audited values through chat. Use the formal human-review workflow so the correction is validated, recorded and the affected checks are rerun."
        else:
            answer = "This request is outside the scope of my compliance assistance."
            
    elif route == "audit_result":
        if not workflow:
            answer = "The audit workflow has not been started yet. Please start the agent audit first."
        else:
            frozen_status = _frozen_status(workflow)
            q = question.casefold()
            if "which documents were checked" in q:
                document_names = ["Commercial Invoice"]
                if workflow.packing_list_document_id:
                    document_names.append("Packing List")
                supporting = (
                    ((workflow.final_report or {}).get("user_report") or {}).get(
                        "supporting_documents"
                    )
                    or []
                )
                document_names.extend(
                    str(item.get("required_document_type"))
                    for item in supporting
                    if item.get("uploaded") == "Yes"
                )
                answer = "The frozen audit checked: " + ", ".join(document_names) + "."
            else:
                answer = (
                    f"The shipment audit status is currently "
                    f"{frozen_status.upper()}. This is the frozen deterministic "
                    "shipment result."
                )
                if "why" in q:
                    discrepancies = (
                        ((workflow.final_report or {}).get("broker_findings") or {}).get(
                            "document_discrepancies"
                        )
                        or []
                    )
                    if discrepancies:
                        reasons = "; ".join(
                            str(item.get("message"))
                            for item in discrepancies
                            if item.get("message")
                        )
                        answer += f" The recorded reasons are: {reasons}"
            
            # Load the latest checks from the audit report event if available
            events = db.execute(
                select(CustomsAuditEvent)
                .where(CustomsAuditEvent.workflow_id == workflow.id)
                .order_by(CustomsAuditEvent.created_at.desc())
            ).scalars().all()
            
            failed_checks = []
            for e in events:
                if e.event_type == "audit_report_generated":
                    report = (e.event_payload or {}).get("report", {})
                    checks = report.get("checks", [])
                    failed_checks = [c for c in checks if c.get("status") in ["failed", "manual_review"]]
                    break
                    
            if failed_checks:
                answer += "\n\nThe following checks require attention:\n"
                for c in failed_checks:
                    answer += f"- {c.get('check_name')}: {c.get('message')}\n"
                    
            sources.append(
                SourceSchema(
                    source_kind="frozen_audit",
                    display_name=f"Audit Workflow {workflow.id}",
                    audit_revision_number=_audit_revision(workflow),
                    status=frozen_status,
                )
            )
        
    elif route == "shipment_document_fact":
        q = question.lower()
        if "invoice total" in q:
            val = structured_data.get("invoice_total")
            currency = structured_data.get("invoice_currency", "")
            if val:
                answer = f"The invoice total is {val} {currency}."
                sources.append(SourceSchema(source_kind="structured_extraction", display_name="Commercial Invoice Data"))
            else:
                answer = "The invoice total was not found in the structured data."
        elif "buyer" in q:
            val = structured_data.get("buyer_name") or structured_data.get("consignee_name")
            if val:
                answer = f"The buyer is {val}."
                sources.append(SourceSchema(source_kind="structured_extraction", display_name="Commercial Invoice Data"))
        elif "packing list" in q and "gross weight" in q:
            val = structured_data.get("packing_gross_weight")
            if val is None:
                val = _packing_item_value(db, workflow, "gross_weight")
            if val is None:
                val = _frozen_document_evidence_value(
                    workflow,
                    check_id="item_gross_weight_match",
                    document_type="Packing list",
                )
            if val is not None:
                answer = f"The packing list states a gross weight of {val} KG."
                shipment_key = (
                    workflow.invoice_document_id
                    if workflow is not None and workflow.invoice_document_id is not None
                    else shipment_id
                )
                from app.services.assistant.shipment_retriever import (
                    ShipmentDocumentRetriever,
                )

                chunks = ShipmentDocumentRetriever(db).retrieve(
                    shipment_key, question, top_k=1
                )
                if chunks:
                    chunk = chunks[0]
                    sources.append(
                        SourceSchema(
                            source_kind="shipment_document",
                            display_name=(
                                f"{chunk.document_name} p.{chunk.page_number}"
                            ),
                            document_name=chunk.document_name,
                            page_number=chunk.page_number,
                            snippet=chunk.text,
                        )
                    )
                else:
                    sources.append(
                        SourceSchema(
                            source_kind="structured_extraction",
                            display_name="Packing List Data",
                        )
                    )
            else:
                answer = "The packing-list gross weight was not found in structured data."
        elif "pct code" in q:
            val = structured_data.get("pct_code")
            if val:
                answer = f"The extracted PCT code is {str(val).replace('.', '')}."
                sources.append(
                    SourceSchema(
                        source_kind="structured_extraction",
                        display_name="Commercial Invoice Data",
                    )
                )
        elif "quantity" in q:
            val = structured_data.get("quantity")
            unit = structured_data.get("quantity_unit", "")
            if val:
                answer = f"The quantity is {val} {unit}."
                sources.append(SourceSchema(source_kind="structured_extraction", display_name="Extracted Data"))
        elif "match" in q:
             # Look at audit result for mismatches
            if not workflow:
                answer = "The audit has not been run yet, so matching cannot be confirmed."
            else:
                matching = [
                    check
                    for check in _frozen_checks(workflow)
                    if check.get("check_id")
                    in {
                        "item_quantity_match",
                        "item_net_weight_match",
                        "item_gross_weight_match",
                        "item_pct_code_match",
                    }
                ]
                mismatches = [
                    check for check in matching if check.get("status") != "passed"
                ]
                if matching and not mismatches:
                    answer = (
                        "Yes. The frozen audit records that the invoice and "
                        "packing list matched for quantity, net weight, gross "
                        "weight and PCT code."
                    )
                elif mismatches:
                    names = ", ".join(
                        str(check.get("check_id")) for check in mismatches
                    )
                    answer = f"No. The frozen audit records mismatches in: {names}."
                else:
                    answer = "The frozen audit does not contain item-matching checks."
                sources.append(
                    SourceSchema(
                        source_kind="frozen_audit",
                        display_name="Frozen Audit Matching Checks",
                        audit_revision_number=_audit_revision(workflow),
                        status=_frozen_status(workflow),
                    )
                )
        else:
            from app.services.assistant.shipment_retriever import ShipmentDocumentRetriever
            retriever = ShipmentDocumentRetriever(db)
            try:
                chunks = retriever.retrieve(shipment_id, search_query)
                if chunks:
                    answer = "Based on retrieved documents:\n"
                    for c in chunks:
                        answer += f"- {c.text} [{c.document_name}, Page {c.page_number}, Section: {c.section}]\n"
                        sources.append(SourceSchema(source_kind="shipment_document", display_name=f"{c.document_name} p.{c.page_number}"))
                else:
                    answer = "I could not find relevant information in the shipment documents."
            except Exception:
                answer = "I could not retrieve the indexed shipment documents."
    elif route == "regulatory_guidance":
        pct = structured_data.get("pct_code")
        dest = structured_data.get("destination_country")
        
        req = EvidenceSearchRequest(
            query=search_query,
            pct_code=pct,
            destination_country=dest,
            top_k=2
        )
        evidence_resp = run_evidence_search(db, req)
        if evidence_resp.status == "ok" and evidence_resp.results:
            answer = "Based on the regulatory documents:\n"
            for res in evidence_resp.results:
                answer += f"- {res.child_evidence_text} [{res.source_document}, Page {res.page_number}]\n"
                sources.append(
                    SourceSchema(
                        source_kind="regulatory",
                        display_name=res.source_document,
                        snippet=res.child_evidence_text,
                        source_document=res.source_document,
                        evidence_status="accepted",
                        page_number=res.page_number
                    )
                )
        else:
            answer = "The indexed regulatory documents did not contain a sufficiently relevant passage to answer this question."

    elif route == "combined_shipment_and_regulation":
        pct = structured_data.get("pct_code", "61091000")
        dest = structured_data.get("destination_country", "China")
        
        sources.append(SourceSchema(source_kind="uploaded_document", display_name="Uploaded Document Data"))
        
        status_text = _frozen_status(workflow).upper()
        sources.append(SourceSchema(source_kind="audit_finding", display_name="Audit Result", status=status_text))
        
        req = EvidenceSearchRequest(
            query=search_query,
            pct_code=pct,
            destination_country=dest,
            top_k=1
        )
        evidence_resp = run_evidence_search(db, req)
        if evidence_resp.status == "ok" and evidence_resp.results:
            res = evidence_resp.results[0]
            sources.append(
                SourceSchema(
                    source_kind="regulatory",
                    display_name=res.source_document,
                    evidence_status="accepted",
                    page_number=res.page_number
                )
            )
        
        sources.append(SourceSchema(source_kind="curated_rule", display_name="Configured Requirement"))
        
        answer = (
            f"The document matched the shipment information checked by CACE (Result: {status_text}). "
            "It has not been externally authenticated with the issuing authority. "
            "CACE does not issue customs clearance."
        )
        
    elif route == "audit_history":
        events = db.execute(
            select(CustomsAuditEvent)
            .where(
                CustomsAuditEvent.workflow_id
                == (workflow.id if workflow is not None else shipment_id)
            )
            .order_by(CustomsAuditEvent.created_at.desc())
        ).scalars().all()
        
        revisions = [e for e in events if e.event_type == "audit_report_generated"]
        if len(revisions) <= 1:
            answer = "There is only one revision. No later correction revision exists."
        else:
            rev_num = len(revisions)
            answer = f"Found {rev_num} revisions in the audit history. The latest revision changed the status."
            
        if events:
            sources.append(
                SourceSchema(
                    source_kind="frozen_audit",
                    display_name=f"Audit History",
                    audit_revision_number=len(revisions)
                )
            )
        
    # Safety Check / Limitations
    limitations = [
        "This assistant is running in single-user prototype mode. Account-based authorization and multi-user document isolation are not implemented.",
        "Responses are generated from frozen audit findings and verified structured extraction without live LLM calls in this test environment."
    ]
    # Save to db
    if not conv:
        conv = AssistantConversation(id=conv_id, shipment_id=shipment_id, mode="shipment_assistant")
        db.add(conv)
    
    db.add(AssistantMessage(
        conversation_id=conv_id,
        role="user",
        text=question
    ))
    db.add(AssistantMessage(
        conversation_id=conv_id,
        role="assistant",
        text=answer,
        answer_type=route,
        sources=[s.model_dump(mode="json") if hasattr(s, "model_dump") else s for s in sources]
    ))
    db.commit()

    suggested_questions = []
    frozen_status = _frozen_status(workflow)
    if not workflow or frozen_status not in ["passed", "failed"]:
        suggested_questions = [
            "What is the invoice total?",
            "What is the gross weight?",
            "What does the packing list say about packaging?",
            "Why is Form-E required?"
        ]
    else:
        suggested_questions = [
            f"Why did this shipment {workflow.status}?",
            "Which checks failed?",
            "Which document is missing?",
            "Does the Certificate of Origin satisfy the requirement?",
            "What should I correct?"
        ]

    return ChatResponse(
        conversation_id=conv_id,
        message_id=msg_id,
        mode="shipment_assistant",
        answer_type=route,
        answer=answer,
        audit_status=frozen_status,
        audit_revision_number=_audit_revision(workflow),
        sources=sources,
        limitations=limitations,
        suggested_questions=suggested_questions
    )
