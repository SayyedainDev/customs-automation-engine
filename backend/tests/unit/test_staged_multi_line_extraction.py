"""Unit tests for staged (per-line) multi-line extraction.

All provider calls are injected, so these tests are hermetic: no Groq, no
network. They pin the behaviour that makes staged extraction worth having -
one malformed row must not destroy the rows that were extracted correctly.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas.multi_line_extraction import (
    InvoiceLineItemCandidate,
    PackingListItemCandidate,
)
from app.services.extraction import staged_multi_line
from app.services.extraction.staged_multi_line import (
    DiscoveredLine,
    InvoiceHeaderCandidates,
    LineDiscovery,
    PackingHeaderCandidates,
    extract_invoice_staged,
    extract_packing_staged,
)


def _cf(value: Any, page: int = 1) -> dict[str, Any]:
    return {
        "value": value,
        "source_page": page,
        "confidence": 1,
        "validation_status": "verified",
        "validation_note": "",
    }


def _header_payload() -> dict[str, Any]:
    return {
        "exporter_name": _cf("Lahore Cotton Garments (Pvt.) Ltd."),
        "buyer_name": _cf("Shanghai Sample Trading Co., Ltd."),
        "invoice_number": _cf("LCG-INV-2026-002"),
        "invoice_date": _cf("2026-06-25"),
        "currency": _cf("USD"),
        "destination_country": _cf("China"),
        "invoice_total": _cf(550.00),
        "declared_net_weight_total": _cf(75.00),
        "declared_gross_weight_total": _cf(80.00),
    }


def _invoice_line(ordinal: int) -> dict[str, Any]:
    return {
        "line_number": _cf(ordinal),
        "product_name": _cf(f"Product {ordinal}"),
        "pct_code": _cf("6109.1000"),
        "quantity": _cf(100 * ordinal),
        "unit": _cf("PCS"),
        "unit_price": _cf(5.50),
        "line_total": _cf(550.00 * ordinal),
        "net_weight": _cf(75.00),
        "gross_weight": _cf(80.00),
    }


def _packing_item(ordinal: int) -> dict[str, Any]:
    return {
        "line_number": _cf(ordinal),
        "product_name": _cf(f"Product {ordinal}"),
        "pct_code": _cf("6109.1000"),
        "quantity": _cf(100 * ordinal),
        "unit": _cf("PCS"),
        "net_weight": _cf(75.00),
        "gross_weight": _cf(80.00),
        "package_count": _cf(5),
    }


class _Provider:
    """Injected stand-in that records every staged request it receives."""

    def __init__(self, *, line_count: int, invoice: bool = True, failing_rows: set[int] | None = None):
        self.line_count = line_count
        self.invoice = invoice
        self.failing_rows = failing_rows or set()
        self.schema_names: list[str] = []

    def __call__(
        self,
        *,
        extracted_text: str,
        response_model: type[BaseModel],
        schema_name: str,
        system_prompt: str,
        user_prompt: str,
        client: Any = None,
    ) -> BaseModel:
        self.schema_names.append(schema_name)
        if response_model in (InvoiceHeaderCandidates, PackingHeaderCandidates):
            payload = _header_payload()
            if response_model is PackingHeaderCandidates:
                payload = {
                    "declared_net_weight_total": _cf(75.00),
                    "declared_gross_weight_total": _cf(80.00),
                    "declared_package_count_total": _cf(5),
                }
            return response_model.model_validate(payload)
        if response_model is LineDiscovery:
            return LineDiscovery(
                line_count=self.line_count,
                lines=[
                    DiscoveredLine(
                        line_number=i,
                        source_page=1,
                        product_name_hint=f"Product {i}",
                    )
                    for i in range(1, self.line_count + 1)
                ],
            )
        ordinal = int(schema_name.rsplit("_", 1)[-1])
        if ordinal in self.failing_rows:
            raise ValidationError.from_exception_data("row", [])
        builder = _invoice_line if response_model is InvoiceLineItemCandidate else _packing_item
        return response_model.model_validate(builder(ordinal))


@pytest.fixture()
def patch_provider(monkeypatch: pytest.MonkeyPatch):
    def _apply(provider: _Provider) -> _Provider:
        monkeypatch.setattr(
            staged_multi_line, "extract_structured_model_from_text", provider
        )
        return provider

    return _apply


@pytest.mark.parametrize("line_count", [1, 2, 3, 5])
def test_staged_invoice_supports_multiple_line_counts(patch_provider, line_count: int) -> None:
    provider = patch_provider(_Provider(line_count=line_count))
    result = extract_invoice_staged("<page number=\"1\">invoice</page>")
    assert len(result.line_items) == line_count
    assert result.invoice_number.value == "LCG-INV-2026-002"
    assert [item.line_number.value for item in result.line_items] == list(
        range(1, line_count + 1)
    )
    # header + discovery + one request per line
    assert len(provider.schema_names) == line_count + 2


def test_header_and_lines_are_separate_requests(patch_provider) -> None:
    provider = patch_provider(_Provider(line_count=3))
    extract_invoice_staged("<page number=\"1\">invoice</page>")
    assert provider.schema_names[0] == "staged_invoice_header"
    assert provider.schema_names[1] == "staged_invoice_line_discovery"
    assert provider.schema_names[2:] == [
        "staged_invoice_line_1",
        "staged_invoice_line_2",
        "staged_invoice_line_3",
    ]


def test_one_malformed_row_preserves_the_valid_rows(patch_provider) -> None:
    """The whole point: row 2 fails, rows 1 and 3 must survive intact."""
    patch_provider(_Provider(line_count=3, failing_rows={2}))
    result = extract_invoice_staged("<page number=\"1\">invoice</page>")

    assert len(result.line_items) == 3
    good_first, bad, good_last = result.line_items
    assert good_first.product_name.value == "Product 1"
    assert good_last.product_name.value == "Product 3"
    assert good_last.line_total.value == Decimal("1650.00")

    # Only the failing row is degraded, and it is degraded to manual_review
    # rather than being dropped or invented.
    assert bad.product_name.value is None
    assert bad.quantity.value is None
    assert bad.product_name.validation_status.value == "manual_review"
    assert "manual review" in bad.product_name.validation_note.lower()


def test_zero_discovered_lines_yields_no_items(patch_provider) -> None:
    patch_provider(_Provider(line_count=0))
    result = extract_invoice_staged("<page number=\"1\">invoice</page>")
    assert result.line_items == []


def test_runaway_line_count_is_rejected(patch_provider) -> None:
    patch_provider(_Provider(line_count=staged_multi_line.MAX_DISCOVERABLE_LINES + 1))
    with pytest.raises(ValueError, match="safety limit"):
        extract_invoice_staged("<page number=\"1\">invoice</page>")


def test_staged_packing_list_extraction(patch_provider) -> None:
    provider = patch_provider(_Provider(line_count=2, invoice=False))
    result = extract_packing_staged("<page number=\"1\">packing</page>")
    assert len(result.items) == 2
    assert result.items[0].package_count.value == 5
    assert provider.schema_names[0] == "staged_packing_header"
    assert provider.schema_names[1] == "staged_packing_line_discovery"


def test_staged_result_still_validates_against_the_real_model(patch_provider) -> None:
    """Staging must not become a way to smuggle in unvalidated output."""
    patch_provider(_Provider(line_count=2))
    result = extract_invoice_staged("<page number=\"1\">invoice</page>")
    # Round-tripping through the strict model must succeed unchanged.
    from app.schemas.multi_line_extraction import MultiLineInvoiceCandidates

    reparsed = MultiLineInvoiceCandidates.model_validate(result.model_dump(mode="json"))
    assert len(reparsed.line_items) == 2
