"""Public entrypoint for the hybrid extractor.

Produces ``MultiLineInvoiceCandidates`` - byte-for-byte the same shape the
legacy single-shot extractor returns - so the deterministic compliance engine
downstream is untouched. The only difference is how the values were obtained
and how much they cost.

Field provenance is preserved in each ``CandidateField``'s ``validation_note``
so an auditor can see which values came from a pattern and which from the
model. Confidence is mapped conservatively: regex evidence is strong but the
downstream provenance gate still applies its own thresholds, and nothing here
is allowed to present an unresolved field as verified.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from app.schemas.multi_line_extraction import (
    InvoiceLineItemCandidate,
    MultiLineInvoiceCandidates,
    MultiLinePackingListCandidates,
    PackingListItemCandidate,
)
from app.schemas.shipment_extraction import CandidateField, FieldValidationStatus
from app.services.extraction.regex_extractor import (
    FieldResult,
    RegexExtraction,
    extract_document,
)
from app.services.extraction.telemetry import DocumentTelemetry

logger = logging.getLogger(__name__)

#: Confidence attached to a label-anchored regex hit. Deliberately below 1.0:
#: the pattern proves the label/value adjacency, not that OCR read it perfectly.
REGEX_LABELED_CONFIDENCE = Decimal("0.95")
REGEX_BARE_CONFIDENCE = Decimal("0.85")
LLM_GAPFILL_CONFIDENCE = Decimal("0.80")

_CONFIDENCE_BY_METHOD: dict[str, Decimal] = {
    "regex_labeled": REGEX_LABELED_CONFIDENCE,
    "regex_table": REGEX_LABELED_CONFIDENCE,
    # Same class of evidence as "regex_table": the value came from a cell
    # whose column was named by a header, not from a bare pattern match.
    "regex_stacked_table": REGEX_LABELED_CONFIDENCE,
    "regex_bare": REGEX_BARE_CONFIDENCE,
    "llm_gapfill": LLM_GAPFILL_CONFIDENCE,
}

#: hybrid field name -> invoice schema field name.
_INVOICE_FIELD_MAP: dict[str, str] = {
    "exporter_name": "exporter_name",
    "consignee_name": "buyer_name",
    "invoice_number": "invoice_number",
    "invoice_date": "invoice_date",
    "currency": "currency",
    "country_of_destination": "destination_country",
    "total_invoice_value": "invoice_total",
    "net_weight": "declared_net_weight_total",
    "gross_weight": "declared_gross_weight_total",
}

_LINE_FIELD_MAP: dict[str, str] = {
    "description": "product_name",
    "pct_code": "pct_code",
    "quantity": "quantity",
    "unit": "unit",
    "unit_price": "unit_price",
    "line_total": "line_total",
}

#: hybrid field name -> packing-list header schema field name. Packing lists
#: only ever declare weight and package-count totals, never a price, so this
#: map is deliberately smaller than the invoice one.
_PACKING_HEADER_FIELD_MAP: dict[str, str] = {
    "net_weight": "declared_net_weight_total",
    "gross_weight": "declared_gross_weight_total",
    "number_of_packages": "declared_package_count_total",
}

#: Packing-list header fields that are a whole count, not a decimal amount -
#: everything else in _PACKING_HEADER_FIELD_MAP coerces through _decimal.
_PACKING_HEADER_INT_FIELDS: frozenset[str] = frozenset({"declared_package_count_total"})

#: hybrid line-item field name -> packing-list item schema field name. A
#: packing-list table has no price column; per-item net_weight/gross_weight/
#: package_count have no reconstructable column either (`_HEADER_PHRASES`
#: deliberately leaves "net wt"/"gross wt"/"packages" unmapped, same as the
#: invoice mapper's per-line weights), so they stay unresolved here too.
_PACKING_LINE_FIELD_MAP: dict[str, str] = {
    "description": "product_name",
    "pct_code": "pct_code",
    "quantity": "quantity",
    "unit": "unit",
}


def _decimal(raw: Any) -> Decimal | None:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _int(raw: Any) -> int | None:
    try:
        return int(Decimal(str(raw)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _candidate(
    result: FieldResult | None,
    *,
    page: int | None,
    coerce: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """Render one FieldResult as the CandidateField payload the schema wants."""
    if result is None or not result.resolved:
        reason = (result.reason if result else "") or "not found in the document"
        return {
            "value": None,
            "source_page": None,
            "confidence": Decimal("0"),
            "validation_status": FieldValidationStatus.MANUAL_REVIEW.value,
            "validation_note": f"hybrid extractor: {reason}",
        }

    value: Any = result.value
    if coerce is not None:
        value = coerce(value)
        if value is None:
            return {
                "value": None,
                "source_page": None,
                "confidence": Decimal("0"),
                "validation_status": FieldValidationStatus.MANUAL_REVIEW.value,
                "validation_note": (
                    f"hybrid extractor: {result.value!r} could not be converted "
                    "to the type this field requires"
                ),
            }

    return {
        "value": value,
        "source_page": page,
        "confidence": _CONFIDENCE_BY_METHOD.get(result.method, REGEX_BARE_CONFIDENCE),
        "validation_status": FieldValidationStatus.VERIFIED.value,
        "validation_note": f"hybrid extractor: {result.method}",
    }


def _missing(note: str) -> dict[str, Any]:
    return {
        "value": None,
        "source_page": None,
        "confidence": Decimal("0"),
        "validation_status": FieldValidationStatus.MANUAL_REVIEW.value,
        "validation_note": f"hybrid extractor: {note}",
    }


def invoice_gapfill_fields() -> tuple[str, ...]:
    """Header field names that actually feed the invoice schema.

    ``extract_document`` resolves against the generic ``DOCUMENT_FIELDS``
    superset, which includes fields belonging to other document types
    (Bill of Lading, GD, Form-E numbers) that a commercial invoice never
    carries. Scoping gap-fill to this list keeps the one allowed call from
    being wasted on fields the invoice schema does not even use.
    """
    return tuple(_INVOICE_FIELD_MAP)


def packing_gapfill_fields() -> tuple[str, ...]:
    """Header field names that actually feed the packing-list schema."""
    return tuple(_PACKING_HEADER_FIELD_MAP)


def to_invoice_candidates(
    extraction: RegexExtraction, *, page: int | None = 1
) -> MultiLineInvoiceCandidates:
    """Map a hybrid extraction onto the existing invoice candidate schema."""
    payload: dict[str, Any] = {}
    for schema_field in MultiLineInvoiceCandidates.model_fields:
        if schema_field == "line_items":
            continue
        source = next(
            (
                hybrid_name
                for hybrid_name, mapped in _INVOICE_FIELD_MAP.items()
                if mapped == schema_field
            ),
            None,
        )
        if source is None:
            payload[schema_field] = _missing(
                "this field has no deterministic pattern yet"
            )
            continue
        coerce = _decimal if schema_field in {
            "invoice_total",
            "declared_net_weight_total",
            "declared_gross_weight_total",
        } else None
        payload[schema_field] = _candidate(
            extraction.fields.get(source), page=page, coerce=coerce
        )

    line_items: list[dict[str, Any]] = []
    for index, item in enumerate(extraction.line_items, start=1):
        line_payload: dict[str, Any] = {
            "line_number": {
                "value": index,
                "source_page": page,
                "confidence": REGEX_LABELED_CONFIDENCE,
                "validation_status": FieldValidationStatus.VERIFIED.value,
                "validation_note": "hybrid extractor: table row position",
            }
        }
        for schema_field in InvoiceLineItemCandidate.model_fields:
            if schema_field == "line_number":
                continue
            source = next(
                (
                    hybrid_name
                    for hybrid_name, mapped in _LINE_FIELD_MAP.items()
                    if mapped == schema_field
                ),
                None,
            )
            if source is None:
                # Weights are not columns of every invoice table; the existing
                # single-line declared-total fallback handles that downstream.
                line_payload[schema_field] = _missing(
                    "not a reconstructed column on this table"
                )
                continue
            coerce = _decimal if schema_field in {
                "quantity",
                "unit_price",
                "line_total",
                "net_weight",
                "gross_weight",
            } else None
            line_payload[schema_field] = _candidate(
                item.get(source), page=page, coerce=coerce
            )
        line_items.append(line_payload)

    payload["line_items"] = line_items
    return MultiLineInvoiceCandidates.model_validate(payload)


def extract_invoice_hybrid(
    text: str,
    *,
    page_words: list[list[tuple]] | None = None,
    document_ref: str = "invoice",
) -> tuple[MultiLineInvoiceCandidates, DocumentTelemetry]:
    """Deterministic-first extraction of one commercial invoice.

    Gap-fill is intentionally *not* invoked here. This function is the
    zero-token path used by ``--no-llm`` and by the coverage report; the caller
    that owns a Groq client decides whether the remaining gaps are worth one
    targeted call. Keeping the provider out of this module is what makes the
    whole layer testable without quota.
    """
    extraction = extract_document(text, page_words=page_words)
    candidates = to_invoice_candidates(extraction)

    resolved = sum(1 for result in extraction.fields.values() if result.resolved)
    telemetry = DocumentTelemetry(
        document_ref=document_ref,
        fields_total=len(extraction.fields),
        fields_from_regex=resolved,
        fields_missing=len(extraction.fields) - resolved,
        line_items_from_table=len(extraction.line_items),
    )
    if extraction.table_reconstruction_failed:
        telemetry.notes.append(
            "line-item table could not be reconstructed from coordinates"
        )
    return candidates, telemetry


def to_packing_candidates(
    extraction: RegexExtraction, *, page: int | None = 1
) -> MultiLinePackingListCandidates:
    """Map a hybrid extraction onto the existing packing-list candidate schema.

    Structural mirror of ``to_invoice_candidates`` - same confidence mapping,
    same "no pattern yet" fallback for unmapped schema fields.
    """
    payload: dict[str, Any] = {}
    for schema_field in MultiLinePackingListCandidates.model_fields:
        if schema_field == "items":
            continue
        source = next(
            (
                hybrid_name
                for hybrid_name, mapped in _PACKING_HEADER_FIELD_MAP.items()
                if mapped == schema_field
            ),
            None,
        )
        if source is None:
            payload[schema_field] = _missing(
                "this field has no deterministic pattern yet"
            )
            continue
        payload[schema_field] = _candidate(
            extraction.fields.get(source),
            page=page,
            coerce=_int if schema_field in _PACKING_HEADER_INT_FIELDS else _decimal,
        )

    items: list[dict[str, Any]] = []
    for index, item in enumerate(extraction.line_items, start=1):
        line_payload: dict[str, Any] = {
            "line_number": {
                "value": index,
                "source_page": page,
                "confidence": REGEX_LABELED_CONFIDENCE,
                "validation_status": FieldValidationStatus.VERIFIED.value,
                "validation_note": "hybrid extractor: table row position",
            }
        }
        for schema_field in PackingListItemCandidate.model_fields:
            if schema_field == "line_number":
                continue
            source = next(
                (
                    hybrid_name
                    for hybrid_name, mapped in _PACKING_LINE_FIELD_MAP.items()
                    if mapped == schema_field
                ),
                None,
            )
            if source is None:
                line_payload[schema_field] = _missing(
                    "not a reconstructed column on this table"
                )
                continue
            coerce = _decimal if schema_field == "quantity" else None
            line_payload[schema_field] = _candidate(
                item.get(source), page=page, coerce=coerce
            )
        items.append(line_payload)

    payload["items"] = items
    return MultiLinePackingListCandidates.model_validate(payload)


def extract_packing_hybrid(
    text: str,
    *,
    page_words: list[list[tuple]] | None = None,
    document_ref: str = "packing_list",
) -> tuple[MultiLinePackingListCandidates, DocumentTelemetry]:
    """Deterministic-first extraction of one packing list. Mirrors
    ``extract_invoice_hybrid`` exactly: zero-token by construction, gap-fill
    is left to the caller."""
    extraction = extract_document(text, page_words=page_words)
    candidates = to_packing_candidates(extraction)

    resolved = sum(1 for result in extraction.fields.values() if result.resolved)
    telemetry = DocumentTelemetry(
        document_ref=document_ref,
        fields_total=len(extraction.fields),
        fields_from_regex=resolved,
        fields_missing=len(extraction.fields) - resolved,
        line_items_from_table=len(extraction.line_items),
    )
    if extraction.table_reconstruction_failed:
        telemetry.notes.append(
            "line-item table could not be reconstructed from coordinates"
        )
    return candidates, telemetry
