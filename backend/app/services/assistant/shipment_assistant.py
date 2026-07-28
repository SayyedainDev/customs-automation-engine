from uuid import UUID, uuid4
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.customs_audit import CustomsAuditWorkflow, CustomsAuditEvent
from app.models.documents import DocumentUploadRecord
from app.models.shipment_chunks import ShipmentDocumentChunk
from app.models.assistant import AssistantConversation, AssistantMessage
from app.schemas.assistant import ChatResponse, SourceSchema
from app.services.assistant.routing import classify_question


def _get_shipment_structured_data(db: Session, workflow: CustomsAuditWorkflow | None, shipment_id: UUID) -> dict:
    data = {}
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
    return data


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
            # Frozen deterministic result
            answer = f"The shipment audit status is currently {workflow.status.upper()}."
            
            # Load the latest checks from the audit report event if available
            events = db.execute(
                select(CustomsAuditEvent)
                .where(CustomsAuditEvent.workflow_id == shipment_id)
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
                    audit_revision_number=None,
                    status=workflow.status
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
                answer = f"The deterministic checks resulted in {workflow.status.upper()}. "
                sources.append(SourceSchema(source_kind="frozen_audit", display_name="Audit Result"))
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
            except Exception as e:
                answer = f"I could not retrieve documents due to an error: {e}"            
    elif route == "regulatory_guidance":
        answer = "This shipment is subject to export regulations. In the full implementation, this uses the regulatory RAG to cite specific laws."
        
    elif route == "combined_shipment_and_regulation":
        answer = "To verify if your document satisfies the requirement, we check the extracted data against the deterministic rules. (Simulated combined answer)"
        
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

    return ChatResponse(
        conversation_id=conv_id,
        message_id=msg_id,
        mode="shipment_assistant",
        answer_type=route,
        answer=answer,
        audit_status=workflow.status if workflow else "unstarted",
        audit_revision_number=None,
        sources=sources,
        limitations=limitations
    )
