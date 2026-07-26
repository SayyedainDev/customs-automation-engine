from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from app.schemas.shipments import (
    ShipmentCreate,
    ShipmentResponse,
    ShipmentStatus,
    ShipmentUpdate,
)
from app.schemas.validation import IssueSeverity, ValidationIssue, ValidationReport

router = APIRouter(
    prefix="/shipments",
    tags=["Shipments"],
)

# Temporary in-memory store until a real database is added.
_shipments: dict[int, ShipmentResponse] = {}
_next_id: int = 1


@router.get("/", response_model=list[ShipmentResponse])
def list_shipments(status_filter: ShipmentStatus | None = None) -> list[ShipmentResponse]:
    """List shipments, optionally filtered with ?status_filter=draft etc."""
    shipments = list(_shipments.values())
    if status_filter is not None:
        shipments = [s for s in shipments if s.status == status_filter]
    return shipments


@router.get("/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(shipment_id: int) -> ShipmentResponse:
    shipment = _shipments.get(shipment_id)
    if shipment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shipment {shipment_id} not found",
        )
    return shipment


@router.post("/", response_model=ShipmentResponse, status_code=status.HTTP_201_CREATED)
def create_shipment(payload: ShipmentCreate) -> ShipmentResponse:
    global _next_id
    shipment = ShipmentResponse(
        id=_next_id,
        created_at=datetime.now(timezone.utc),
        **payload.model_dump(),
    )
    _shipments[_next_id] = shipment
    _next_id += 1
    return shipment


@router.patch("/{shipment_id}", response_model=ShipmentResponse)
def update_shipment(shipment_id: int, payload: ShipmentUpdate) -> ShipmentResponse:
    shipment = _shipments.get(shipment_id)
    if shipment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shipment {shipment_id} not found",
        )
    # exclude_unset=True keeps only the fields the client actually sent.
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    updated = shipment.model_copy(update=updates)
    _shipments[shipment_id] = updated
    return updated


@router.delete("/{shipment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shipment(shipment_id: int) -> None:
    if shipment_id not in _shipments:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shipment {shipment_id} not found",
        )
    del _shipments[shipment_id]


@router.post("/{shipment_id}/validate", response_model=ValidationReport)
def validate_shipment(shipment_id: int) -> ValidationReport:
    """Run a basic compliance check: declared value vs sum of line items."""
    shipment = _shipments.get(shipment_id)
    if shipment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shipment {shipment_id} not found",
        )

    issues: list[ValidationIssue] = []
    calculated_value = sum(item.quantity * item.unit_price for item in shipment.items)
    if abs(calculated_value - shipment.declared_invoice_value) > 0.01:
        issues.append(
            ValidationIssue(
                field="declared_invoice_value",
                message=(
                    f"Declared value {shipment.declared_invoice_value} does not match "
                    f"the calculated line-item total {calculated_value}"
                ),
                severity=IssueSeverity.CRITICAL,
            )
        )

    is_compliant = not any(i.severity == IssueSeverity.CRITICAL for i in issues)
    new_status = ShipmentStatus.APPROVED if is_compliant else ShipmentStatus.FLAGGED
    _shipments[shipment_id] = shipment.model_copy(update={"status": new_status})

    return ValidationReport(
        shipment_id=shipment_id,
        is_compliant=is_compliant,
        issues=issues,
        checked_at=datetime.now(timezone.utc),
    )
