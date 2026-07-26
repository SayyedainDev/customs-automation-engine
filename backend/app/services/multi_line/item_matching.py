"""Deterministic invoice-item to packing-item matching for Phase 2C.

No language model is used to decide the final match.  Items are paired only
when a key uniquely identifies exactly one item on each side, tried in a fixed
priority order:

1. exact line / item reference,
2. exact normalized PCT code,
3. normalized product name.

If a key is ambiguous (it appears on more than one line on either side) the
strategy is skipped for those items and a later, more specific pairing is left
to a lower-priority strategy, or the item is reported unmatched.  Unmatched
items are never guessed; they are surfaced for manual review with a reason.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from app.schemas.multi_line_extraction import (
    InvoiceLineItem,
    ItemMatchStatus,
    ItemMatchStrategy,
    PackingListItem,
)
from app.services.compliance.rule_loader import normalize_pct_code
from app.services.extraction.document_bundle import normalized_product


_UNMATCHED_INVOICE_NOTE = (
    "This invoice line could not be matched to a packing-list item by line "
    "reference, PCT code, or product name. It may be missing from the packing "
    "list or its identifying fields need manual review."
)
_UNMATCHED_PACKING_NOTE = (
    "This packing-list item could not be matched to an invoice line by line "
    "reference, PCT code, or product name. It appears only on the packing list "
    "or its identifying fields need manual review."
)


@dataclass(frozen=True)
class ItemMatch:
    invoice_item: InvoiceLineItem | None
    packing_item: PackingListItem | None
    status: ItemMatchStatus
    strategy: ItemMatchStrategy | None
    note: str


def _line_key(
    item: InvoiceLineItem | PackingListItem,
) -> object | None:
    return item.line_number.value


def _pct_key(
    item: InvoiceLineItem | PackingListItem,
) -> object | None:
    value = item.pct_code.value
    if value is None:
        return None
    try:
        return normalize_pct_code(value)
    except ValueError:
        return None


def _product_key(
    item: InvoiceLineItem | PackingListItem,
) -> object | None:
    value = item.product_name.value
    if value is None:
        return None
    normalized = normalized_product(value)
    return normalized or None


_STRATEGIES = (
    (ItemMatchStrategy.LINE_REFERENCE, _line_key),
    (ItemMatchStrategy.PCT_CODE, _pct_key),
    (ItemMatchStrategy.PRODUCT_NAME, _product_key),
)


def _group_by_key(
    items: list,
    key_fn: Callable[..., object | None],
) -> dict[object, list]:
    groups: dict[object, list] = defaultdict(list)
    for item in items:
        key = key_fn(item)
        if key is None:
            continue
        groups[key].append(item)
    return groups


def match_line_items(
    invoice_items: list[InvoiceLineItem],
    packing_items: list[PackingListItem],
) -> list[ItemMatch]:
    """Pair items one-to-one using unique keys in a fixed priority order."""

    remaining_invoice = list(invoice_items)
    remaining_packing = list(packing_items)
    matches: list[ItemMatch] = []

    for strategy, key_fn in _STRATEGIES:
        invoice_by_key = _group_by_key(remaining_invoice, key_fn)
        packing_by_key = _group_by_key(remaining_packing, key_fn)
        for key, invoice_group in invoice_by_key.items():
            packing_group = packing_by_key.get(key, [])
            # Only accept a pairing when the key is unambiguous on both sides.
            if len(invoice_group) != 1 or len(packing_group) != 1:
                continue
            invoice_item = invoice_group[0]
            packing_item = packing_group[0]
            matches.append(
                ItemMatch(
                    invoice_item=invoice_item,
                    packing_item=packing_item,
                    status=ItemMatchStatus.MATCHED,
                    strategy=strategy,
                    note=(
                        f"Matched by {strategy.value} using key '{key}'."
                    ),
                )
            )
            remaining_invoice.remove(invoice_item)
            remaining_packing.remove(packing_item)

    for invoice_item in remaining_invoice:
        matches.append(
            ItemMatch(
                invoice_item=invoice_item,
                packing_item=None,
                status=ItemMatchStatus.INVOICE_ONLY,
                strategy=None,
                note=_UNMATCHED_INVOICE_NOTE,
            )
        )
    for packing_item in remaining_packing:
        matches.append(
            ItemMatch(
                invoice_item=None,
                packing_item=packing_item,
                status=ItemMatchStatus.PACKING_ONLY,
                strategy=None,
                note=_UNMATCHED_PACKING_NOTE,
            )
        )

    # Present matched invoice lines first, in their original invoice order,
    # then invoice-only lines, then packing-only items.
    matches.sort(
        key=lambda match: (
            0 if match.status == ItemMatchStatus.MATCHED else 1,
            match.invoice_item.item_index if match.invoice_item else 10_000,
            match.packing_item.item_index if match.packing_item else 10_000,
        )
    )
    return matches
