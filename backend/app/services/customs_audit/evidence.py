"""Split compliance-check evidence into two independently-sourced kinds.

Every check the deterministic engine already decided - passed, failed, or
manual_review - gets an evidence record attached here. Nothing in this module
computes or changes a status; it only explains one that was already frozen.

Two kinds, because they come from different places and fail differently:

* **Document evidence** - a value read straight from the invoice or packing
  list that the deterministic engine already compared (quantity, weight, PCT
  code...). No retrieval, no LLM: the value and its page/confidence are
  already sitting in the extraction result.
* **Regulatory evidence** - retrieved from the hybrid RAG pipeline for a check
  that cites a government source or SRO. Retrieval runs for a check
  regardless of whether it passed or failed, so "a certificate of origin is
  not required here" is backed by a citation exactly like "it is required" -
  the citation explains the rule, not the verdict.

A regulatory check for which retrieval finds nothing is reported as
``evidence_status="unavailable"``, never a fabricated citation.
"""

from __future__ import annotations

from typing import Any

#: check_id -> (field label, [(document_key, field_name), ...]) for checks that
#: compare one named field between the two uploaded documents.
_ITEM_FIELD_MAP: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "item_quantity_match": ("quantity", (("invoice", "quantity"), ("packing_list", "quantity"))),
    "item_net_weight_match": ("net weight", (("invoice", "net_weight"), ("packing_list", "net_weight"))),
    "item_gross_weight_match": (
        "gross weight",
        (("invoice", "gross_weight"), ("packing_list", "gross_weight")),
    ),
    "item_pct_code_match": ("PCT code", (("invoice", "pct_code"),)),
    # Depends on three invoice fields at once, not a single comparison - all
    # three are shown so the arithmetic itself is visible, not just a page
    # reference. Two check ids for the same real-world calculation (one from
    # the cross-document layer, one from the compliance-rule layer - see
    # line_item_checks.py and arithmetic_checks.py) share this mapping.
    "item_line_calculation": (
        "quantity, unit price and line total",
        (
            ("invoice", "quantity"),
            ("invoice", "unit_price"),
            ("invoice", "line_total"),
        ),
    ),
    "invoice_line_calculation": (
        "quantity, unit price and line total",
        (
            ("invoice", "quantity"),
            ("invoice", "unit_price"),
            ("invoice", "line_total"),
        ),
    ),
}

#: Same shape, for checks that compare a shipment/header-level field.
_HEADER_FIELD_MAP: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "sum_line_totals_match_invoice_total": ("invoice total", (("invoice", "invoice_total"),)),
    "invoice_total_consistency": ("invoice total", (("invoice", "invoice_total"),)),
    "invoice_net_weight_total": ("declared net weight", (("invoice", "declared_net_weight_total"),)),
    "invoice_gross_weight_total": (
        "declared gross weight",
        (("invoice", "declared_gross_weight_total"),),
    ),
    "packing_net_weight_total": (
        "packing-list net weight",
        (("packing_list", "declared_net_weight_total"),),
    ),
    "packing_gross_weight_total": (
        "packing-list gross weight",
        (("packing_list", "declared_gross_weight_total"),),
    ),
}

_DOCUMENT_LABELS = {"invoice": "Commercial invoice", "packing_list": "Packing list"}

_SNIPPET_MAX_CHARS = 320


#: Not a real citation source: arithmetic_checks.py uses this exact string as
#: ``source_document`` on positive_quantity/positive_unit_price/
#: invoice_line_calculation/invoice_total_consistency to label them as
#: internal math, not a government rule. Found live: those checks were
#: routed through RAG anyway (any non-empty source_document counted as
#: "regulatory"), and a query built from "Quantity greater than zero" has no
#: real regulatory content to match, so retrieval returned whatever
#: low-relevance passage happened to be nearby - a citation to an unrelated
#: Export Policy Order appendix, attached to a check that is just "is this
#: number bigger than zero".
_ARITHMETIC_SOURCE_LABEL = "Shipment invoice arithmetic"

