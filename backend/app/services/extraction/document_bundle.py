"""Shared PDF-text / OCR-fallback bundling used by every Phase 2 extractor.

Phase 2A (single line) and Phase 2C (multiple line items) both need the same
deterministic pipeline: load stored PDF pages, run the Tesseract OCR fallback
for scanned pages, mark which pages have usable text, and materialize an
untrusted provider ``CandidateField`` into a provenance-carrying
``ExtractedField``.  Keeping this logic in one module means the two phases can
never drift apart in how they gate confidence or OCR trust.
"""

from decimal import Decimal
from pathlib import Path
import re
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.documents import DocumentUploadRecord
from app.schemas.ocr import OcrPageResult, OcrValidationStatus
from app.schemas.shipment_extraction import (
    CandidateField,
    ExtractedField,
    ExtractionMethod,
    FieldValidationStatus,
    SourcePageReview,
)
from app.services import document_service
from app.services.document_service import (
    extract_uploaded_pdf,
    get_uploaded_document_by_id,
)
from app.services.extraction.ocr_extractor import (
    OcrConfigurationError,
    OcrExtractionError,
    ocr_pdf_page,
)


MIN_USEFUL_PAGE_ALPHANUMERIC_CHARACTERS = 20
MIN_VERIFIED_CONFIDENCE = Decimal("0.80")

_FieldValueT = TypeVar("_FieldValueT")


class StoredPage(BaseModel):
    page_number: int = Field(ge=1)
    text: str
    original_embedded_text: str = ""
    extraction_method: str = "pdf_embedded_text"
    ocr_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    ocr_validation_status: OcrValidationStatus | None = None
    #: PyMuPDF word coordinates for this page, for free line-item table
    #: reconstruction in EXTRACTION_MODE=hybrid. Empty for documents ingested
    #: before this field existed, and for genuinely rasterized pages that have
    #: no text layer to give coordinates for - both cases are handled the same
    #: way downstream: no table means "fall back to one LLM call", not an error.
    words: list[tuple[float, float, float, float, str, int, int, int]] = Field(
        default_factory=list
    )


class DocumentTextBundle(BaseModel):
    document_id: UUID
    document_type: str
    pages: list[StoredPage]
    reviews: list[SourcePageReview]

    @property
    def useful_pages(self) -> list[StoredPage]:
        return [page for page in self.pages if page_has_useful_text(page.text)]


def page_has_useful_text(text: str) -> bool:
    return (
        sum(character.isalnum() for character in text)
        >= MIN_USEFUL_PAGE_ALPHANUMERIC_CHARACTERS
    )


