"""Deterministic per-item and shipment-level checks for Phase 2C.

Every check here is pure arithmetic or exact comparison over already-extracted,
provenance-carrying values.  No language model participates.  Missing or
uncertain inputs never silently pass: they resolve to ``manual_review`` (cannot
verify) or ``not_applicable`` (nothing to verify), following the same
fail-closed policy as the Phase 1 engine.
"""

from decimal import Decimal
from typing import Callable, TypeVar

from app.schemas.compliance import ComplianceCheckStatus
from app.schemas.multi_line_extraction import (
    InvoiceLineItem,
    ItemMatchStatus,
    MultiLineCommercialInvoiceExtraction,
    MultiLinePackingListExtraction,
    PackingListItem,
)
from app.schemas.shipment_extraction import CrossDocumentCheck, ExtractedField
from app.services.compliance.arithmetic_checks import (
    MONEY_TOLERANCE,
    quantize_money,
)
from app.services.compliance.rule_loader import normalize_pct_code
from app.services.extraction.document_bundle import normalized_product


WEIGHT_TOLERANCE = Decimal("0.01")

_ComparableT = TypeVar("_ComparableT")


def _item_comparison_check(
    *,
    check_id: str,
    check_name: str,
    invoice_field: ExtractedField[_ComparableT],
    packing_field: ExtractedField[_ComparableT],
    normalizer: Callable[[_ComparableT], object] | None = None,
    missing_status: ComplianceCheckStatus = ComplianceCheckStatus.MANUAL_REVIEW,
    missing_message: str | None = None,
) -> CrossDocumentCheck:
    invoice_value = invoice_field.value
    packing_value = packing_field.value
    if invoice_value is None or packing_value is None:
        return CrossDocumentCheck(
            check_id=check_id,
            check_name=check_name,
            status=missing_status,
            message=(
                missing_message
                or (
                    f"{check_name} could not be verified because one or both "
                    "values are missing or uncertain."
                )
            ),
            invoice_source_page=invoice_field.source_page,
            packing_list_source_page=packing_field.source_page,
        )
    left = normalizer(invoice_value) if normalizer else invoice_value
    right = normalizer(packing_value) if normalizer else packing_value
    matches = left == right
    return CrossDocumentCheck(
        check_id=check_id,
        check_name=check_name,
        status=(
            ComplianceCheckStatus.PASSED
            if matches
            else ComplianceCheckStatus.FAILED
        ),
        message=(
            f"{check_name} matches across both documents."
            if matches
            else (
                f"{check_name} mismatch: invoice has '{invoice_value}' and "
                f"packing list has '{packing_value}'."
            )
        ),
        invoice_source_page=invoice_field.source_page,
        packing_list_source_page=packing_field.source_page,
    )


def _item_line_calculation_check(
    invoice_item: InvoiceLineItem,
) -> CrossDocumentCheck:
    quantity = invoice_item.quantity.value
    unit_price = invoice_item.unit_price.value
    line_total = invoice_item.line_total.value
    if quantity is None or unit_price is None or line_total is None:
        return CrossDocumentCheck(
            check_id="item_line_calculation",
            check_name="Quantity multiplied by unit price matches line total",
            status=ComplianceCheckStatus.MANUAL_REVIEW,
            message=(
                "Line-total calculation could not run because quantity, unit "
                "price, or line total is missing or uncertain."
            ),
            invoice_source_page=invoice_item.line_total.source_page,
            packing_list_source_page=None,
        )
    calculated = quantize_money(quantity * unit_price)
    declared = quantize_money(line_total)
    passed = abs(calculated - declared) <= MONEY_TOLERANCE
    return CrossDocumentCheck(
        check_id="item_line_calculation",
        check_name="Quantity multiplied by unit price matches line total",
        status=(
            ComplianceCheckStatus.PASSED
            if passed
            else ComplianceCheckStatus.FAILED
        ),
        message=(
            f"Calculated line total {calculated} matches the declared line "
            f"total within {MONEY_TOLERANCE}."
            if passed
            else (
                f"Calculated line total {calculated} does not match declared "
                f"line total {declared}."
            )
        ),
        invoice_source_page=invoice_item.line_total.source_page,
        packing_list_source_page=None,
    )


