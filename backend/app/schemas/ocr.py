from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class OcrValidationStatus(str, Enum):
    VERIFIED = "verified"
    MANUAL_REVIEW = "manual_review"
    FAILED = "failed"


class OcrPageResult(BaseModel):
    document_id: UUID
    page_number: int = Field(ge=1)
    original_embedded_text: str
    ocr_text: str
    extraction_method: str = "tesseract_ocr"
    ocr_engine: str = "tesseract"
    ocr_confidence: Decimal = Field(ge=0, le=1)
    validation_status: OcrValidationStatus
    validation_note: str
