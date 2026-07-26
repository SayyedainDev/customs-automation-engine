from dataclasses import dataclass
import logging
from pathlib import Path

from langchain_community.document_loaders.pdf import PyMuPDFLoader

from app.core.exceptions import PdfExtractionError

logger = logging.getLogger(__name__)

#: One PyMuPDF word: (x0, y0, x1, y1, "text", block_no, line_no, word_no).
WordTuple = tuple[float, float, float, float, str, int, int, int]


@dataclass(frozen=True)
class ExtractedPdfPage:
    page_number: int
    text: str


@dataclass(frozen=True)
class PdfExtractorResult:
    """Plain result returned by the PDF extraction layer."""

    text: str
    page_count: int
    pages: tuple[ExtractedPdfPage, ...] = ()


def extract_text_from_pdf(file_path: Path) -> PdfExtractorResult:
    """Load a PDF with LangChain and combine its page documents into one string."""
    try:
        pages = PyMuPDFLoader(str(file_path)).load()
    except Exception as exc:
        # The loader can surface errors from PyMuPDF as well as LangChain. Keep
        # those implementation details inside this layer and expose one stable
        # application exception to callers.
        raise PdfExtractionError(
            "Text could not be extracted from the PDF."
        ) from exc

    extracted_pages = tuple(
        ExtractedPdfPage(
            page_number=index,
            text=page.page_content.strip(),
        )
        for index, page in enumerate(pages, start=1)
    )
    return PdfExtractorResult(
        text="\n\n".join(page.text for page in extracted_pages),
        page_count=len(extracted_pages),
        pages=extracted_pages,
    )


def extract_page_word_coordinates(file_path: Path) -> list[list[WordTuple]]:
    """Best-effort PyMuPDF word coordinates, one list per page.

    This is a second, isolated PDF read alongside ``extract_text_from_pdf``
    rather than a replacement for it: word coordinates are only needed to
    reconstruct line-item tables for free in ``EXTRACTION_MODE=hybrid``
    (``regex_extractor.reconstruct_line_items``), and that is a local,
    zero-token capability that must never be able to break plain text
    extraction. A rasterized/scanned page has no text layer to give
    coordinates for, and any other failure is swallowed here - callers see an
    empty word list per page, never an exception.
    """
    import pymupdf  # local import, matches ocr_extractor.py's style

    try:
        document = pymupdf.open(file_path)
    except Exception:
        logger.warning("Word-coordinate extraction could not open the PDF", exc_info=True)
        return []
    try:
        page_words: list[list[WordTuple]] = []
        for index in range(document.page_count):
            try:
                words = document[index].get_text("words")
            except Exception:
                logger.warning(
                    "Word-coordinate extraction failed for page %s", index + 1, exc_info=True
                )
                words = []
            page_words.append([tuple(word) for word in words])
        return page_words
    finally:
        document.close()
