from io import BytesIO
from uuid import UUID
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.main import app
from app.services import document_upload_service


client = TestClient(app)


def make_docx_content() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    return buffer.getvalue()


def test_upload_pdf_saves_file_with_uuid_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(document_upload_service, "UPLOAD_DIRECTORY", tmp_path)
    content = b"%PDF-1.4\n%%EOF"

    response = client.post(
        "/documents/upload",
        files={"file": ("invoice.pdf", content, "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    UUID(body["document_id"])
    assert body["stored_filename"].endswith(".pdf")
    assert (tmp_path / body["stored_filename"]).read_bytes() == content


def test_upload_docx_saves_file_with_docx_extension(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(document_upload_service, "UPLOAD_DIRECTORY", tmp_path)
    content = make_docx_content()

    response = client.post(
        "/documents/upload",
        files={
            "file": (
                "packing-list.docx",
                content,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["stored_filename"].endswith(".docx")
    assert (tmp_path / body["stored_filename"]).read_bytes() == content


def test_reject_docx_with_invalid_internal_structure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(document_upload_service, "UPLOAD_DIRECTORY", tmp_path)
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("unrelated.txt", "Not a Word document")

    response = client.post(
        "/documents/upload",
        files={
            "file": (
                "fake.docx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 400
    assert "valid Word document" in response.json()["detail"]


def test_reject_file_with_wrong_mime_type(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(document_upload_service, "UPLOAD_DIRECTORY", tmp_path)

    response = client.post(
        "/documents/upload",
        files={"file": ("invoice.pdf", b"%PDF-1.4\n%%EOF", "text/plain")},
    )

    assert response.status_code == 415
