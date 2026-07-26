from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ShipmentStatus(str, Enum):
    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    FLAGGED = "flagged"


class ShipmentItem(BaseModel):
    description: str = Field(min_length=1, examples=["Surgical scissors, stainless steel"])
    hs_code: str = Field(
        pattern=r"^\d{4}\.\d{4}$",
        description="Pakistan Customs Tariff code, e.g. 9018.9010",
        examples=["9018.9010"],
    )
    quantity: int = Field(gt=0)
    unit_price: float = Field(gt=0, description="Price per unit in the shipment currency")
    weight_kg: float = Field(gt=0)


class ShipmentBase(BaseModel):
    exporter_name: str = Field(min_length=2)
    destination_country: str = Field(min_length=2, examples=["Germany"])
    port_of_loading: str = Field(examples=["Karachi Port Trust"])
    currency: str = Field(default="USD", min_length=3, max_length=3)
    declared_invoice_value: float = Field(gt=0)
    items: list[ShipmentItem] = Field(min_length=1)


class ShipmentCreate(ShipmentBase):
    """Payload for creating a new shipment declaration."""
    pass


class ShipmentUpdate(BaseModel):
    """Partial update — every field is optional."""
    destination_country: str | None = None
    port_of_loading: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    declared_invoice_value: float | None = Field(default=None, gt=0)


class ShipmentResponse(ShipmentBase):
    id: int
    status: ShipmentStatus = ShipmentStatus.DRAFT
    created_at: datetime
