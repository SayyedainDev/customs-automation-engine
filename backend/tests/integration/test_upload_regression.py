from uuid import UUID
from io import BytesIO

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.main import app
from app.services import document_upload_service
from app.models.documents import DocumentUploadRecord

client = TestClient(app)

def test_commercial_invoice_upload(tmp_path, monkeypatch, isolated_database) -> None:
    monkeypatch.setattr(document_upload_service, "UPLOAD_DIRECTORY", tmp_path)
    content = b"%PDF-1.4\n%%EOF"

    response = client.post(
        "/documents/upload",
        files={"file": ("01_commercial_invoice.pdf", content, "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    doc_id = UUID(body["document_id"])
    assert body["original_filename"] == "01_commercial_invoice.pdf"

    # Verify metadata row exists and indexing status has safe initial value
    db = Session(isolated_database)
    record = db.get(DocumentUploadRecord, doc_id)
    assert record is not None
    assert record.indexing_status == "pending"
    assert record.indexing_error is None

def test_packing_list_upload(tmp_path, monkeypatch, isolated_database) -> None:
    monkeypatch.setattr(document_upload_service, "UPLOAD_DIRECTORY", tmp_path)
    content = b"%PDF-1.4\n%%EOF"

    response = client.post(
        "/documents/upload",
        files={"file": ("02_packing_list.pdf", content, "application/pdf")},
    )

    assert response.status_code == 201

def test_supporting_documents_upload(tmp_path, monkeypatch, isolated_database) -> None:
    monkeypatch.setattr(document_upload_service, "UPLOAD_DIRECTORY", tmp_path)
    
    for filename in ["form_e.pdf", "certificate_of_origin.pdf"]:
        content = b"%PDF-1.4\n%%EOF"
        response = client.post(
            "/documents/upload",
            files={"file": (filename, content, "application/pdf")},
        )
        assert response.status_code == 201

def test_database_schema_regression(isolated_database) -> None:
    # Test that the columns actually exist in the DB schema
    db = Session(isolated_database)
    # Using pragma table_info since isolated_database is sqlite in test
    result = db.execute(text("PRAGMA table_info(document_uploads)")).fetchall()
    column_names = [row[1] for row in result]
    assert "indexing_status" in column_names
    assert "indexing_error" in column_names

def test_transaction_rollback(tmp_path, monkeypatch, isolated_database) -> None:
    monkeypatch.setattr(document_upload_service, "UPLOAD_DIRECTORY", tmp_path)
    
    # Mock db.commit to raise an error
    original_commit = Session.commit
    from sqlalchemy.exc import SQLAlchemyError
    def mock_commit(self):
        raise SQLAlchemyError("Simulated DB failure")
    
    monkeypatch.setattr(Session, "commit", mock_commit)
    
    content = b"%PDF-1.4\n%%EOF"
    response = client.post(
        "/documents/upload",
        files={"file": ("invoice.pdf", content, "application/pdf")},
    )
    
    assert response.status_code == 500
    assert "could not save the document metadata" in response.json()["detail"]
    
    db = Session(isolated_database)
    # Check that no rows were inserted
    assert db.query(DocumentUploadRecord).count() == 0

