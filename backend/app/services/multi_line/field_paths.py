"""Allowlisted field-path grammar shared by correction application and the
customs-audit dependency map.

Lives at this layer (not in customs_audit) because both the low-level
correction-application code in multi_line_shipment_service.py and the
higher-level customs_audit.dependency_map need the identical grammar, and
customs_audit is the layer that depends on multi_line services - not the
other way around.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The field group deliberately excludes leading/trailing underscores (no
#: dunder-shaped name like ``__class__`` can ever match) - this regex is
#: itself the first allowlist, not just a shape check a dependency table
#: happens to also reject.
_FIELD_NAME = r"[a-z]+(?:_[a-z]+)*"
_ITEM_FIELD_PATH = re.compile(
    r"^(?P<document>invoice|packing_list)\.(?:line_items|items)\[(?P<item_index>[1-9]\d*)\]\.(?P<field>"
    + _FIELD_NAME
    + r")$"
)
_HEADER_FIELD_PATH = re.compile(
    r"^(?P<document>invoice|packing_list)\.(?P<field>" + _FIELD_NAME + r")$"
)


@dataclass(frozen=True)
class ParsedFieldPath:
    document: str  # "invoice" | "packing_list"
    field: str
    item_index: int | None  # None for a header-level field


class InvalidFieldPathError(ValueError):
    """Raised for a field_path that does not match the allowed grammar."""


def parse_field_path(field_path: str) -> ParsedFieldPath:
    """Parse a field path against the allowlisted grammar, or raise.

    Grammar: ``"<document>.line_items[<item_index>].<field>"`` or
    ``"<document>.items[<item_index>].<field>"`` for a line item, or
    ``"<document>.<field>"`` for a header-level value. ``item_index`` is the
    model's own stable 1-based ``item_index`` (see InvoiceLineItem /
    PackingListItem), never an array position - positions shift if an item
    is re-matched, item_index does not.
    """
    match = _ITEM_FIELD_PATH.match(field_path)
    if match:
        return ParsedFieldPath(
            document=match.group("document"),
            field=match.group("field"),
            item_index=int(match.group("item_index")),
        )
    match = _HEADER_FIELD_PATH.match(field_path)
    if match:
        return ParsedFieldPath(
            document=match.group("document"), field=match.group("field"), item_index=None
        )
    raise InvalidFieldPathError(f"unrecognized field_path: {field_path!r}")