#: check_ids that mean "this prototype's configured rule data covers this
#: input" - a statement about the software's own scope, not a government
#: requirement. Only one exists today (see general_checks.py's
#: mvp_pct_support); this is a lookup table, not a heuristic, so it can never
#: misclassify a real regulatory check as scope or vice versa.
_SYSTEM_SCOPE_CHECK_IDS = frozenset({"mvp_pct_support"})


def is_system_scope_check(check: dict[str, Any]) -> bool:
    """True for a check that only asserts the prototype supports this input."""
    return str(check.get("check_id") or "") in _SYSTEM_SCOPE_CHECK_IDS


def is_regulatory_check(check: dict[str, Any]) -> bool:
    """A check that cites a government source, not a two-document comparison."""
    if is_system_scope_check(check):
        return False
    source = check.get("source_document")
    if source == _ARITHMETIC_SOURCE_LABEL:
        return False
    return bool(source or check.get("sro_number"))


def system_scope_statement(check: dict[str, Any]) -> str:
    """Plain statement of prototype coverage - never phrased as a legal claim."""
    pct_code = check.get("pct_code")
    if pct_code:
        return f"PCT {pct_code} is supported by this CACE prototype."
    return "This input is supported by this CACE prototype."


def _field_evidence(doc_key: str, field_name: str, field: Any) -> dict[str, Any] | None:
    if not isinstance(field, dict) or field.get("value") is None:
        return None
    return {
        "document_type": _DOCUMENT_LABELS[doc_key],
        "page_number": field.get("source_page"),
        "field_name": field_name,
        "extracted_value": field.get("value"),
        "extraction_method": field.get("extraction_method"),
        "confidence": field.get("confidence"),
    }


def _find_item(extraction_result: dict[str, Any], item_reference: str | None) -> dict[str, Any] | None:
    if item_reference is None:
        return None
    for item in extraction_result.get("items") or []:
        if item.get("item_reference") == item_reference:
            return item
    return None


def document_evidence_for_check(
    check: dict[str, Any],
    extraction_result: dict[str, Any],
    *,
    item_reference: str | None = None,
) -> list[dict[str, Any]]:
    """Evidence read directly from the extracted invoice/packing-list values.

    Falls back to the check's own recorded page references when no field
    mapping is known for this check id, so a comparison check never ships
    with zero evidence attached.
    """
    check_id = str(check.get("check_id") or "")
    invoice = extraction_result.get("invoice") or {}
    packing = extraction_result.get("packing_list") or {}

    if check_id in _HEADER_FIELD_MAP:
        _, field_refs = _HEADER_FIELD_MAP[check_id]
        documents = {"invoice": invoice, "packing_list": packing}
        evidence = [
            _field_evidence(doc_key, field_name, documents[doc_key].get(field_name))
            for doc_key, field_name in field_refs
        ]
        return [item for item in evidence if item is not None]

    if check_id in _ITEM_FIELD_MAP:
        item = _find_item(extraction_result, item_reference)
        invoice_line = None
        packing_item = None
        if item is not None:
            invoice_index = item.get("invoice_item_index")
            packing_index = item.get("packing_item_index")
            invoice_line = next(
                (
                    line
                    for line in invoice.get("line_items") or []
                    if line.get("item_index") == invoice_index
                ),
                None,
            )
            packing_item = next(
                (
                    entry
                    for entry in packing.get("items") or []
                    if entry.get("item_index") == packing_index
                ),
                None,
            )
        _, field_refs = _ITEM_FIELD_MAP[check_id]
        source_by_doc = {"invoice": invoice_line, "packing_list": packing_item}
        evidence = []
        for doc_key, field_name in field_refs:
            source = source_by_doc[doc_key]
            if source is None:
                continue
            evidence.append(_field_evidence(doc_key, field_name, source.get(field_name)))
        return [item for item in evidence if item is not None]

    # No explicit field mapping for this check id: fall back to whatever page
    # references the check itself carries.
    fallback: list[dict[str, Any]] = []
    for doc_key, page_key in (
        ("invoice", "invoice_source_page"),
        ("packing_list", "packing_list_source_page"),
    ):
        page = check.get(page_key)
        if page is None:
            continue
        fallback.append(
            {
                "document_type": _DOCUMENT_LABELS[doc_key],
                "page_number": page,
                "field_name": None,
                "extracted_value": None,
                "extraction_method": None,
                "confidence": None,
            }
        )
    return fallback