def per_item_checks(
    invoice_item: InvoiceLineItem,
    packing_item: PackingListItem,
) -> list[CrossDocumentCheck]:
    """Cross-document and arithmetic checks for one matched shipment item."""

    return [
        _item_comparison_check(
            check_id="item_quantity_match",
            check_name="Quantity",
            invoice_field=invoice_item.quantity,
            packing_field=packing_item.quantity,
        ),
        _item_comparison_check(
            check_id="item_net_weight_match",
            check_name="Net weight",
            invoice_field=invoice_item.net_weight,
            packing_field=packing_item.net_weight,
        ),
        _item_comparison_check(
            check_id="item_gross_weight_match",
            check_name="Gross weight",
            invoice_field=invoice_item.gross_weight,
            packing_field=packing_item.gross_weight,
        ),
        _item_comparison_check(
            check_id="item_pct_code_match",
            check_name="PCT code",
            invoice_field=invoice_item.pct_code,
            packing_field=packing_item.pct_code,
            normalizer=normalize_pct_code,
            # PCT is optional on packing lists; only verify when both sides show
            # one. A missing PCT here is not a failure, it is simply skipped.
            missing_status=ComplianceCheckStatus.NOT_APPLICABLE,
            missing_message=(
                "PCT code was not printed on both documents, so the per-item "
                "PCT comparison does not apply."
            ),
        ),
        _item_line_calculation_check(invoice_item),
    ]


# --------------------------------------------------------------------------- #
# Shipment-level checks.
# --------------------------------------------------------------------------- #
def _invoice_total_sum_check(
    invoice: MultiLineCommercialInvoiceExtraction,
) -> CrossDocumentCheck:
    declared_total = invoice.invoice_total.value
    line_totals = [item.line_total.value for item in invoice.line_items]
    if declared_total is None or not line_totals or any(
        value is None for value in line_totals
    ):
        return CrossDocumentCheck(
            check_id="sum_line_totals_match_invoice_total",
            check_name="Sum of line totals matches invoice total",
            status=ComplianceCheckStatus.MANUAL_REVIEW,
            message=(
                "The invoice-total reconciliation could not run because the "
                "invoice total or at least one line total is missing or "
                "uncertain."
            ),
            invoice_source_page=invoice.invoice_total.source_page,
            packing_list_source_page=None,
        )
    present_totals = [value for value in line_totals if value is not None]
    summed = quantize_money(sum(present_totals, Decimal("0")))
    declared = quantize_money(declared_total)
    passed = abs(summed - declared) <= MONEY_TOLERANCE
    return CrossDocumentCheck(
        check_id="sum_line_totals_match_invoice_total",
        check_name="Sum of line totals matches invoice total",
        status=(
            ComplianceCheckStatus.PASSED
            if passed
            else ComplianceCheckStatus.FAILED
        ),
        message=(
            f"The line totals sum to {summed}, matching the invoice total "
            f"within {MONEY_TOLERANCE}."
            if passed
            else (
                f"The line totals sum to {summed}, which does not match the "
                f"declared invoice total {declared}."
            )
        ),
        invoice_source_page=invoice.invoice_total.source_page,
        packing_list_source_page=None,
    )


