from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.core.exceptions import (
    DocumentNotFoundError,
    PdfExtractionError,
    StoredDocumentNotFoundError,
    UnsupportedDocumentTypeError,
)
from app.models.documents import DocumentRecord, DocumentUploadRecord
from app.schemas.documents import (
    DocumentCreate,
    DocumentExtractionResponse,
    DocumentMetadataResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from app.services.document_service import (
    extract_uploaded_pdf,
    get_uploaded_document_by_id,
)
from app.services.document_upload_service import UPLOAD_DIRECTORY, process_document_upload


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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The document extraction state could not be saved.",
        ) from exc


@router.get("/", response_model=list[DocumentResponse])
def list_documents(db: Session = Depends(get_db_session)) -> list[DocumentRecord]:
    statement = select(DocumentRecord).order_by(DocumentRecord.id)
    return list(db.scalars(statement).all())


@router.post("/", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
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


@router.get("/{document_id}", response_model=DocumentResponse)
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


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
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
