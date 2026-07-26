import csv
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO
from pathlib import Path
import shutil
import subprocess
from uuid import UUID

import pymupdf

from app.core.config import get_settings
from app.schemas.ocr import OcrPageResult, OcrValidationStatus


class OcrConfigurationError(RuntimeError):
    """Raised when the configured Tesseract executable is unavailable."""


class OcrExtractionError(RuntimeError):
    """Raised when a selected page cannot produce usable OCR text."""


def _resolve_tesseract_executable() -> str:
    configured = get_settings().ocr_executable
    executable = shutil.which(configured)
    if executable is None:
        raise OcrConfigurationError(
            f"OCR executable '{configured}' is not installed or not on PATH."
        )
    return executable


def _render_pdf_page(pdf_path: Path, page_number: int, dpi: int) -> bytes:
    try:
        with pymupdf.open(pdf_path) as document:
            if page_number < 1 or page_number > document.page_count:
                raise OcrExtractionError(
                    f"OCR page {page_number} is outside the PDF page range."
                )
            page = document.load_page(page_number - 1)
            scale = dpi / 72
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale),
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
            return pixmap.tobytes("png")
    except OcrExtractionError:
        raise
    except Exception as exc:
        raise OcrExtractionError(
            f"PDF page {page_number} could not be rendered for OCR."
        ) from exc


def _parse_tesseract_tsv(tsv_output: str) -> tuple[str, Decimal]:
    words: list[str] = []
    confidences: list[Decimal] = []
    previous_line: tuple[str, str, str, str] | None = None

    for row in csv.DictReader(StringIO(tsv_output), delimiter="\t"):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            confidence = Decimal(row.get("conf") or "-1")
        except Exception:
            continue
        if confidence < 0:
            continue

        line_key = (
            row.get("block_num") or "",
            row.get("par_num") or "",
            row.get("line_num") or "",
            row.get("page_num") or "",
        )
        if words and previous_line is not None and line_key != previous_line:
            words.append("\n")
        elif words and words[-1] != "\n":
            words.append(" ")
        words.append(text)
        confidences.append(confidence)
        previous_line = line_key

    ocr_text = "".join(words).strip()
    if not ocr_text or not confidences:
        raise OcrExtractionError("Tesseract returned no usable OCR words.")

    mean_confidence = (
        sum(confidences, start=Decimal("0"))
        / Decimal(len(confidences))
        / Decimal("100")
    ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return ocr_text, mean_confidence


def ocr_pdf_page(
    *,
    pdf_path: Path,
    document_id: UUID,
    page_number: int,
    original_embedded_text: str,
) -> OcrPageResult:
    """Render one selected page and OCR it with Tesseract TSV output."""

    settings = get_settings()
    executable = _resolve_tesseract_executable()
    image_bytes = _render_pdf_page(pdf_path, page_number, settings.ocr_dpi)
    command = [
        executable,
        "stdin",
        "stdout",
        "-l",
        settings.ocr_language,
        "--psm",
        str(settings.ocr_page_segmentation_mode),
        "tsv",
    ]
    try:
        completed = subprocess.run(
            command,
            input=image_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=settings.ocr_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrExtractionError(
            f"Tesseract timed out on page {page_number}."
        ) from exc
    except OSError as exc:
        raise OcrExtractionError(
            f"Tesseract could not start for page {page_number}."
        ) from exc

    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise OcrExtractionError(
            f"Tesseract failed on page {page_number}: "
            f"{error or 'no diagnostic was returned'}"
        )

    ocr_text, confidence = _parse_tesseract_tsv(
        completed.stdout.decode("utf-8", errors="replace")
    )
    validation_status = (
        OcrValidationStatus.VERIFIED
        if confidence >= settings.ocr_min_confidence
        else OcrValidationStatus.MANUAL_REVIEW
    )
    return OcrPageResult(
        document_id=document_id,
        page_number=page_number,
        original_embedded_text=original_embedded_text,
        ocr_text=ocr_text,
        ocr_confidence=confidence,
        validation_status=validation_status,
        validation_note=(
            "Tesseract OCR confidence meets the configured threshold."
            if validation_status == OcrValidationStatus.VERIFIED
            else "Tesseract OCR confidence is below the configured threshold."
        ),
    )
