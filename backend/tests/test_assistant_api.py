from fastapi.testclient import TestClient
from uuid import uuid4
import json

from app.main import app
from app.core.database import get_db_session
from sqlalchemy.orm import Session
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

    print("\n\n--- POST /api/v1/assistant/guidance ---")
    payload = {
        "product": "Cotton knitted T-shirts",
        "pct_code": "61091000",
        "destination": "China"
    }
    resp = client.post("/api/v1/assistant/guidance", json=payload)
    print("Status:", resp.status_code)
    print("Response:", json.dumps(resp.json(), indent=2))
    
    print("\n--- POST /api/v1/assistant/shipments/{shipment_id}/chat ---")
    payload2 = {
        "question": "What is the invoice total?",
        "conversation_id": str(uuid4())
    }
    resp2 = client.post(f"/api/v1/assistant/shipments/{workflow_id}/chat", json=payload2)
    print("Status:", resp2.status_code)
    print("Response:", json.dumps(resp2.json(), indent=2))

    # Clean up
    app.dependency_overrides.clear()
