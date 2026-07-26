from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile, is_zipfile

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.documents import DocumentUploadRecord
from app.schemas.documents import DocumentUploadResponse

ALLOWED_FILE_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
REQUIRED_DOCX_FILES = {
    "[Content_Types].xml",
    "word/document.xml",
}

settings = get_settings()
UPLOAD_DIRECTORY = settings.upload_dir
MAX_FILE_SIZE = settings.max_upload_size_bytes


def validate_file_content(file_extension: str, file_content: bytes) -> None:
    """Check that the uploaded bytes match the selected file format."""
    if file_extension == ".pdf":
        if not file_content.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The uploaded file does not appear to be a valid PDF.",
            )
        return

    if not is_zipfile(BytesIO(file_content)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded file does not appear to be a valid DOCX file.",
        )

    try:
        with ZipFile(BytesIO(file_content)) as docx_archive:
            archived_files = set(docx_archive.namelist())
    except BadZipFile as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded DOCX file is corrupted.",
        ) from exc

    if not REQUIRED_DOCX_FILES.issubset(archived_files):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded ZIP does not contain a valid Word document.",
        )


async def process_document_upload(
    file: UploadFile,
    db: Session,
) -> DocumentUploadResponse:
    """Validate, store, and persist metadata for one uploaded document."""
    try:
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The uploaded file must have a filename.",
            )

        file_extension = Path(file.filename).suffix.lower()
        if file_extension not in ALLOWED_FILE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Only PDF and DOCX files are supported.",
            )

        expected_content_type = ALLOWED_FILE_TYPES[file_extension]
        if file.content_type != expected_content_type:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"The uploaded {file_extension} file has an unsupported MIME type.",
            )

        file_content = await file.read()
        if not file_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The uploaded file is empty.",
            )

        if len(file_content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"The uploaded file is larger than {settings.max_upload_size_mb} MB.",
            )

        validate_file_content(file_extension, file_content)

        try:
            UPLOAD_DIRECTORY.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="The server could not create the upload directory.",
            ) from exc

        document_id = uuid4()
        stored_filename = f"{document_id}{file_extension}"
        stored_path = UPLOAD_DIRECTORY / stored_filename

        try:
            stored_path.write_bytes(file_content)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="The server could not save the uploaded file.",
            ) from exc

        upload_record = DocumentUploadRecord(
            id=document_id,
            original_filename=file.filename,
            stored_filename=stored_filename,
            file_extension=file_extension,
            mime_type=expected_content_type,
            size_bytes=len(file_content),
            status="uploaded",
        )

        try:
            db.add(upload_record)
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            try:
                stored_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="The server could not save the document metadata.",
            ) from exc

        return DocumentUploadResponse(
            document_id=document_id,
            original_filename=file.filename,
            stored_filename=stored_filename,
            size_bytes=len(file_content),
            status="uploaded",
        )
    finally:
        await file.close()
