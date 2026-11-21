"""Unit and API tests for PDF page preview rendering and bounding-box highlighting."""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pymupdf
from fastapi.testclient import TestClient

from app.main import app
from app.services import document_service, document_upload_service
from app.services.extraction.page_preview import locate_highlight_boxes

client = TestClient(app)


def _configure_upload_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(document_upload_service, "UPLOAD_DIRECTORY", tmp_path)
    monkeypatch.setattr(document_service, "UPLOAD_DIRECTORY", tmp_path)


def _make_sample_invoice_pdf() -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((50, 50), "Invoice Date: 20 Jul 2026")
    page.insert_text((50, 85), "Description")
    page.insert_text((220, 85), "PCT Code")
    page.insert_text((320, 85), "Quantity")
    page.insert_text((420, 85), "Net Weight")

    page.insert_text((50, 115), "Cotton knitted T-shirts")
    page.insert_text((220, 115), "6109.1000")
    page.insert_text((320, 115), "500")
    page.insert_text((420, 115), "500")
    content = pdf.tobytes()
    pdf.close()
    return content


def _make_docx_bytes() -> bytes:
    buf = BytesIO()
    with ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    return buf.getvalue()


def test_locate_highlight_boxes_supports_phrase_pct_decimal_date_and_scanned() -> None:
    words = [
        (50.0, 40.0, 95.0, 52.0, "Exporter:", 0, 0, 0),
        (105.0, 40.0, 145.0, 52.0, "Lahore", 0, 0, 1),
        (150.0, 40.0, 188.0, 52.0, "Cotton", 0, 0, 2),
        (193.0, 40.0, 245.0, 52.0, "Garments", 0, 0, 3),
        (250.0, 40.0, 280.0, 52.0, "(Pvt.)", 0, 0, 4),
        (285.0, 40.0, 310.0, 52.0, "Ltd.", 0, 0, 5),
        (50.0, 65.0, 80.0, 77.0, "Date", 0, 1, 0),
        (90.0, 65.0, 105.0, 77.0, "20", 0, 1, 1),
        (110.0, 65.0, 128.0, 77.0, "Jul", 0, 1, 2),
        (133.0, 65.0, 165.0, 77.0, "2026", 0, 1, 3),
        (50.0, 95.0, 110.0, 107.0, "6109.1000", 0, 2, 0),
        (150.0, 95.0, 185.0, 107.0, "75.00", 0, 2, 1),
    ]

    phrase_boxes = locate_highlight_boxes(
        words,
        page_width=595,
        page_height=842,
        query="Lahore Cotton Garments (Pvt.) Ltd.",
        field_name="exporter_name",
    )
    assert len(phrase_boxes) == 1
    assert phrase_boxes[0].match_type == "exact_phrase"
    assert phrase_boxes[0].x0 < 105.0
    assert phrase_boxes[0].x1 > 310.0

    pct_boxes = locate_highlight_boxes(
        words,
        page_width=595,
        page_height=842,
        query="61091000",
        field_name="pct_code",
    )
    assert len(pct_boxes) == 1
    assert pct_boxes[0].match_type == "pct_code"
    assert pct_boxes[0].text == "6109.1000"

    decimal_boxes = locate_highlight_boxes(
        words,
        page_width=595,
        page_height=842,
        query="75",
        field_name="net_weight",
    )
    assert len(decimal_boxes) == 1
    assert decimal_boxes[0].match_type == "numeric_value"
    assert decimal_boxes[0].text == "75.00"

    date_boxes = locate_highlight_boxes(
        words,
        page_width=595,
        page_height=842,
        query="2026-07-20",
        field_name="invoice_date",
    )
    assert len(date_boxes) == 1
    assert date_boxes[0].match_type == "date"

    scanned_boxes = locate_highlight_boxes(
        [],
        page_width=595,
        page_height=842,
        query="61091000",
    )
    assert scanned_boxes == []


