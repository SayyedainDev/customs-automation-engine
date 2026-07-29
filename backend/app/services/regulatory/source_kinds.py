"""Classification of indexed regulatory sources into honest provenance kinds.

The corpus mixes two very different things:

* documents published by a government body (an SRO, the Export Policy Order),
  which can be cited as regulation; and
* a CACE-authored structured summary of what those bodies require
  (``textile_product_requirements.json``), which cannot.

Before this module the UI labelled every citation "Official regulatory source",
including the curated summary. Nothing in the database distinguished them, so
the label was decoration rather than a claim the data supported. The kinds
below are derived deterministically from provenance fields that are already
persisted on every chunk - no metadata is invented, and anything that does not
match a known official pattern falls back to ``unclassified_source``, which is
never displayed as official.
"""

from __future__ import annotations

import re
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.regulatory import RegulatoryChunk

OFFICIAL_REGULATION = "official_regulation"
OFFICIAL_MANUAL = "official_manual"
OFFICIAL_TARIFF = "official_tariff"
OFFICIAL_POLICY = "official_policy"
OFFICIAL_PROCEDURE = "official_procedure"
CURATED_RULE_SUMMARY = "curated_rule_summary"
INTERNAL_TEST_FIXTURE = "internal_test_fixture"
#: Explicit "we do not know" bucket. Deliberately not one of the official
#: kinds: an unrecognised document must never inherit official standing by
#: default.
UNCLASSIFIED = "unclassified_source"

OFFICIAL_KINDS = frozenset(
    {
        OFFICIAL_REGULATION,
        OFFICIAL_MANUAL,
        OFFICIAL_TARIFF,
        OFFICIAL_POLICY,
        OFFICIAL_PROCEDURE,
    }
)

DISPLAY_LABELS = {
    OFFICIAL_REGULATION: "Official regulation",
    OFFICIAL_MANUAL: "Official manual",
    OFFICIAL_TARIFF: "Official tariff",
    OFFICIAL_POLICY: "Official policy",
    OFFICIAL_PROCEDURE: "Official procedure",
    CURATED_RULE_SUMMARY: "CACE curated rule summary",
    INTERNAL_TEST_FIXTURE: "Internal test fixture",
    UNCLASSIFIED: "Unclassified source",
}

_CURATED_MARKER = re.compile(r"\(curated\)|curated", re.IGNORECASE)
_FIXTURE_MARKER = re.compile(r"\bfixture\b|\bsynthetic\b|\btest[_\- ]bundle\b", re.IGNORECASE)
_GOVERNMENT_MARKER = re.compile(
    r"government of pakistan|ministry of|federal board of revenue|\bfbr\b"
    r"|state bank of pakistan|department of plant protection",
    re.IGNORECASE,
)

_DOCUMENT_TYPE_KINDS = {
    "psw_user_manual": OFFICIAL_MANUAL,
    "tdap_exporter_guide": OFFICIAL_MANUAL,
    "export_policy_amendment": OFFICIAL_REGULATION,
    "sro": OFFICIAL_REGULATION,
    "statutory_regulatory_order": OFFICIAL_REGULATION,
    "export_policy_order": OFFICIAL_POLICY,
    "export_policy": OFFICIAL_POLICY,
    "customs_manual": OFFICIAL_MANUAL,
    "tariff_schedule": OFFICIAL_TARIFF,
    "pakistan_customs_tariff": OFFICIAL_TARIFF,
    "clearance_procedure": OFFICIAL_PROCEDURE,
    "procedure": OFFICIAL_PROCEDURE,
    "product_requirements_structured": CURATED_RULE_SUMMARY,
}


def classify_source_kind(
    *,
    source_document: str | None,
    document_type: str | None = None,
    issuing_authority: str | None = None,
    source_path: str | None = None,
) -> str:
    """Return the provenance kind for one indexed source.

    Precedence matters. A curated marker wins over an official-looking
    ``document_type`` because the curated summary of an official rule is still
    curated; claiming otherwise is the exact defect this module exists to fix.
    """
    haystack = " ".join(
        part for part in (source_document, issuing_authority, source_path) if part
    )
    if _FIXTURE_MARKER.search(haystack):
        return INTERNAL_TEST_FIXTURE
    if _CURATED_MARKER.search(haystack):
        return CURATED_RULE_SUMMARY

    normalized_type = (document_type or "").strip().casefold()
    kind = _DOCUMENT_TYPE_KINDS.get(normalized_type)
    if kind:
        return kind
    for fragment, mapped in (
        ("manual", OFFICIAL_MANUAL),
        ("tariff", OFFICIAL_TARIFF),
        ("procedure", OFFICIAL_PROCEDURE),
        ("policy", OFFICIAL_POLICY),
        ("amendment", OFFICIAL_REGULATION),
        ("regulation", OFFICIAL_REGULATION),
    ):
        if fragment in normalized_type:
            # Only trust a bare type keyword when a government body issued it.
            if _GOVERNMENT_MARKER.search(issuing_authority or ""):
                return mapped
            return UNCLASSIFIED
    return UNCLASSIFIED


def resolve_source_kind(chunk: RegulatoryChunk) -> str:
    """The kind recorded at ingestion, or a deterministic fallback.

    Rows ingested before migration 012 carry no ``source_kind``; they are
    classified from their provenance fields instead of being assumed official.
    A stored value always wins, because it came from the source registry rather
    than from pattern-matching a title.
    """
    stored = (chunk.source_kind or "").strip()
    if stored and stored in DISPLAY_LABELS:
        return stored
    return classify_source_kind(
        source_document=chunk.source_document,
        document_type=chunk.document_type,
        issuing_authority=chunk.issuing_authority,
        source_path=chunk.source_path,
    )


def is_official(source_kind: str) -> bool:
    return source_kind in OFFICIAL_KINDS


def source_kind_label(source_kind: str) -> str:
    return DISPLAY_LABELS.get(source_kind, DISPLAY_LABELS[UNCLASSIFIED])


def referenced_official_source(chunk: RegulatoryChunk, source_kind: str) -> str | None:
    """The official document a curated summary points at, when it records one.

    Returns the URL the curated entry itself cites. Nothing is inferred: if the
    ingested row carries no ``source_url``, the caller shows no official
    reference rather than guessing which regulation is meant.
    """
    if source_kind != CURATED_RULE_SUMMARY:
        return None
    return chunk.source_url


def get_corpus_snapshot_date(db: Session) -> date | None:
    """Latest ingestion date across the active corpus, or None if empty."""
    value = db.execute(select(func.max(RegulatoryChunk.ingested_at))).scalar()
    if value is None:
        return None
    return value.date() if hasattr(value, "date") else None