def normalized_product(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _load_stored_pages(document: DocumentUploadRecord) -> list[StoredPage]:
    if document.extracted_pages:
        pages: list[StoredPage] = []
        for raw_page in document.extracted_pages:
            pages.append(StoredPage.model_validate(raw_page))
        return sorted(pages, key=lambda page: page.page_number)

    page_count = document.page_count or 1
    pages = [
        StoredPage(
            page_number=1,
            text=document.extracted_text or "",
            original_embedded_text=document.extracted_text or "",
        )
    ]
    pages.extend(
        StoredPage(
            page_number=page_number,
            text="",
            original_embedded_text="",
        )
        for page_number in range(2, page_count + 1)
    )
    return pages


def _persist_ocr_pages(
    db: Session,
    document: DocumentUploadRecord,
    results: list[OcrPageResult],
) -> None:
    existing_by_page = {
        int(record["page_number"]): record
        for record in (document.ocr_pages or [])
        if isinstance(record, dict) and "page_number" in record
    }
    for result in results:
        existing_by_page[result.page_number] = result.model_dump(mode="json")
    document.ocr_pages = [
        existing_by_page[page_number]
        for page_number in sorted(existing_by_page)
    ]
    db.commit()


def _apply_ocr_fallback(
    db: Session,
    document: DocumentUploadRecord,
    bundle: DocumentTextBundle,
) -> DocumentTextBundle:
    pages_by_number = {page.page_number: page for page in bundle.pages}
    updated_reviews: list[SourcePageReview] = []
    ocr_results: list[OcrPageResult] = []
    pdf_path = Path(document_service.UPLOAD_DIRECTORY) / document.stored_filename

    for review in bundle.reviews:
        page = pages_by_number[review.page_number]
        if not review.requires_ocr:
            updated_reviews.append(review)
            continue

        try:
            result = ocr_pdf_page(
                pdf_path=pdf_path,
                document_id=document.id,
                page_number=page.page_number,
                original_embedded_text=page.original_embedded_text,
            )
            if not page_has_useful_text(result.ocr_text):
                result = result.model_copy(
                    update={
                        "validation_status": OcrValidationStatus.MANUAL_REVIEW,
                        "validation_note": (
                            f"{result.validation_note} OCR text is too short to "
                            "be useful for structured extraction."
                        ),
                    }
                )
            ocr_results.append(result)
            pages_by_number[page.page_number] = page.model_copy(
                update={
                    "text": result.ocr_text,
                    "extraction_method": result.extraction_method,
                    "ocr_confidence": result.ocr_confidence,
                    "ocr_validation_status": result.validation_status,
                }
            )
            updated_reviews.append(
                review.model_copy(
                    update={
                        "ocr_attempted": True,
                        "ocr_engine": result.ocr_engine,
                        "ocr_confidence": result.ocr_confidence,
                        "ocr_validation_status": result.validation_status,
                        "ocr_note": result.validation_note,
                        "review_note": (
                            "Embedded text was unavailable; the page was "
                            "processed by Tesseract OCR."
                        ),
                    }
                )
            )
        except (OcrConfigurationError, OcrExtractionError) as exc:
            failed_result = OcrPageResult(
                document_id=document.id,
                page_number=page.page_number,
                original_embedded_text=page.original_embedded_text,
                ocr_text="",
                ocr_confidence=Decimal("0"),
                validation_status=OcrValidationStatus.FAILED,
                validation_note=str(exc),
            )
            ocr_results.append(failed_result)
            updated_reviews.append(
                review.model_copy(
                    update={
                        "ocr_attempted": True,
                        "ocr_engine": "tesseract",
                        "ocr_confidence": Decimal("0"),
                        "ocr_validation_status": OcrValidationStatus.FAILED,
                        "ocr_note": str(exc),
                        "review_note": (
                            "Embedded text was unavailable and OCR failed; "
                            "manual review is required."
                        ),
                    }
                )
            )

    if ocr_results:
        _persist_ocr_pages(db, document, ocr_results)
    return bundle.model_copy(
        update={
            "pages": [
                pages_by_number[page_number]
                for page_number in sorted(pages_by_number)
            ],
            "reviews": updated_reviews,
        }
    )


def ensure_pdf_text(
    db: Session,
    document_id: UUID,
    document_type: str,
) -> DocumentTextBundle:
    document = get_uploaded_document_by_id(db=db, document_id=document_id)
    if document.status != "extracted":
        extract_uploaded_pdf(db=db, document_id=document_id)
        document = get_uploaded_document_by_id(db=db, document_id=document_id)

    pages = _load_stored_pages(document)
    pages = [
        page.model_copy(
            update={
                "original_embedded_text": page.text,
                "extraction_method": "pdf_embedded_text",
                "ocr_confidence": None,
                "ocr_validation_status": None,
            }
        )
        for page in pages
    ]
    reviews = [
        SourcePageReview(
            document_id=document.id,
            document_type=document_type,
            page_number=page.page_number,
            character_count=len(page.text),
            requires_ocr=not page_has_useful_text(page.text),
            review_note=(
                "No useful embedded text was found; OCR is required."
                if not page_has_useful_text(page.text)
                else "Embedded PDF text is available for structured extraction."
            ),
        )
        for page in pages
    ]
    bundle = DocumentTextBundle(
        document_id=document.id,
        document_type=document_type,
        pages=pages,
        reviews=reviews,
    )
    return _apply_ocr_fallback(db, document, bundle)


def page_marked_text(bundle: DocumentTextBundle) -> str:
    return "\n\n".join(
        f'<page number="{page.page_number}">\n{page.text}\n</page>'
        for page in bundle.useful_pages
    )


def page_word_lists(
    bundle: DocumentTextBundle,
) -> list[list[tuple[float, float, float, float, str, int, int, int]]]:
    """Word coordinates for every useful page, for table reconstruction."""
    return [page.words for page in bundle.useful_pages]


def materialize_field(
    candidate: CandidateField[_FieldValueT],
    bundle: DocumentTextBundle,
) -> ExtractedField[_FieldValueT]:
    """Attach trusted document provenance and gate an untrusted candidate.

    A value survives only when the model cited a page that actually has usable
    text, its confidence clears the Phase 2 threshold, and (for OCR pages) the
    OCR result itself was verified.  Anything else is nulled and routed to
    manual review so untrusted data can never reach the compliance engine.
    """

    useful_page_numbers = {page.page_number for page in bundle.useful_pages}
    value = candidate.value
    validation_status = candidate.validation_status
    note = candidate.validation_note
    # Provenance is an audit claim, so it has to name what actually read the
    # value. Only the supporting-document hybrid was recognised here; the
    # invoice/packing-list hybrid records notes of the form
    # "hybrid extractor: regex_labeled", which matched nothing and fell
    # through to the LLM default. Every deterministically-parsed invoice and
    # packing-list field was therefore reported to the auditor - and in the
    # audit trail - as having come from a language model that never ran.
    note = candidate.validation_note or ""
    method = ExtractionMethod.PDF_TEXT_LLM_STRUCTURED_OUTPUT
    if note.startswith("supporting_hybrid:llm_gapfill") or note.startswith(
        "hybrid extractor: llm_gapfill"
    ):
        method = ExtractionMethod.LLM_GAPFILL
    elif note.startswith("supporting_hybrid:regex_label") or note.startswith(
        "hybrid extractor: regex_"
    ):
        # regex_labeled, regex_bare, regex_table and regex_stacked_table are
        # all deterministic reads of the document text.
        method = ExtractionMethod.REGEX_LABEL
    elif note.startswith("supporting_hybrid:ocr_regex"):
        method = ExtractionMethod.OCR_REGEX
    elif note.startswith("supporting_hybrid:unresolved") or note.startswith(
        "hybrid extractor: unresolved"
    ):
        method = ExtractionMethod.UNRESOLVED
    ocr_confidence: Decimal | None = None
    source_page = next(
        (
            page
            for page in bundle.pages
            if page.page_number == candidate.source_page
        ),
        None,
    )

    if candidate.source_page not in useful_page_numbers:
        value = None
        validation_status = FieldValidationStatus.MANUAL_REVIEW
        if candidate.source_page is not None:
            note = (
                f"{note} The cited page {candidate.source_page} has no usable "
                "embedded or OCR text, or is outside this document."
            )
        else:
            note = f"{note} No valid source page was supplied."
    if candidate.confidence < MIN_VERIFIED_CONFIDENCE:
        value = None
        validation_status = FieldValidationStatus.MANUAL_REVIEW
        note = (
            f"{note} Confidence {candidate.confidence} is below the Phase 2 "
            f"threshold {MIN_VERIFIED_CONFIDENCE}."
        )
    if source_page and source_page.extraction_method == "tesseract_ocr":
        method = (
            ExtractionMethod.OCR_REGEX
            if method is ExtractionMethod.REGEX_LABEL
            else (
                ExtractionMethod.LLM_GAPFILL
                if method is ExtractionMethod.LLM_GAPFILL
                else ExtractionMethod.TESSERACT_OCR_LLM_STRUCTURED_OUTPUT
            )
        )
        ocr_confidence = source_page.ocr_confidence
        if (
            source_page.ocr_validation_status
            != OcrValidationStatus.VERIFIED
            or ocr_confidence is None
            or ocr_confidence < get_settings().ocr_min_confidence
        ):
            value = None
            validation_status = FieldValidationStatus.MANUAL_REVIEW
            note = (
                f"{note} The source page OCR confidence "
                f"{ocr_confidence if ocr_confidence is not None else 'is missing'} "
                "does not meet the verified OCR threshold."
            )
    if value is None:
        validation_status = FieldValidationStatus.MANUAL_REVIEW
        if not bundle.useful_pages:
            method = ExtractionMethod.NOT_EXTRACTED_OCR_REQUIRED

    return ExtractedField[_FieldValueT](
        value=value,
        source_document_id=bundle.document_id,
        source_page=(
            candidate.source_page
            if candidate.source_page in useful_page_numbers
            else None
        ),
        extraction_method=method,
        confidence=candidate.confidence,
        ocr_confidence=ocr_confidence,
        validation_status=validation_status,
        validation_note=note,
        original_field_location=(
            note.split("span=", 1)[1].split(";", 1)[0]
            if "span=" in note
            else None
        ),
    )
