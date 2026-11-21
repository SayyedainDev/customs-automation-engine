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
from app.schemas.documents import DocumentExtractionResponse, DocumentPagePreviewResponse
from app.services.extraction.page_preview import (
    build_pdf_page_preview,
    render_pdf_page_png,
)
from app.services.extraction.pdf_extractor import extract_text_from_pdf


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


def resolve_stored_document_path(
    stored_filename: str | None, *, require_exists: bool = True
) -> Path:
    """Resolve a stored filename strictly inside UPLOAD_DIRECTORY."""
    if not stored_filename:
        raise StoredDocumentNotFoundError("The stored PDF could not be found.")
    upload_root = Path(UPLOAD_DIRECTORY).resolve()
    stored_path = (upload_root / stored_filename).resolve()
    if not stored_path.is_relative_to(upload_root) or (
        require_exists and not stored_path.is_file()
    ):
        raise StoredDocumentNotFoundError("The stored PDF could not be found.")
    return stored_path


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

        stored_path = resolve_stored_document_path(document.stored_filename)

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

        document.extracted_text = result.text
        document.extracted_pages = [
            {
                "page_number": page.page_number,
                "text": page.text,
                "words": [list(word) for word in page.words],
            }
            for page in result.pages
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


def _resolve_stored_pdf(
    db: Session,
    document_id: UUID,
) -> tuple[DocumentUploadRecord, Path]:
    """Validate document type, resolve the safe disk path inside UPLOAD_DIRECTORY."""
    document = get_uploaded_document_by_id(db=db, document_id=document_id)
    if document.file_extension != ".pdf":
        raise UnsupportedDocumentTypeError(
            "Page preview currently supports PDF files only."
        )
    return document, resolve_stored_document_path(document.stored_filename)


def get_uploaded_pdf_page_preview(
    db: Session,
    document_id: UUID,
    page_number: int,
    *,
    highlight: str | None = None,
    field_name: str | None = None,
) -> DocumentPagePreviewResponse:
    """Return page dimensions, image URL, and highlighted field bounding boxes."""
    document, stored_path = _resolve_stored_pdf(db=db, document_id=document_id)
    stored_pages = (
        document.extracted_pages
        if isinstance(document.extracted_pages, list)
        else None
    )
    return build_pdf_page_preview(
        stored_path,
        document_id=document.id,
        original_filename=document.original_filename,
        page_number=page_number,
        stored_pages=stored_pages,
        highlight=highlight,
        field_name=field_name,
    )


def render_uploaded_pdf_page_image(
    db: Session,
    document_id: UUID,
    page_number: int,
    *,
    dpi: int = 150,
) -> bytes:
    """Render one page of an uploaded PDF to PNG bytes."""
    _document, stored_path = _resolve_stored_pdf(db=db, document_id=document_id)
    return render_pdf_page_png(stored_path, page_number=page_number, dpi=dpi)
