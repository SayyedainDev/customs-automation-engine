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
    StructuredExtractionProviderUnavailableError,
    UnsupportedDocumentTypeError,
)
from app.schemas.multi_line_extraction import (
    MultiLineShipmentRequest,
    MultiLineShipmentResponse,
)
from app.services.multi_line_shipment_service import (
    extract_match_and_check_multi_line_shipment,
)


router = APIRouter(
    prefix="/api/v1/compliance",
    tags=["Multi-line shipment"],
)


@router.post(
    "/check-documents/multi-line",
    response_model=MultiLineShipmentResponse,
)
async def check_multi_line_shipment_documents(
    payload: MultiLineShipmentRequest,
    db: Session = Depends(get_db_session),
) -> MultiLineShipmentResponse:
    """Extract multi-line documents, match items, and check each separately."""

    try:
        return extract_match_and_check_multi_line_shipment(db=db, request=payload)
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
    except StructuredExtractionProviderUnavailableError as exc:
        # Quota exhaustion / upstream outage is an operational condition. It
        # must not be reported as an extraction defect: no shipment conclusion
        # can be drawn from a request the provider never served.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The extraction model provider is unavailable or rate limited; "
                "no extraction was performed. Retry once capacity is available."
            ),
        ) from exc
    except StructuredExtractionProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The language model returned malformed structured data.",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The Phase 2C extraction state could not be saved.",
        ) from exc
