from dataclasses import dataclass
import logging
from pathlib import Path

import pymupdf

from app.core.exceptions import PdfExtractionError

logger = logging.getLogger(__name__)

#: One PyMuPDF word: (x0, y0, x1, y1, "text", block_no, line_no, word_no).
WordTuple = tuple[float, float, float, float, str, int, int, int]


@dataclass(frozen=True)
class ExtractedPdfPage:
    page_number: int
    text: str
    words: tuple[WordTuple, ...] = ()


@dataclass(frozen=True)
class PdfExtractorResult:
    """Plain result returned by the PDF extraction layer."""

    text: str
    page_count: int
    pages: tuple[ExtractedPdfPage, ...] = ()


def _extract_page_words(page: pymupdf.Page, page_number: int) -> tuple[WordTuple, ...]:
    """Best-effort word-coordinate extraction for a single already-open PyMuPDF page."""
    try:
        raw_words = page.get_text("words")
    except Exception:
        logger.warning(
            "Word-coordinate extraction failed for page %s", page_number, exc_info=True
        )
        return ()
    return tuple(tuple(word) for word in raw_words)


def extract_text_from_pdf(file_path: Path) -> PdfExtractorResult:
    """Load a PDF with native PyMuPDF and extract text + word coordinates in one pass."""
    try:
        document = pymupdf.open(file_path)
    except Exception as exc:
        raise PdfExtractionError(
            "Text could not be extracted from the PDF."
        ) from exc

    try:
        extracted_pages: list[ExtractedPdfPage] = []
        for index in range(document.page_count):
            page_number = index + 1
            try:
                page = document[index]
                page_text = page.get_text("text").strip()
            except Exception as exc:
                raise PdfExtractionError(
                    "Text could not be extracted from the PDF."
                ) from exc
            extracted_pages.append(
                ExtractedPdfPage(
                    page_number=page_number,
                    text=page_text,
                    words=_extract_page_words(page, page_number),
                )
            )
    finally:
        document.close()

    pages_tuple = tuple(extracted_pages)
    return PdfExtractorResult(
        text="\n\n".join(page.text for page in pages_tuple),
        page_count=len(pages_tuple),
        pages=pages_tuple,
    )


def extract_page_word_coordinates(file_path: Path) -> list[list[WordTuple]]:
    """Best-effort PyMuPDF word coordinates, one list per page.

    Preserved as a standalone helper for callers that only want word coordinates;
    ``extract_text_from_pdf`` already populates ``ExtractedPdfPage.words`` in a
    single pass so ``document_service.extract_uploaded_pdf`` does not re-open the PDF.
    """
    try:
        document = pymupdf.open(file_path)
    except Exception:
        logger.warning("Word-coordinate extraction could not open the PDF", exc_info=True)
        return []
    try:
        return [
            list(_extract_page_words(document[index], index + 1))
            for index in range(document.page_count)
        ]
    finally:
        document.close()
