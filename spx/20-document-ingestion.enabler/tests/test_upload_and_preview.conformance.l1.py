"""Conformance evidence for document upload validation and PDF provenance preview."""

from __future__ import annotations

from http import HTTPStatus
from uuid import uuid4

from app.schemas.documents import (
    DocumentExtractionResponse,
    DocumentPagePreviewResponse,
    DocumentUploadResponse,
)
from app.services.document_upload_service import ALLOWED_FILE_TYPES
from cace_testing.harnesses.database import CaceRuntime
from cace_testing.harnesses.fixtures_paths import (
    build_real_docx_bytes,
    build_real_pdf_bytes,
)


def test_valid_pdf_and_docx_uploads_conform_to_contract(
    cace_runtime: CaceRuntime,
) -> None:
    pdf_bytes = build_real_pdf_bytes(
        [
            "COMMERCIAL INVOICE",
            "PCT Code: 6109.1000",
            "Quantity: 500 PCS",
        ]
    )
    pdf_resp = cace_runtime.client.post(
        "/documents/upload",
        files={"file": ("invoice.pdf", pdf_bytes, ALLOWED_FILE_TYPES[".pdf"])},
    )
    assert pdf_resp.status_code == HTTPStatus.CREATED
    pdf_payload = DocumentUploadResponse.model_validate(pdf_resp.json())
    assert pdf_payload.stored_filename.endswith(".pdf")
    assert pdf_payload.size_bytes == len(pdf_bytes)

    docx_bytes = build_real_docx_bytes("Certificate of Origin sample body")
    docx_resp = cace_runtime.client.post(
        "/documents/upload",
        files={"file": ("coo.docx", docx_bytes, ALLOWED_FILE_TYPES[".docx"])},
    )
    assert docx_resp.status_code == HTTPStatus.CREATED
    docx_payload = DocumentUploadResponse.model_validate(docx_resp.json())
    assert docx_payload.stored_filename.endswith(".docx")


def test_invalid_uploads_fail_closed_by_media_and_header_contract(
    cace_runtime: CaceRuntime,
) -> None:
    spoofed_pdf = cace_runtime.client.post(
        "/documents/upload",
        files={"file": ("bad.pdf", b"NOT_A_PDF", ALLOWED_FILE_TYPES[".pdf"])},
    )
    assert spoofed_pdf.status_code == HTTPStatus.BAD_REQUEST

    empty_pdf = cace_runtime.client.post(
        "/documents/upload",
        files={"file": ("empty.pdf", b"", ALLOWED_FILE_TYPES[".pdf"])},
    )
    assert empty_pdf.status_code == HTTPStatus.BAD_REQUEST

    unsupported_ext = cace_runtime.client.post(
        "/documents/upload",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )
    assert unsupported_ext.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE


def test_extracted_pdf_renders_page_png_and_highlight_boxes(
    cace_runtime: CaceRuntime,
) -> None:
    pdf_bytes = build_real_pdf_bytes(
        [
            "Exporter: Lahore Cotton Garments (Pvt.) Ltd.",
            "PCT Code 6109.1000",
            "Net Weight 75.00 KG",
        ]
    )
    upload = DocumentUploadResponse.model_validate(
        cace_runtime.client.post(
            "/documents/upload",
            files={"file": ("preview.pdf", pdf_bytes, ALLOWED_FILE_TYPES[".pdf"])},
        ).json()
    )

    extracted = cace_runtime.client.post(
        f"/documents/uploads/{upload.document_id}/extract"
    )
    assert extracted.status_code == HTTPStatus.OK
    ext_model = DocumentExtractionResponse.model_validate(extracted.json())
    assert ext_model.page_count == 1
    assert ext_model.character_count > 0

    preview = cace_runtime.client.get(
        f"/documents/uploads/{upload.document_id}/pages/1",
        params={"highlight": "61091000", "field_name": "pct_code"},
    )
    assert preview.status_code == HTTPStatus.OK
    preview_model = DocumentPagePreviewResponse.model_validate(preview.json())
    assert preview_model.page_number == 1
    assert preview_model.word_count > 0
    assert any(box.match_type == "pct_code" for box in preview_model.highlights)

    image_resp = cace_runtime.client.get(
        f"/documents/uploads/{upload.document_id}/pages/1/image"
    )
    assert image_resp.status_code == HTTPStatus.OK
    assert image_resp.headers["content-type"] == "image/png"
    assert image_resp.content.startswith(b"\x89PNG\r\n\x1a\n")

    missing_page = cace_runtime.client.get(
        f"/documents/uploads/{upload.document_id}/pages/99"
    )
    assert missing_page.status_code == HTTPStatus.NOT_FOUND

    missing_doc = cace_runtime.client.get(f"/documents/uploads/{uuid4()}/pages/1")
    assert missing_doc.status_code == HTTPStatus.NOT_FOUND