def _weight_total_check(
    *,
    check_id: str,
    check_name: str,
    item_weights: list[Decimal | None],
    declared_total: ExtractedField[Decimal],
    is_invoice: bool,
) -> CrossDocumentCheck:
    source_page = declared_total.source_page
    invoice_page = source_page if is_invoice else None
    packing_page = None if is_invoice else source_page
    declared_value = declared_total.value
    if declared_value is None:
        return CrossDocumentCheck(
            check_id=check_id,
            check_name=check_name,
            status=ComplianceCheckStatus.NOT_APPLICABLE,
            message=(
                f"{check_name} does not apply because the document does not "
                "declare this total."
            ),
            invoice_source_page=invoice_page,
            packing_list_source_page=packing_page,
        )
    if not item_weights or any(value is None for value in item_weights):
        return CrossDocumentCheck(
            check_id=check_id,
            check_name=check_name,
            status=ComplianceCheckStatus.MANUAL_REVIEW,
            message=(
                f"{check_name} could not run because at least one item weight "
                "is missing or uncertain."
            ),
            invoice_source_page=invoice_page,
            packing_list_source_page=packing_page,
        )
    present_weights = [value for value in item_weights if value is not None]
    summed = sum(present_weights, Decimal("0"))
    passed = abs(summed - declared_value) <= WEIGHT_TOLERANCE
    return CrossDocumentCheck(
        check_id=check_id,
        check_name=check_name,
        status=(
            ComplianceCheckStatus.PASSED
            if passed
            else ComplianceCheckStatus.FAILED
        ),
        message=(
            f"Item weights sum to {summed}, matching the declared total "
            f"{declared_total.value}."
            if passed
            else (
                f"Item weights sum to {summed}, which does not match the "
                f"declared total {declared_total.value}."
            )
        ),
        invoice_source_page=invoice_page,
        packing_list_source_page=packing_page,
    )


def _invoice_item_signature(item: InvoiceLineItem) -> tuple | None:
    product = (
        normalized_product(item.product_name.value)
        if item.product_name.value
        else None
    )
    pct = _normalized_pct_or_none(item.pct_code.value)
    if product is None and pct is None:
        return None
    return (
        product,
        pct,
        item.quantity.value,
        item.unit_price.value,
        item.line_total.value,
        item.net_weight.value,
        item.gross_weight.value,
    )


def _packing_item_signature(item: PackingListItem) -> tuple | None:
    product = (
        normalized_product(item.product_name.value)
        if item.product_name.value
        else None
    )
    pct = _normalized_pct_or_none(item.pct_code.value)
    if product is None and pct is None:
        return None
    return (
        product,
        pct,
        item.quantity.value,
        item.net_weight.value,
        item.gross_weight.value,
        item.package_count.value,
    )


def _normalized_pct_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return normalize_pct_code(value)
    except ValueError:
        return None


def _duplicate_check(
    *,
    check_id: str,
    check_name: str,
    items: list,
    signature_fn: Callable[..., tuple | None],
    is_invoice: bool,
) -> CrossDocumentCheck:
    line_numbers: dict[object, list[int]] = {}
    signatures: dict[tuple, list[int]] = {}
    for item in items:
        line_value = item.line_number.value
        if line_value is not None:
            line_numbers.setdefault(line_value, []).append(item.item_index)
        signature = signature_fn(item)
        if signature is not None:
            signatures.setdefault(signature, []).append(item.item_index)

    duplicate_lines = {
        line: indexes
        for line, indexes in line_numbers.items()
        if len(indexes) > 1
    }
    duplicate_signatures = [
        indexes for indexes in signatures.values() if len(indexes) > 1
    ]
    if not duplicate_lines and not duplicate_signatures:
        return CrossDocumentCheck(
            check_id=check_id,
            check_name=check_name,
            status=ComplianceCheckStatus.PASSED,
            message="No duplicate lines were detected.",
            invoice_source_page=None,
            packing_list_source_page=None,
        )

    reasons: list[str] = []
    for line, indexes in sorted(duplicate_lines.items(), key=lambda pair: str(pair[0])):
        reasons.append(
            f"line reference {line} appears on item positions "
            f"{', '.join(str(index) for index in indexes)}"
        )
    for indexes in duplicate_signatures:
        reasons.append(
            "identical line contents on item positions "
            f"{', '.join(str(index) for index in indexes)}"
        )
    return CrossDocumentCheck(
        check_id=check_id,
        check_name=check_name,
        status=ComplianceCheckStatus.FAILED,
        message=(
            f"{check_name} failed because "
            + "; ".join(reasons)
            + ". Duplicated lines corrupt the document totals."
        ),
        invoice_source_page=None,
        packing_list_source_page=None,
    )


