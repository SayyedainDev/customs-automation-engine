from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.core.exceptions import (
    DocumentNotFoundError,
    PdfExtractionError,
    ShipmentExtractionInputError,
    StoredDocumentNotFoundError,
    StructuredExtractionConfigurationError,
    StructuredExtractionProviderError,
    UnsupportedDocumentTypeError,
)
from app.schemas.shipment_extraction import (
    ShipmentExtractionRequest,
    ShipmentExtractionResponse,
)
from app.services.shipment_extraction_service import (
    extract_validate_and_check_shipment,
)


router = APIRouter(
    prefix="/api/v1/compliance",
    tags=["Shipment extraction"],
)


@router.post(
    "/check-documents",
    response_model=ShipmentExtractionResponse,
)
async def check_uploaded_shipment_documents(
    payload: ShipmentExtractionRequest,
    db: Session = Depends(get_db_session),
) -> ShipmentExtractionResponse:
    """Extract two existing uploads, compare them, then run Phase 1 checks."""

    try:
        return extract_validate_and_check_shipment(db=db, request=payload)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Uploaded document {exc.args[0]} was not found.",
        ) from exc
    except ShipmentExtractionInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except UnsupportedDocumentTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc
    except StoredDocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PdfExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
            detail="The language model returned malformed structured data.",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The Phase 2A extraction state could not be saved.",
        ) from exc
