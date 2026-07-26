"""Regression tests for DEF-001: provider-schema rejection and its fallout.

Root cause: Pydantic emits a JSON-Schema ``pattern`` containing a negative
lookahead for the *string* branch of ``Decimal`` fields. Groq's constrained
decoder rejects any schema using lookarounds/backreferences, so strict
``json_schema`` mode was refused for every Phase 2C model. The request then fell
back to unconstrained ``json_object`` mode, where the model intermittently emits
empty strings between array objects (``[obj, "", obj, "", obj]``), which fails
local Pydantic validation and discards the whole document.

These tests pin the two properties that prevent that:

1. the transport schema sent to the provider contains no unsupported regex
   construct, so strict mode is actually usable;
2. stripping that hint never weakens local validation - the real Pydantic model
   must still reject a bad value.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.multi_line_extraction import (
    InvoiceLineItemCandidate,
    MultiLineInvoiceCandidates,
    MultiLinePackingListCandidates,
)
from app.schemas.shipment_extraction import CandidateField
from app.services.structured_extraction_service import _groq_strict_schema

# Constructs Groq's constrained decoder cannot compile.
_UNSUPPORTED_REGEX = re.compile(r"\(\?=|\(\?!|\(\?<=|\(\?<!|\\[1-9]")


def _collect_patterns(node: object, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        pattern = node.get("pattern")
        if isinstance(pattern, str):
            found.append((path, pattern))
        for key, value in node.items():
            found.extend(_collect_patterns(value, f"{path}/{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_collect_patterns(value, f"{path}[{index}]"))
    return found


@pytest.mark.parametrize(
    "model",
    [MultiLineInvoiceCandidates, MultiLinePackingListCandidates, InvoiceLineItemCandidate],
)
def test_transport_schema_has_no_unsupported_regex(model: type) -> None:
    """The provider-facing schema must be compilable by a constrained decoder."""
    offending = [
        (path, pattern)
        for path, pattern in _collect_patterns(_groq_strict_schema(model))
        if _UNSUPPORTED_REGEX.search(pattern)
    ]
    assert offending == [], (
        "Transport schema still contains regex features Groq rejects "
        f"(lookarounds/backrefs): {offending}"
    )


def test_transport_schema_still_describes_line_items_as_objects() -> None:
    """Strict mode is what structurally prevents empty strings in the array."""
    schema = _groq_strict_schema(MultiLineInvoiceCandidates)
    line_items = schema["properties"]["line_items"]
    assert line_items["type"] == "array"
    assert "$ref" in line_items["items"] or line_items["items"].get("type") == "object"


def _numeric_branch_types(node: dict) -> list[str]:
    return [branch.get("type") for branch in node.get("anyOf", [])]


@pytest.mark.parametrize(
    "model",
    [MultiLineInvoiceCandidates, MultiLinePackingListCandidates, InvoiceLineItemCandidate],
)
def test_numeric_fields_do_not_offer_a_free_string_branch(model: type) -> None:
    """DEF-008: dropping only the `pattern` widened the string branch.

    Pydantic's Decimal schema is ``anyOf[number, string(pattern=...), null]``.
    Removing just the unsupported ``pattern`` left an *unconstrained* string
    branch, and the provider promptly emitted ``confidence: "unknown"``, which
    local validation then rejected - turning a schema-compatibility fix into a
    generation failure. The transport schema must drop the whole unsupported
    branch so a JSON number is the only numeric option offered.
    """
    schema = _groq_strict_schema(model)
    definitions = schema.get("$defs", {})
    offenders: list[str] = []
    for name, definition in definitions.items():
        if "CandidateField" not in name:
            continue
        for field_name, field_schema in (definition.get("properties") or {}).items():
            branches = _numeric_branch_types(field_schema)
            if "number" in branches and "string" in branches:
                offenders.append(f"{name}.{field_name}")
    assert offenders == [], (
        "Numeric transport fields still offer an unconstrained string branch, "
        f"which lets the provider emit non-numeric text: {offenders}"
    )


def test_local_validation_is_not_weakened_by_schema_stripping() -> None:
    """Removing the provider hint must not make our own model accept junk."""
    with pytest.raises(ValidationError):
        CandidateField[Decimal].model_validate(
            {
                "value": "not-a-number",
                "source_page": 1,
                "confidence": 1,
                "validation_status": "verified",
                "validation_note": "",
            }
        )
    # And a genuinely valid numeric string is still accepted.
    ok = CandidateField[Decimal].model_validate(
        {
            "value": "550.00",
            "source_page": 1,
            "confidence": 1,
            "validation_status": "verified",
            "validation_note": "",
        }
    )
    assert ok.value == Decimal("550.00")


def test_line_items_array_of_non_objects_is_still_rejected() -> None:
    """The exact malformed shape seen live must never validate silently."""
    with pytest.raises(ValidationError):
        MultiLineInvoiceCandidates.model_validate(
            {
                **{
                    name: {
                        "value": None,
                        "source_page": None,
                        "confidence": 0,
                        "validation_status": "manual_review",
                        "validation_note": "x",
                    }
                    for name in MultiLineInvoiceCandidates.model_fields
                    if name != "line_items"
                },
                "line_items": [""],
            }
        )
