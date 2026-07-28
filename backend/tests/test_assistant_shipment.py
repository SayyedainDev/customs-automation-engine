from uuid import uuid4
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.documents import DocumentUploadRecord
from app.models.customs_audit import CustomsAuditWorkflow, CustomsAuditEvent
from app.services.assistant.shipment_assistant import answer_shipment_question

def test_answer_shipment_question_invoice_total(isolated_database):
    db = Session(isolated_database)
    
    workflow_id = uuid4()
    doc1_id = uuid4()
    
    workflow = CustomsAuditWorkflow(id=workflow_id, thread_id="thread-1", status="passed", invoice_document_id=doc1_id)
    doc1 = DocumentUploadRecord(
        id=doc1_id, 
        original_filename="inv.pdf", 
        stored_filename="inv.pdf",
        file_extension=".pdf",
        mime_type="application/pdf",
        size_bytes=100,
        structured_data={"invoice_total": "1000", "invoice_currency": "USD"}
    )
    db.add(workflow)
    db.add(doc1)
    db.commit()
    
    resp = answer_shipment_question(db, workflow_id, "What is the invoice total?")
    assert resp.answer_type == "shipment_document_fact"
    assert "1000 USD" in resp.answer
    assert resp.sources[0].source_kind == "structured_extraction"

def test_answer_shipment_question_audit_result(isolated_database):
    db = Session(isolated_database)
    
    workflow_id = uuid4()
    workflow = CustomsAuditWorkflow(id=workflow_id, thread_id="thread-2", status="failed")
    db.add(workflow)
    
    event = CustomsAuditEvent(
        id=uuid4(),
        workflow_id=workflow_id,
        event_type="audit_report_generated",
        actor_type="system",
        event_payload={
            "report": {
                "checks": [
                    {"status": "failed", "check_name": "Check1", "message": "Missing document"}
                ]
            }
        }
    )
    db.add(event)
    db.commit()
    
    resp = answer_shipment_question(db, workflow_id, "Why did it fail?")
    assert resp.answer_type == "audit_result"
    assert "FAILED" in resp.answer
    assert "Missing document" in resp.answer
    assert resp.sources[0].source_kind == "frozen_audit"

def test_answer_shipment_question_out_of_scope(isolated_database):
    db = Session(isolated_database)
    workflow_id = uuid4()
    workflow = CustomsAuditWorkflow(id=workflow_id, thread_id="thread-3", status="passed")
    db.add(workflow)
    db.commit()
    
    resp = answer_shipment_question(db, workflow_id, "Change the quantity to 100.")
    assert resp.answer_type == "out_of_scope"
    assert "cannot change audited values" in resp.answer

def test_answer_shipment_question_regulatory_guidance(isolated_database):
    db = Session(isolated_database)
    workflow_id = uuid4()
    workflow = CustomsAuditWorkflow(id=workflow_id, thread_id="thread-4", status="passed")
    db.add(workflow)
    db.commit()
    
    import app.services.assistant.shipment_assistant as sa
    original_classify = sa.classify_question
    sa.classify_question = lambda q: "regulatory_guidance"
    try:
        resp = answer_shipment_question(db, workflow_id, "Why is Form-E required?")
        assert resp.answer_type == "regulatory_guidance"
        assert "In the full implementation" not in resp.answer
        assert resp.suggested_questions != []
    finally:
        sa.classify_question = original_classify

def test_answer_shipment_question_combined(isolated_database):
    db = Session(isolated_database)
    workflow_id = uuid4()
    workflow = CustomsAuditWorkflow(id=workflow_id, thread_id="thread-5", status="passed")
    db.add(workflow)
    db.commit()
    
    resp = answer_shipment_question(db, workflow_id, "Does my Form-E satisfy the requirement?")
    assert resp.answer_type == "combined_shipment_and_regulation"
    assert "Simulated combined answer" not in resp.answer
    source_kinds = [s.source_kind for s in resp.sources]
    assert "uploaded_document" in source_kinds
    assert "audit_finding" in source_kinds

def test_answer_shipment_question_audit_history(isolated_database):
    db = Session(isolated_database)
    workflow_id = uuid4()
    workflow = CustomsAuditWorkflow(id=workflow_id, thread_id="thread-6", status="passed")
    db.add(workflow)
    
    event = CustomsAuditEvent(
        id=uuid4(),
        workflow_id=workflow_id,
        event_type="audit_report_generated",
        actor_type="system",
        event_payload={"report": {"status": "failed"}}
    )
    event2 = CustomsAuditEvent(
        id=uuid4(),
        workflow_id=workflow_id,
        event_type="audit_report_generated",
        actor_type="system",
        event_payload={"report": {"status": "passed"}}
    )
    db.add(event)
    db.add(event2)
    db.commit()
    
    # We don't have a specific router classifier for audit history in the provided tests, but we can call it manually if we mock the route, or if "history" matches.
    # The prompt says: "audit_history has no real handler." Wait, the routing logic in `classify_question` might not map to `audit_history` perfectly for all strings, but we can test the function if it hits the branch.
    # Let's mock classify_question in the test.
    import app.services.assistant.shipment_assistant as sa
    original_classify = sa.classify_question
    sa.classify_question = lambda q: "audit_history"
    try:
        resp = answer_shipment_question(db, workflow_id, "What changed between revision 1 and revision 2?")
        assert resp.answer_type == "audit_history"
        assert "Found 2 revisions" in resp.answer
        assert resp.sources[0].source_kind == "frozen_audit"
    finally:
        sa.classify_question = original_classify