def _single_document_items_check(matches) -> CrossDocumentCheck:
    invoice_only = [
        match.invoice_item.item_index
        for match in matches
        if match.status == ItemMatchStatus.INVOICE_ONLY
        and match.invoice_item is not None
    ]
    packing_only = [
        match.packing_item.item_index
        for match in matches
        if match.status == ItemMatchStatus.PACKING_ONLY
        and match.packing_item is not None
    ]
    if not invoice_only and not packing_only:
        return CrossDocumentCheck(
            check_id="items_present_in_both_documents",
            check_name="Every item is present in both documents",
            status=ComplianceCheckStatus.PASSED,
            message="Every item was matched across both documents.",
            invoice_source_page=None,
            packing_list_source_page=None,
        )
    parts: list[str] = []
    if invoice_only:
        parts.append(
            "invoice-only item positions "
            + ", ".join(str(index) for index in invoice_only)
        )
    if packing_only:
        parts.append(
            "packing-list-only item positions "
            + ", ".join(str(index) for index in packing_only)
        )
    return CrossDocumentCheck(
        check_id="items_present_in_both_documents",
        check_name="Every item is present in both documents",
        status=ComplianceCheckStatus.MANUAL_REVIEW,
        message=(
            "Some items appear in only one document and need manual review: "
            + "; ".join(parts)
            + "."
        ),
        invoice_source_page=None,
        packing_list_source_page=None,
    )


def shipment_level_checks(
    invoice: MultiLineCommercialInvoiceExtraction,
    packing_list: MultiLinePackingListExtraction,
    matches,
) -> list[CrossDocumentCheck]:
    """Checks that span all lines rather than a single matched item."""

    return [
        _invoice_total_sum_check(invoice),
        _weight_total_check(
            check_id="invoice_net_weight_total",
            check_name="Sum of invoice item net weights matches declared total",
            item_weights=[item.net_weight.value for item in invoice.line_items],
            declared_total=invoice.declared_net_weight_total,
            is_invoice=True,
        ),
        _weight_total_check(
            check_id="invoice_gross_weight_total",
            check_name=(
                "Sum of invoice item gross weights matches declared total"
            ),
            item_weights=[
                item.gross_weight.value for item in invoice.line_items
            ],
            declared_total=invoice.declared_gross_weight_total,
            is_invoice=True,
        ),
        _weight_total_check(
            check_id="packing_net_weight_total",
            check_name=(
                "Sum of packing-list item net weights matches declared total"
            ),
            item_weights=[item.net_weight.value for item in packing_list.items],
            declared_total=packing_list.declared_net_weight_total,
            is_invoice=False,
        ),
        _weight_total_check(
            check_id="packing_gross_weight_total",
            check_name=(
                "Sum of packing-list item gross weights matches declared total"
            ),
            item_weights=[
                item.gross_weight.value for item in packing_list.items
            ],
            declared_total=packing_list.declared_gross_weight_total,
            is_invoice=False,
        ),
        _duplicate_check(
            check_id="duplicate_invoice_lines",
            check_name="Duplicate invoice lines",
            items=invoice.line_items,
            signature_fn=_invoice_item_signature,
            is_invoice=True,
        ),
        _duplicate_check(
            check_id="duplicate_packing_items",
            check_name="Duplicate packing-list items",
            items=packing_list.items,
            signature_fn=_packing_item_signature,
            is_invoice=False,
        ),
        _single_document_items_check(matches),
    ]
