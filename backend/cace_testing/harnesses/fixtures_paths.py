"""Inert repository path helpers and minimal binary payload builders for CACE tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pymupdf
import yaml

_STANDARD_PDF_WIDTH = 595.0
_STANDARD_PDF_HEIGHT = 842.0


@dataclass(frozen=True)
class GoldenQuestion:
    """One labeled case loaded by path from ``backend/tests/eval/golden_questions.yaml``."""

    question_id: str
    question: str
    category: str
    resolved_by: str


def repo_root() -> Path:
    """Return the root directory of ``enterprise_customs_engine``."""
    return Path(__file__).resolve().parents[3]


def frontend_dir() -> Path:
    """Return the frontend application root directory."""
    return repo_root() / "frontend"


def golden_questions_path() -> Path:
    """Return the path to the curated golden chatbot evaluation dataset."""
    return repo_root() / "backend" / "tests" / "eval" / "golden_questions.yaml"


def synthetic_factory_dir() -> Path:
    """Return the path to the synthetic shipment benchmark corpus."""
    return repo_root() / "synthetic_factory"


def load_golden_questions() -> list[GoldenQuestion]:
    """Load golden chatbot questions cleanly from the repository YAML fixture."""
    raw = yaml.safe_load(golden_questions_path().read_text(encoding="utf-8"))
    return [
        GoldenQuestion(
            question_id=str(row["id"]),
            question=str(row["question"]),
            category=str(row["category"]),
            resolved_by=str(row["resolved_by"]),
        )
        for row in raw
    ]


def build_real_pdf_bytes(lines: Sequence[str]) -> bytes:
    """Render lines of text into a valid in-memory PDF via PyMuPDF."""
    with pymupdf.open() as document:
        page = document.new_page(width=_STANDARD_PDF_WIDTH, height=_STANDARD_PDF_HEIGHT)
        for idx, line_text in enumerate(lines):
            y_pos = 50.0 + (idx * 22.0)
            page.insert_text((50.0, y_pos), line_text)
        return bytes(document.tobytes())


def build_real_docx_bytes(body_text: str) -> bytes:
    """Build a minimal structurally valid OOXML DOCX archive in memory."""
    buf = BytesIO()
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        "</Types>"
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{body_text}</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    with ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document_xml)
    return buf.getvalue()
