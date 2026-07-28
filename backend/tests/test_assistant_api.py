from fastapi.testclient import TestClient
from uuid import uuid4
import json

from app.main import app
from app.core.database import get_db_session
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models.customs_audit import CustomsAuditWorkflow

client = TestClient(app)

def test_api_endpoints(isolated_database):
    # Override get_db_session
    def override_get_db():
        db = Session(isolated_database)
        try:
            yield db
        finally:
            db.close()
            
    app.dependency_overrides[get_db_session] = override_get_db
    
    db = Session(isolated_database)
    workflow_id = uuid4()
    workflow = CustomsAuditWorkflow(id=workflow_id, thread_id="thread-test", status="passed")
    db.add(workflow)
    db.commit()

    # Test Guidance
    payload = {
        "product": "Cotton knitted T-shirts",
        "pct_code": "61091000",
        "destination": "China"
    }
    resp = client.post("/api/v1/assistant/guidance", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}. Response: {resp.text}"
    data = resp.json()
    assert data["pct_code"] == "61091000"
    assert data["supported_scope"] is True
    assert len(data["documents"]) > 0

    # Test Chat
    payload2 = {
        "question": "What is the invoice total?",
        "conversation_id": str(uuid4())
    }
    resp2 = client.post(f"/api/v1/assistant/shipments/{workflow_id}/chat", json=payload2)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["answer_type"] == "shipment_document_fact"
    assert "invoice total" in data2["answer"]
    # Test Guidance - Missing Destination
    resp_no_dest = client.post("/api/v1/assistant/guidance", json={
        "product": "Cotton knitted T-shirts",
        "pct_code": "61091000",
        "destination": ""
    })
    assert resp_no_dest.status_code == 200
    assert resp_no_dest.json()["supported_scope"] is True
    assert "Please provide a destination" in resp_no_dest.json()["answer"]

    # Test Guidance - Unsupported PCT
    resp_unsupported = client.post("/api/v1/assistant/guidance", json={
        "product": "Some Product",
        "pct_code": "62034200",
        "destination": "China"
    })
    assert resp_unsupported.status_code == 200
    assert resp_unsupported.json()["supported_scope"] is False
    assert "CACE currently supports only five textile PCT codes" in resp_unsupported.json()["answer"]
    
    # Test Guidance - Product/PCT conflict
    resp_conflict = client.post("/api/v1/assistant/guidance", json={
        "product": "Cotton yarn",
        "pct_code": "61091000",
        "destination": "China"
    })
    assert resp_conflict.status_code == 200
    assert resp_conflict.json()["supported_scope"] is False
    assert "inconsistent" in resp_conflict.json()["answer"]

    # Test Conversation Deletion
    conv_id = data2["conversation_id"]
    resp_delete = client.delete(f"/api/v1/assistant/conversations/{conv_id}")
    assert resp_delete.status_code == 204
    
    # Verify DB state
    from app.models.assistant import AssistantConversation, AssistantMessage
    from uuid import UUID
    conv_uuid = UUID(conv_id)
    assert db.get(AssistantConversation, conv_uuid) is None
    messages = db.execute(select(AssistantMessage).where(AssistantMessage.conversation_id == conv_uuid)).scalars().all()
    assert len(messages) == 0
    # Clean up
    app.dependency_overrides.clear()
