from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class IssueSeverity(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"


class ValidationIssue(BaseModel):
    field: str = Field(examples=["declared_invoice_value"])
    message: str = Field(examples=["Declared value does not match the sum of line items"])
    severity: IssueSeverity


class ValidationReport(BaseModel):
    shipment_id: int
    is_compliant: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    checked_at: datetime