#: document_type values that come from a project-curated summary record
#: rather than a page of an official government document - see sources.py's
#: ingestion, which tags this one type this way. A curated record is
#: reviewed against official sources but is not itself a government
#: publication, so it must never be labelled as one.
_CURATED_DOCUMENT_TYPES = frozenset({"product_requirements_structured"})

#: How many citations are shown expanded by default; the rest are still
#: returned (never dropped) but flagged ``display_primary=False`` so the
#: caller can put them under a collapsed "additional sources" detail instead
#: of repeating a full card for every near-duplicate match. This is a display
#: ranking only - it says nothing about legal authority. A curated project
#: record (see ``source_kind``) can be ``display_primary=True`` if it is the
#: strongest match; it is never thereby a primary *legal* source.
_MAX_DISPLAY_PRIMARY_CITATIONS = 2


def _source_kind(document_type: Any) -> str:
    if document_type in _CURATED_DOCUMENT_TYPES:
        return "curated"
    return "official"


def normalize_regulatory_evidence(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shape retrieved evidence into the citation fields a reader needs.

    Reads defensively (``.get`` with a default) because test fakes and older
    callers may supply only the legacy subset of keys; anything absent is
    reported as absent rather than guessed. Deduplicates identical
    source/page combinations and marks only the first
    ``_MAX_DISPLAY_PRIMARY_CITATIONS`` with ``display_primary=True`` - the
    caller decides how to render the rest, but they are never silently
    discarded. ``display_primary`` is purely about which citations are shown
    expanded versus collapsed; ``source_kind`` (curated/official) is the
    separate, authoritative field for legal provenance - do not conflate the
    two.
    """
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for item in raw_items:
        key = (
            item.get("source_document_id") or item.get("source_document"),
            item.get("page_number"),
        )
        if key in seen:
            continue
        seen.add(key)

        snippet = item.get("evidence_text")
        if isinstance(snippet, str) and len(snippet) > _SNIPPET_MAX_CHARS:
            snippet = snippet[: _SNIPPET_MAX_CHARS - 1].rstrip() + "…"
        document_type = item.get("document_type")
        normalized.append(
            {
                "source_title": item.get("source_document"),
                "source_document_id": item.get("source_document_id")
                or item.get("document_checksum"),
                "source_kind": _source_kind(document_type),
                "issuing_authority": item.get("issuing_authority"),
                "sro_number": item.get("sro_number"),
                "page_number": item.get("page_number"),
                "section": item.get("section"),
                "snippet": snippet,
                "retrieval_score": item.get("retrieval_score") or item.get("rrf_score"),
                "rerank_score": item.get("rerank_score") or item.get("cross_encoder_score"),
                "validation_status": item.get("validation_status"),
                "display_primary": len(normalized) < _MAX_DISPLAY_PRIMARY_CITATIONS,
            }
        )
    return normalized


#: Ranked from strongest to weakest; evidence_status_for_regulatory returns
#: the first one that applies, so a single conflicting item never gets
#: masked by other, unrelated verified items in the same result set.
def evidence_status_for_regulatory(raw_items: list[dict[str, Any]]) -> str:
    """Honest, graded label for what retrieval actually returned.

    - ``evidence_unavailable``: nothing cleared the relevance floor. Never
      filled with an unrelated passage to avoid an empty result.
    - ``evidence_conflicting``: at least one candidate's own validation
      status says the sources disagree.
    - ``evidence_verified``: every candidate comes from a fully verified
      source.
    - ``evidence_partial``: candidates exist and are not conflicting, but at
      least one is only partially verified - real evidence, weaker
      confidence, reported as such rather than rounded up.
    """
    if not raw_items:
        return "evidence_unavailable"
    if any(item.get("validation_status") == "conflicting" for item in raw_items):
        return "evidence_conflicting"
    if all(item.get("validation_status") == "verified" for item in raw_items):
        return "evidence_verified"
    return "evidence_partial"
