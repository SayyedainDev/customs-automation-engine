from pydantic import BaseModel, Field


class ExtractedLineItem(BaseModel):
    description: str | None = None
    pct_code: str | None = None
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = None
    unit_price: float | None = Field(default=None, ge=0)
    total_value: float | None = Field(default=None, ge=0)
    net_weight_kg: float | None = Field(default=None, ge=0)
    gross_weight_kg: float | None = Field(default=None, ge=0)


class ExtractedShipment(BaseModel):
    exporter_name: str | None = None
    exporter_ntn: str | None = None
    consignee_name: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    currency: str | None = None
    declared_total: float | None = Field(default=None, ge=0)
    total_net_weight_kg: float | None = Field(default=None, ge=0)
    total_gross_weight_kg: float | None = Field(default=None, ge=0)
    items: list[ExtractedLineItem] = Field(default_factory=list)
