"""Mapping evidence for deterministic multi-line invoice-to-packing matching."""

from __future__ import annotations

from decimal import Decimal
from typing import TypeVar
from uuid import uuid4

import pytest

from app.schemas.multi_line_extraction import (
    InvoiceLineItem,
    ItemMatchStatus,
    ItemMatchStrategy,
    PackingListItem,
)
from app.schemas.shipment_extraction import (
    ExtractedField,
    ExtractionMethod,
    FieldValidationStatus,
)
from app.services.multi_line.item_matching import match_line_items

_ValT = TypeVar("_ValT")


def _field(val: _ValT | None) -> ExtractedField[_ValT]:
    return ExtractedField(
        value=val,
        source_document_id=uuid4(),
        source_page=1,
        extraction_method=ExtractionMethod.REGEX_LABEL,
        confidence=Decimal("0.95"),
        validation_status=FieldValidationStatus.VERIFIED,
        validation_note="deterministic test fixture field",
    )


def _invoice_line(
    item_index: int,
    *,
    line_number: int | None,
    product_name: str,
    pct_code: str,
) -> InvoiceLineItem:
    return InvoiceLineItem(
        item_index=item_index,
        line_number=_field(line_number),
        product_name=_field(product_name),
        pct_code=_field(pct_code),
        quantity=_field(Decimal("100")),
        unit=_field("PCS"),
        unit_price=_field(Decimal("5.00")),
        line_total=_field(Decimal("500.00")),
        net_weight=_field(Decimal("20.00")),
        gross_weight=_field(Decimal("22.00")),
        item_confidence=Decimal("0.95"),
        item_validation_status=FieldValidationStatus.VERIFIED,
        item_note="verified",
    )


def _packing_item(
    item_index: int,
    *,
    line_number: int | None,
    product_name: str,
    pct_code: str,
) -> PackingListItem:
    return PackingListItem(
        item_index=item_index,
        line_number=_field(line_number),
        product_name=_field(product_name),
        pct_code=_field(pct_code),
        quantity=_field(Decimal("100")),
        unit=_field("PCS"),
        net_weight=_field(Decimal("20.00")),
        gross_weight=_field(Decimal("22.00")),
        package_count=_field(5),
        item_confidence=Decimal("0.95"),
        item_validation_status=FieldValidationStatus.VERIFIED,
        item_note="verified",
    )


@pytest.mark.parametrize("strategy", list(ItemMatchStrategy))
def test_every_priority_tier_maps_unique_pairs_deterministically(
    strategy: ItemMatchStrategy,
) -> None:
    if strategy is ItemMatchStrategy.LINE_REFERENCE:
        inv = _invoice_line(1, line_number=1, product_name="Item Alpha", pct_code="6109.1000")
        pkg = _packing_item(1, line_number=1, product_name="Item Beta", pct_code="5205.1100")
    elif strategy is ItemMatchStrategy.SKU_CODE:
        inv = _invoice_line(
            1, line_number=None, product_name="T-Shirt SKU: TX-901", pct_code="6109.1000"
        )
        pkg = _packing_item(
            1, line_number=None, product_name="Garment Style #TX-901", pct_code="5205.1100"
        )
    elif strategy is ItemMatchStrategy.PCT_CODE:
        inv = _invoice_line(
            1, line_number=None, product_name="Cotton Tee A", pct_code="6109.1000"
        )
        pkg = _packing_item(
            1, line_number=None, product_name="Knitted Shirt B", pct_code="61091000"
        )
    else:
        inv = _invoice_line(
            1, line_number=None, product_name="Denim Trouser Pair", pct_code="6109.1000"
        )
        pkg = _packing_item(
            1, line_number=None, product_name="Denim Trouser Pair", pct_code="6203.4200"
        )

    matches = match_line_items([inv], [pkg])
    assert len(matches) == 1
    assert matches[0].status is ItemMatchStatus.MATCHED
    assert matches[0].strategy is strategy


def test_ambiguous_keys_leave_items_unmatched_for_review() -> None:
    inv_items = [
        _invoice_line(1, line_number=None, product_name="Cotton Yarn", pct_code="5205.1100"),
        _invoice_line(2, line_number=None, product_name="Cotton Yarn", pct_code="5205.1100"),
    ]
    pkg_items = [
        _packing_item(1, line_number=None, product_name="Cotton Yarn", pct_code="5205.1100"),
    ]

    matches = match_line_items(inv_items, pkg_items)
    statuses = [match.status for match in matches]
    assert statuses == [
        ItemMatchStatus.INVOICE_ONLY,
        ItemMatchStatus.INVOICE_ONLY,
        ItemMatchStatus.PACKING_ONLY,
    ]
    assert all(match.strategy is None for match in matches)
