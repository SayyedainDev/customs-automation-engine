"""Deterministic retrieval-query construction from a compliance check.

No language model is used to decide the retrieval query. The query is built
purely from deterministic fields (check id/name, PCT code, destination, required
document, SRO number, source document, deterministic message). Exact legal
identifiers (PCT code, SRO number) are placed verbatim so BM25 keeps strong
keyword treatment and semantic retrieval cannot silently replace them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PCT = re.compile(r"\b(\d{4})\.?(\d{4})\b")
_SRO = re.compile(r"\b\d{2,4}\s*\(\s*[IiVvXx]+\s*\)\s*/\s*\d{4}\b")

# Product names for query enrichment, derived from the supported-PCT catalog
# rather than duplicated here.
from app.services.compliance.pct_catalog import product_search_hints



@dataclass
class ComplianceQueryInputs:
    check_id: str
    check_name: str | None = None
    pct_code: str | None = None
    destination_country: str | None = None
    required_document: str | None = None
    sro_number: str | None = None
    source_document: str | None = None
    message: str | None = None
    user_question: str | None = None


def _humanize(check_id: str) -> str:
    text = check_id[3:] if check_id.startswith("xr_") else check_id
    text = re.sub(r"\b\d{8}\b", "", text)
    text = text.replace("_", " ").strip()
    return re.sub(r"\s+", " ", text)


def build_compliance_query(inputs: ComplianceQueryInputs) -> str:
    """Return a deterministic retrieval query string for a compliance check."""
    parts: list[str] = []

    if inputs.pct_code:
        digits = re.sub(r"\D", "", inputs.pct_code)
        if len(digits) == 8:
            parts.append(f"PCT {digits}")
        hint = product_search_hints().get(digits)
        if hint:
            parts.append(hint)

    parts.append(_humanize(inputs.check_name or inputs.check_id))

    if inputs.required_document:
        parts.append(inputs.required_document.replace("_", " "))
    if inputs.destination_country:
        parts.append(inputs.destination_country)
    if inputs.sro_number:
        parts.append(f"SRO {inputs.sro_number}")
    elif inputs.source_document:
        # Pull an SRO identifier out of the source document title, if present.
        match = _SRO.search(inputs.source_document)
        if match:
            parts.append(f"SRO {match.group(0)}")
    if inputs.user_question and inputs.user_question.strip():
        parts.append(inputs.user_question.strip())

    # De-duplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        key = part.casefold()
        if part and key not in seen:
            seen.add(key)
            ordered.append(part)
    return " ".join(ordered)