def test_locate_highlight_boxes_disambiguates_columns_using_field_name() -> None:
    words = [
        (300.0, 80.0, 350.0, 92.0, "Quantity", 0, 0, 0),
        (410.0, 80.0, 430.0, 92.0, "Net", 0, 0, 1),
        (434.0, 80.0, 472.0, 92.0, "Weight", 0, 0, 2),
        (312.0, 110.0, 336.0, 122.0, "500", 0, 1, 0),
        (425.0, 110.0, 449.0, 122.0, "500", 0, 1, 1),
    ]

    qty_boxes = locate_highlight_boxes(
        words,
        page_width=595,
        page_height=842,
        query="500",
        field_name="invoice.line_items[1].quantity",
    )
    assert len(qty_boxes) == 1
    assert 305.0 <= qty_boxes[0].x0 <= 320.0

    net_boxes = locate_highlight_boxes(
        words,
        page_width=595,
        page_height=842,
        query="500",
        field_name="packing_list.items[1].net_weight",
    )
    assert len(net_boxes) == 1
    assert 418.0 <= net_boxes[0].x0 <= 432.0


def test_pdf_page_preview_and_image_endpoints(
    tmp_path,
    monkeypatch,
    isolated_database,
) -> None:
    _configure_upload_dir(tmp_path, monkeypatch)
    upload = client.post(
        "/documents/upload",
        files={"file": ("invoice.pdf", _make_sample_invoice_pdf(), "application/pdf")},
    )
    assert upload.status_code == 201
    document_id = upload.json()["document_id"]

    extract = client.post(f"/documents/uploads/{document_id}/extract")
    assert extract.status_code == 200

    preview_pct = client.get(
        f"/documents/uploads/{document_id}/pages/1",
        params={"highlight": "61091000", "field_name": "pct_code"},
    )
    assert preview_pct.status_code == 200
    body = preview_pct.json()
    assert body["document_id"] == document_id
    assert body["page_number"] == 1
    assert body["page_count"] == 1
    assert body["word_count"] > 0
    assert body["image_url"] == f"/documents/uploads/{document_id}/pages/1/image"
    assert len(body["highlights"]) == 1
    box = body["highlights"][0]
    assert box["match_type"] == "pct_code"
    assert 0.0 <= box["left_pct"] <= 100.0
    assert 0.0 <= box["top_pct"] <= 100.0
    assert box["width_pct"] > 0.0
    assert box["height_pct"] > 0.0

    preview_qty = client.get(
        f"/documents/uploads/{document_id}/pages/1",
        params={"highlight": "500", "field_name": "invoice.line_items[1].quantity"},
    )
    assert preview_qty.status_code == 200
    qty_highlights = preview_qty.json()["highlights"]
    assert len(qty_highlights) == 1
    assert qty_highlights[0]["x0"] < 380.0

    image_resp = client.get(f"/documents/uploads/{document_id}/pages/1/image")
    assert image_resp.status_code == 200
    assert image_resp.headers["content-type"] == "image/png"
    assert image_resp.content.startswith(b"\x89PNG\r\n\x1a\n")

    assert client.get(f"/documents/uploads/{uuid4()}/pages/1").status_code == 404
    assert client.get(f"/documents/uploads/{document_id}/pages/99").status_code == 404
    assert (
        client.get(f"/documents/uploads/{document_id}/pages/99/image").status_code
        == 404
    )

    docx_upload = client.post(
        "/documents/upload",
        files={
            "file": (
                "notes.docx",
                _make_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert docx_upload.status_code == 201
    docx_id = docx_upload.json()["document_id"]
    assert client.get(f"/documents/uploads/{docx_id}/pages/1").status_code == 415

    (tmp_path / upload.json()["stored_filename"]).unlink()
    assert client.get(f"/documents/uploads/{document_id}/pages/1").status_code == 404
    assert (
        client.get(f"/documents/uploads/{document_id}/pages/1/image").status_code
        == 404
    )
