from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.core.exceptions import (
    DocumentNotFoundError,
    StructuredExtractionConfigurationError,
    StructuredExtractionProviderError,
    StructuredExtractionUnavailableError,
)
from app.schemas.extraction import ExtractedShipment
from app.services.structured_extraction_service import (
    structure_extracted_document,
)


router = APIRouter(prefix="/api/v1/documents", tags=["Structured extraction"])


@router.post(
    "/{document_id}/structured-extraction",
    response_model=ExtractedShipment,
)
def structured_extraction(
    document_id: UUID,
    db: Session = Depends(get_db_session),
) -> ExtractedShipment:
    try:
        return structure_extracted_document(db=db, document_id=document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Uploaded document {document_id} was not found.",
        ) from exc
    except StructuredExtractionUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except StructuredExtractionConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Structured extraction is not configured.",
        ) from exc
    except StructuredExtractionProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The language model could not structure this invoice.",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The structured extraction state could not be saved.",
        ) from exc
