from decimal import Decimal, ROUND_HALF_UP

from app.schemas.compliance import (
    ComplianceCheckResult,
    ComplianceCheckStatus,
    ShipmentComplianceInput,
)
from app.services.compliance.result_builder import build_result


MONEY_QUANTUM = Decimal("0.01")
MONEY_TOLERANCE = Decimal("0.01")


def quantize_money(value: Decimal) -> Decimal:
    """Round money to two decimal places without converting through float."""

    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _format_money(value: Decimal) -> str:
    return format(quantize_money(value), ".2f")


def check_positive_quantity(
    shipment: ShipmentComplianceInput,
    pct_code: str | None,
) -> ComplianceCheckResult:
    if shipment.quantity is None:
        return build_result(
            check_id="positive_quantity",
            check_name="Quantity greater than zero",
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message="Quantity check could not run because quantity is missing.",
            pct_code=pct_code,
            source_document="Shipment invoice arithmetic",
            validation_status="input_missing",
        )
    passed = shipment.quantity > 0
    return build_result(
        check_id="positive_quantity",
        check_name="Quantity greater than zero",
        status=(
            ComplianceCheckStatus.PASSED
            if passed
            else ComplianceCheckStatus.FAILED
        ),
        message=(
            "Quantity is greater than zero."
            if passed
            else "Quantity must be greater than zero."
        ),
        pct_code=pct_code,
        source_document="Shipment invoice arithmetic",
        validation_status="calculated_from_shipment_input",
    )


def check_positive_unit_price(
    shipment: ShipmentComplianceInput,
    pct_code: str | None,
) -> ComplianceCheckResult:
    if shipment.unit_price is None:
        return build_result(
            check_id="positive_unit_price",
            check_name="Unit price greater than zero",
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message="Unit-price check could not run because unit price is missing.",
            pct_code=pct_code,
            source_document="Shipment invoice arithmetic",
            validation_status="input_missing",
        )
    passed = shipment.unit_price > 0
    return build_result(
        check_id="positive_unit_price",
        check_name="Unit price greater than zero",
        status=(
            ComplianceCheckStatus.PASSED
            if passed
            else ComplianceCheckStatus.FAILED
        ),
        message=(
            "Unit price is greater than zero."
            if passed
            else "Unit price must be greater than zero."
        ),
        pct_code=pct_code,
        source_document="Shipment invoice arithmetic",
        validation_status="calculated_from_shipment_input",
    )


def check_line_total(
    shipment: ShipmentComplianceInput,
    pct_code: str | None,
) -> ComplianceCheckResult:
    if (
        shipment.quantity is None
        or shipment.unit_price is None
        or shipment.invoice_line_total is None
    ):
        return build_result(
            check_id="invoice_line_calculation",
            check_name="Quantity multiplied by unit price matches line total",
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message="Line-total calculation could not run because an input is missing.",
            pct_code=pct_code,
            source_document="Shipment invoice arithmetic",
            validation_status="input_missing",
        )

    calculated = quantize_money(shipment.quantity * shipment.unit_price)
    declared = quantize_money(shipment.invoice_line_total)
    difference = abs(calculated - declared)
    passed = difference <= MONEY_TOLERANCE
    return build_result(
        check_id="invoice_line_calculation",
        check_name="Quantity multiplied by unit price matches line total",
        status=(
            ComplianceCheckStatus.PASSED
            if passed
            else ComplianceCheckStatus.FAILED
        ),
        message=(
            f"Calculated line total {_format_money(calculated)} matches "
            f"the declared line total within {_format_money(MONEY_TOLERANCE)}."
            if passed
            else (
                f"Calculated line total {_format_money(calculated)} does not "
                f"match declared line total {_format_money(declared)}."
            )
        ),
        pct_code=pct_code,
        source_document="Shipment invoice arithmetic",
        validation_status="calculated_with_decimal_two_places",
    )


def check_invoice_total(
    shipment: ShipmentComplianceInput,
    pct_code: str | None,
) -> ComplianceCheckResult:
    if shipment.invoice_line_total is None or shipment.invoice_total is None:
        return build_result(
            check_id="invoice_total_consistency",
            check_name="Line totals match invoice total",
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message="Invoice-total check could not run because a total is missing.",
            pct_code=pct_code,
            source_document="Shipment invoice arithmetic",
            validation_status="input_missing",
        )

    line_total = quantize_money(shipment.invoice_line_total)
    invoice_total = quantize_money(shipment.invoice_total)
    difference = abs(line_total - invoice_total)
    passed = difference <= MONEY_TOLERANCE
    return build_result(
        check_id="invoice_total_consistency",
        check_name="Line totals match invoice total",
        status=(
            ComplianceCheckStatus.PASSED
            if passed
            else ComplianceCheckStatus.FAILED
        ),
        message=(
            "The invoice line total matches the invoice total."
            if passed
            else (
                f"Invoice line total {_format_money(line_total)} does not match "
                f"invoice total {_format_money(invoice_total)}."
            )
        ),
        pct_code=pct_code,
        source_document="Shipment invoice arithmetic",
        validation_status="calculated_with_decimal_two_places",
    )
