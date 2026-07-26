from io import BytesIO
from uuid import UUID, uuid4
from zipfile import ZipFile

import pymupdf
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.main import app
from app.models.documents import DocumentUploadRecord
from app.services import document_service, document_upload_service
from app.services.extraction.pdf_extractor import PdfExtractorResult


client = TestClient(app)


def make_pdf_content(text: str) -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    content = pdf.tobytes()
    pdf.close()
    return content


def make_docx_content() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    return buffer.getvalue()


def configure_upload_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(document_upload_service, "UPLOAD_DIRECTORY", tmp_path)
    monkeypatch.setattr(document_service, "UPLOAD_DIRECTORY", tmp_path)


def test_extract_pdf_uses_uploaded_record_and_updates_status(
    tmp_path,
    monkeypatch,
    isolated_database: Engine,
) -> None:
    configure_upload_directory(tmp_path, monkeypatch)
    upload = client.post(
        "/documents/upload",
        files={
            "file": (
                "commercial-invoice.pdf",
                make_pdf_content("Invoice number INV-1001"),
                "application/pdf",
            )
        },
    )
    document_id = upload.json()["document_id"]
    document_uuid = UUID(document_id)

    def fake_extract(_file_path) -> PdfExtractorResult:
        # The first status commit must happen before the expensive loader runs.
        with Session(isolated_database) as inspection_session:
            stored_document = inspection_session.get(
                DocumentUploadRecord,
                document_uuid,
            )
            assert stored_document is not None
            assert stored_document.status == "extracting"
        return PdfExtractorResult(
            text="Invoice number INV-1001",
            page_count=1,
        )

    monkeypatch.setattr(document_service, "extract_text_from_pdf", fake_extract)

    extraction = client.post(f"/documents/uploads/{document_id}/extract")
    metadata = client.get(f"/documents/uploads/{document_id}")

    assert upload.status_code == 201
    assert extraction.status_code == 200
    assert extraction.json()["page_count"] == 1
    assert extraction.json()["character_count"] == len("Invoice number INV-1001")
    assert extraction.json()["status"] == "extracted"
    assert "extracted_text" not in extraction.json()
    assert metadata.status_code == 200
    assert metadata.json()["document_id"] == document_id
    assert metadata.json()["original_filename"] == "commercial-invoice.pdf"
    assert metadata.json()["status"] == "extracted"
    assert metadata.json()["page_count"] == 1
    assert metadata.json()["character_count"] == len("Invoice number INV-1001")
    assert metadata.json()["extracted_at"] is not None

    with Session(isolated_database) as inspection_session:
        stored_document = inspection_session.get(
            DocumentUploadRecord,
            document_uuid,
        )
        assert stored_document is not None
        assert stored_document.extracted_text == "Invoice number INV-1001"
        assert stored_document.extraction_error is None


def test_extract_returns_404_when_database_record_does_not_exist() -> None:
    response = client.post(f"/documents/uploads/{uuid4()}/extract")

    assert response.status_code == 404
    assert "was not found" in response.json()["detail"]


def test_extract_returns_404_when_stored_pdf_is_missing(
    tmp_path,
    monkeypatch,
    isolated_database: Engine,
) -> None:
    configure_upload_directory(tmp_path, monkeypatch)
    upload = client.post(
        "/documents/upload",
        files={
            "file": (
                "invoice.pdf",
                make_pdf_content("Invoice"),
                "application/pdf",
            )
        },
    )
    (tmp_path / upload.json()["stored_filename"]).unlink()

    response = client.post(
        f"/documents/uploads/{upload.json()['document_id']}/extract"
    )
    metadata = client.get(f"/documents/uploads/{upload.json()['document_id']}")

    assert response.status_code == 404
    assert "stored file is missing" in response.json()["detail"]
    assert metadata.json()["status"] == "failed"
    with Session(isolated_database) as inspection_session:
        stored_document = inspection_session.get(
            DocumentUploadRecord,
            UUID(upload.json()["document_id"]),
        )
        assert stored_document is not None
        assert stored_document.extraction_error is not None
        assert "StoredDocumentNotFoundError" in stored_document.extraction_error


def test_extract_rejects_a_docx_upload(
    tmp_path,
    monkeypatch,
    isolated_database: Engine,
) -> None:
    configure_upload_directory(tmp_path, monkeypatch)
    upload = client.post(
        "/documents/upload",
        files={
            "file": (
                "packing-list.docx",
                make_docx_content(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    response = client.post(
        f"/documents/uploads/{upload.json()['document_id']}/extract"
    )
    metadata = client.get(f"/documents/uploads/{upload.json()['document_id']}")

    assert response.status_code == 415
    assert "PDF files only" in response.json()["detail"]
    assert metadata.json()["status"] == "failed"
    with Session(isolated_database) as inspection_session:
        stored_document = inspection_session.get(
            DocumentUploadRecord,
            UUID(upload.json()["document_id"]),
        )
        assert stored_document is not None
        assert stored_document.extraction_error is not None
        assert "UnsupportedDocumentTypeError" in stored_document.extraction_error


def test_extract_returns_422_for_a_corrupted_pdf(
    tmp_path,
    monkeypatch,
    isolated_database: Engine,
) -> None:
    configure_upload_directory(tmp_path, monkeypatch)
    upload = client.post(
        "/documents/upload",
        files={
            "file": (
                "corrupted.pdf",
                b"%PDF-this-is-not-a-real-pdf",
                "application/pdf",
            )
        },
    )

    response = client.post(
        f"/documents/uploads/{upload.json()['document_id']}/extract"
    )
    metadata = client.get(f"/documents/uploads/{upload.json()['document_id']}")

    assert response.status_code == 422
    assert "could not be extracted" in response.json()["detail"]
    assert metadata.json()["status"] == "failed"
    assert metadata.json()["page_count"] is None
    assert metadata.json()["character_count"] is None
    with Session(isolated_database) as inspection_session:
        stored_document = inspection_session.get(
            DocumentUploadRecord,
            UUID(upload.json()["document_id"]),
        )
        assert stored_document is not None
        assert stored_document.extraction_error is not None
        assert "PdfExtractionError" in stored_document.extraction_error
