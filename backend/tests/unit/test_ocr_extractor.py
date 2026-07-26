from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.extraction import ocr_extractor


TESSERACT_TSV = """level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext
5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t90\tInvoice
5\t1\t1\t1\t1\t2\t12\t0\t10\t10\t80\tINV-1001
5\t1\t1\t1\t2\t1\t0\t12\t10\t10\t70\t6109.1000
"""


def test_tesseract_tsv_text_and_confidence_are_preserved(
    monkeypatch,
) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        ocr_extractor,
        "_resolve_tesseract_executable",
        lambda: "/usr/bin/tesseract",
    )
    monkeypatch.setattr(
        ocr_extractor,
        "_render_pdf_page",
        lambda _path, _page_number, _dpi: b"png-bytes",
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout=TESSERACT_TSV.encode(),
            stderr=b"",
        )

    monkeypatch.setattr(ocr_extractor.subprocess, "run", fake_run)

    result = ocr_extractor.ocr_pdf_page(
        pdf_path=Path("invoice.pdf"),
        document_id=uuid4(),
        page_number=2,
        original_embedded_text="",
    )

    assert result.ocr_text == "Invoice INV-1001\n6109.1000"
    assert result.ocr_confidence == Decimal("0.800")
    assert result.validation_status == "verified"
    assert captured["command"][-1] == "tsv"
    assert captured["input"] == b"png-bytes"


def test_tesseract_nonzero_response_becomes_ocr_failure(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ocr_extractor,
        "_resolve_tesseract_executable",
        lambda: "/usr/bin/tesseract",
    )
    monkeypatch.setattr(
        ocr_extractor,
        "_render_pdf_page",
        lambda _path, _page_number, _dpi: b"png-bytes",
    )
    monkeypatch.setattr(
        ocr_extractor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"recognition failed",
        ),
    )

    with pytest.raises(
        ocr_extractor.OcrExtractionError,
        match="recognition failed",
    ):
        ocr_extractor.ocr_pdf_page(
            pdf_path=Path("invoice.pdf"),
            document_id=uuid4(),
            page_number=1,
            original_embedded_text="",
        )
