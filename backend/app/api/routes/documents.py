from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.core.exceptions import (
    DocumentNotFoundError,
    DocumentPageNotFoundError,
    PdfExtractionError,
    StoredDocumentNotFoundError,
    UnsupportedDocumentTypeError,
)
from app.models.documents import DocumentRecord, DocumentUploadRecord
from app.schemas.documents import (
    DocumentCreate,
    DocumentExtractionResponse,
    DocumentMetadataResponse,
    DocumentPagePreviewResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from app.services.document_service import (
    extract_uploaded_pdf,
    get_uploaded_document_by_id,
    get_uploaded_pdf_page_preview,
    render_uploaded_pdf_page_image,
)
from app.services.document_upload_service import process_document_upload


router = APIRouter(prefix="/documents", tags=["Documents"])


# Static paths must be registered before /{document_id}, otherwise FastAPI can
# try to parse the word "upload" as an integer document ID.
@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db_session),
) -> DocumentUploadResponse:
    return await process_document_upload(file=file, db=db)


@router.get(
    "/uploads/{document_id}",
    response_model=DocumentMetadataResponse,
)
def get_uploaded_document(
    document_id: UUID,
    db: Session = Depends(get_db_session),
) -> DocumentUploadRecord:
    try:
        return get_uploaded_document_by_id(db=db, document_id=document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Uploaded document {document_id} was not found.",
        ) from exc


@router.post(
    "/uploads/{document_id}/extract",
    response_model=DocumentExtractionResponse,
)
def extract_document(
    document_id: UUID,
    db: Session = Depends(get_db_session),
) -> DocumentExtractionResponse:
    try:
        return extract_uploaded_pdf(db=db, document_id=document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Uploaded document {document_id} was not found.",
        ) from exc
    except StoredDocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The database record exists, but its stored file is missing.",
        ) from exc
    except UnsupportedDocumentTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc
    except PdfExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The document extraction state could not be saved.",
        ) from exc


def _preview_http_error(document_id: UUID, exc: Exception) -> HTTPException:
    if isinstance(exc, DocumentNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Uploaded document {document_id} was not found.",
        )
    if isinstance(exc, (StoredDocumentNotFoundError, DocumentPageNotFoundError)):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    if isinstance(exc, UnsupportedDocumentTypeError):
        return HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        )
    if isinstance(exc, PdfExtractionError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="The document preview could not be generated.",
    )


@router.get(
    "/uploads/{document_id}/pages/{page_number}",
    response_model=DocumentPagePreviewResponse,
)
def get_uploaded_document_page_preview(
    document_id: UUID,
    page_number: int,
    highlight: str | None = Query(default=None),
    field_name: str | None = Query(default=None),
    db: Session = Depends(get_db_session),
) -> DocumentPagePreviewResponse:
    try:
        return get_uploaded_pdf_page_preview(
            db=db,
            document_id=document_id,
            page_number=page_number,
            highlight=highlight,
            field_name=field_name,
        )
    except (
        DocumentNotFoundError,
        StoredDocumentNotFoundError,
        DocumentPageNotFoundError,
        UnsupportedDocumentTypeError,
        PdfExtractionError,
    ) as exc:
        raise _preview_http_error(document_id, exc) from exc


@router.get("/uploads/{document_id}/pages/{page_number}/image")
def get_uploaded_document_page_image(
    document_id: UUID,
    page_number: int,
    dpi: int = Query(default=150, ge=72, le=300),
    db: Session = Depends(get_db_session),
) -> Response:
    try:
        png_bytes = render_uploaded_pdf_page_image(
            db=db,
            document_id=document_id,
            page_number=page_number,
            dpi=dpi,
        )
    except (
        DocumentNotFoundError,
        StoredDocumentNotFoundError,
        DocumentPageNotFoundError,
        UnsupportedDocumentTypeError,
        PdfExtractionError,
    ) as exc:
        raise _preview_http_error(document_id, exc) from exc
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/", response_model=list[DocumentResponse], include_in_schema=False)
def list_documents(db: Session = Depends(get_db_session)) -> list[DocumentRecord]:
    statement = select(DocumentRecord).order_by(DocumentRecord.id)
    return list(db.scalars(statement).all())


@router.post(
    "/",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def create_document(
    payload: DocumentCreate,
    db: Session = Depends(get_db_session),
) -> DocumentRecord:
    document = DocumentRecord(
        file_name=payload.file_name,
        document_type=payload.document_type.value,
        exporter_name=payload.exporter_name,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


@router.get("/{document_id}", response_model=DocumentResponse, include_in_schema=False)
def get_document(
    document_id: int,
    db: Session = Depends(get_db_session),
) -> DocumentRecord:
    document = db.get(DocumentRecord, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        )
    return document


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
)
def delete_document(
    document_id: int,
    db: Session = Depends(get_db_session),
) -> None:
    document = db.get(DocumentRecord, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        )
    db.delete(document)
    db.commit()
