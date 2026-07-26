from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    DocumentNotFoundError,
    PdfExtractionError,
    StoredDocumentNotFoundError,
    UnsupportedDocumentTypeError,
)
from app.models.documents import DocumentUploadRecord
from app.schemas.documents import DocumentExtractionResponse
from app.services.extraction.pdf_extractor import (
    extract_page_word_coordinates,
    extract_text_from_pdf,
)


settings = get_settings()
UPLOAD_DIRECTORY = settings.upload_dir
MAX_STORED_ERROR_LENGTH = 2_000


def get_uploaded_document_by_id(
    db: Session,
    document_id: UUID,
) -> DocumentUploadRecord:
    """Fetch upload metadata from PostgreSQL by its UUID primary key."""
    document = db.get(DocumentUploadRecord, document_id)
    if document is None:
        raise DocumentNotFoundError(document_id)
    return document


def _stored_error_message(exc: Exception) -> str:
    """Keep diagnostics in PostgreSQL without allowing unbounded error text."""
    message = str(exc).strip() or "No error details were provided."
    return f"{type(exc).__name__}: {message}"[:MAX_STORED_ERROR_LENGTH]


def _mark_extraction_failed(
    db: Session,
    document_id: UUID,
    exc: Exception,
) -> None:
    """Rollback the failed operation, then persist an independent failed state."""
    db.rollback()
    document = db.get(DocumentUploadRecord, document_id)
    if document is None:
        return

    document.status = "failed"
    document.extraction_error = _stored_error_message(exc)
    document.extracted_text = None
    document.extracted_pages = None
    document.page_count = None
    document.character_count = None
    document.extracted_at = None
    db.commit()


def extract_uploaded_pdf(
    db: Session,
    document_id: UUID,
) -> DocumentExtractionResponse:
    """Resolve an uploaded PDF, persist its text, and track every state change."""
    document = get_uploaded_document_by_id(db=db, document_id=document_id)

    try:
        if document.file_extension != ".pdf":
            raise UnsupportedDocumentTypeError(
                "Text extraction currently supports PDF files only."
            )

        stored_path = Path(UPLOAD_DIRECTORY) / document.stored_filename
        if not stored_path.is_file():
            raise StoredDocumentNotFoundError(
                "The stored PDF could not be found."
            )

        # Commit before invoking the loader so other requests can observe that
        # this document is actively being processed.
        document.status = "extracting"
        document.extraction_error = None
        document.extracted_text = None
        document.extracted_pages = None
        document.page_count = None
        document.character_count = None
        document.extracted_at = None
        db.commit()

        result = extract_text_from_pdf(stored_path)
        page_words = extract_page_word_coordinates(stored_path)

        document.extracted_text = result.text
        document.extracted_pages = [
            {
                "page_number": page.page_number,
                "text": page.text,
                "words": [
                    list(word)
                    for word in (page_words[index] if index < len(page_words) else [])
                ],
            }
            for index, page in enumerate(result.pages)
        ]
        document.page_count = result.page_count
        document.character_count = len(result.text)
        document.status = "extracted"
        document.extraction_error = None
        document.extracted_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(document)

    except Exception as exc:
        _mark_extraction_failed(db=db, document_id=document_id, exc=exc)
        if isinstance(
            exc,
            (
                PdfExtractionError,
                SQLAlchemyError,
                StoredDocumentNotFoundError,
                UnsupportedDocumentTypeError,
            ),
        ):
            raise
        raise PdfExtractionError("The PDF text extraction failed.") from exc

    return DocumentExtractionResponse(
        document_id=document.id,
        status=document.status,
        page_count=document.page_count,
        character_count=document.character_count,
    )
