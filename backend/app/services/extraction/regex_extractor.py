"""Deterministic first-pass extraction: everything resolvable without Groq.

Returns a ``FieldResult`` per field rather than a bare value, because the
*reason* a field is unresolved determines how cheaply the LLM can finish it:

* ``low`` means several candidates were found - the LLM is asked to pick from a
  short list, which costs a handful of tokens;
* ``missing`` means nothing matched - the LLM gets a small text window.

Distinguishing these is what keeps gap-fill cheap. Collapsing both to "no
value" would force a full-document call, which is the cost this module exists
to remove.

Nothing here ever writes a value the document does not contain.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Literal

from app.services.extraction.patterns import (
    DIGITS_ONLY_FIELDS,
    DOCUMENT_FIELDS,
    FIELD_NORMALISERS,
    FIELD_PATTERNS,
    LABEL_REQUIRED_FIELDS,
    contains_ocr_confusable_digit,
    normalise_text,
    parse_date,
)

logger = logging.getLogger(__name__)

Confidence = Literal["high", "medium", "low", "missing"]
Method = Literal[
    "regex_labeled", "regex_bare", "regex_table", "llm_gapfill", "none"
]


@dataclass
class FieldResult:
    """One field's deterministic extraction outcome."""

    value: Any | None = None
    confidence: Confidence = "missing"
    method: Method = "none"
    #: Every distinct candidate seen, for cheap LLM disambiguation.
    candidates: list[str] = dataclass_field(default_factory=list)
    #: Character offsets into the normalised source text, for gap-fill windows.
    source_span: tuple[int, int] | None = None
    #: Why the field was not resolved, when it was not.
    reason: str = ""

    @property
    def resolved(self) -> bool:
        """True when this field needs no LLM attention."""
        return self.value is not None and self.confidence in ("high", "medium")


@dataclass
class RegexExtraction:
    """Deterministic pass over one document."""

    fields: dict[str, FieldResult]
    line_items: list[dict[str, FieldResult]]
    normalised_text: str
    #: True when the line-item table could not be reconstructed locally.
    table_reconstruction_failed: bool = False

    def unresolved_fields(self) -> list[str]:
        return [name for name, result in self.fields.items() if not result.resolved]

    def coverage(self) -> float:
        if not self.fields:
            return 0.0
        resolved = sum(1 for result in self.fields.values() if result.resolved)
        return resolved / len(self.fields)


# --------------------------------------------------------------------------- #
# Field-level extraction
# --------------------------------------------------------------------------- #
def _normalise_value(field_name: str, raw: str) -> str | None:
    normaliser = FIELD_NORMALISERS.get(field_name)
    if normaliser is None:
        value = raw.strip()
        return value or None
    return normaliser(raw)


def _distinct(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def extract_field(field_name: str, text: str) -> FieldResult:
    """Run every pattern for one field and grade the outcome."""
    patterns = FIELD_PATTERNS.get(field_name)
    if not patterns:
        return FieldResult(reason="no pattern registered for this field")

    labeled_hits: list[tuple[str, tuple[int, int]]] = []
    bare_hits: list[tuple[str, tuple[int, int]]] = []

    for pattern in patterns:
        for match in pattern.regex.finditer(text):
            captured = match.group(1) if match.groups() else match.group(0)
            normalised = _normalise_value(field_name, captured)
            if normalised is None:
                continue
            span = match.span(1) if match.groups() else match.span(0)
            target = labeled_hits if pattern.tier == "labeled" else bare_hits
            target.append((normalised, span))

    # A labelled match is stronger evidence than any number of bare ones.
    if labeled_hits:
        values = _distinct([value for value, _ in labeled_hits])
        if len(values) == 1:
            value, span = labeled_hits[0]
            return _finalise(field_name, value, "high", "regex_labeled", [value], span)
        return FieldResult(
            confidence="low",
            method="regex_labeled",
            candidates=values,
            source_span=labeled_hits[0][1],
            reason=f"{len(values)} different labelled values found",
        )

    if bare_hits:
        values = _distinct([value for value, _ in bare_hits])
        if field_name in LABEL_REQUIRED_FIELDS:
            # A legal or financial figure is only trustworthy when the document
            # says what it is. An unlabelled number that happens to look right
            # is exactly the kind of guess that corrupts a verdict.
            return FieldResult(
                confidence="low",
                method="regex_bare",
                candidates=values,
                source_span=bare_hits[0][1],
                reason="value found without a label; a label is required for this field",
            )
        if len(values) == 1:
            value, span = bare_hits[0]
            return _finalise(field_name, value, "medium", "regex_bare", [value], span)
        return FieldResult(
            confidence="low",
            method="regex_bare",
            candidates=values,
            source_span=bare_hits[0][1],
            reason=f"{len(values)} different unlabelled values found",
        )

    return FieldResult(reason="no pattern matched")


def _finalise(
    field_name: str,
    value: str,
    confidence: Confidence,
    method: Method,
    candidates: list[str],
    span: tuple[int, int],
) -> FieldResult:
    """Apply field-specific post-checks that can downgrade confidence."""
    # A digits-only field that captured an OCR-confusable letter is reported,
    # never repaired: rewriting O->0 inside an NTN would be inventing a legal
    # identifier that the page does not actually show.
    if field_name in DIGITS_ONLY_FIELDS and contains_ocr_confusable_digit(value):
        return FieldResult(
            confidence="low",
            method=method,
            candidates=candidates,
            source_span=span,
            reason=(
                "capture contains a character OCR commonly confuses with a "
                "digit; needs confirmation rather than silent correction"
            ),
        )

    if field_name.endswith("_date") or field_name == "invoice_date":
        parsed = parse_date(value)
        if parsed.ambiguous:
            return FieldResult(
                confidence="low",
                method=method,
                candidates=candidates,
                source_span=span,
                reason=parsed.reason or "ambiguous day/month order",
            )
        if parsed.value is None:
            return FieldResult(
                confidence="missing",
                method=method,
                candidates=candidates,
                source_span=span,
                reason=parsed.reason or "unparseable date",
            )
        return FieldResult(
            value=parsed.value.isoformat(),
            confidence=confidence,
            method=method,
            candidates=candidates,
            source_span=span,
        )

    return FieldResult(
        value=value,
        confidence=confidence,
        method=method,
        candidates=candidates,
        source_span=span,
    )


# --------------------------------------------------------------------------- #
# Line-item table reconstruction (free, via PDF word coordinates)
# --------------------------------------------------------------------------- #
# Header phrases, longest first within each field so "unit price" wins over
# "unit", and "line total" over "total". Phrases are matched against runs of
# words, which is what separates two columns whose headings sit flush against
# each other ("Quantity" right-aligned hard against "Unit").
_HEADER_PHRASES: tuple[tuple[str, str | None], ...] = (
    # (phrase, schema field or None for a real-but-unmodelled column)
    ("product description", "description"),
    ("description of goods", "description"),
    ("description", "description"),
    ("particulars", "description"),
    ("commodity", "description"),
    ("goods", "description"),
    ("pct code", "pct_code"),
    ("hs code", "pct_code"),
    ("h s code", "pct_code"),
    ("tariff heading", "pct_code"),
    ("pct", "pct_code"),
    ("tariff", "pct_code"),
    ("quantity", "quantity"),
    ("qty", "quantity"),
    ("qnty", "quantity"),
    ("unit price", "unit_price"),
    ("price per unit", "unit_price"),
    ("rate", "unit_price"),
    ("unit", "unit"),
    ("uom", "unit"),
    ("line total", "line_total"),
    ("total amount", "line_total"),
    ("amount", "line_total"),
    ("value", "line_total"),
    # Columns that genuinely exist but are not part of the line-item schema.
    # Naming them explicitly is what stops their values being reassigned to a
    # neighbouring modelled column.
    ("net wt kg", None),
    ("gross wt kg", None),
    ("net wt", None),
    ("gross wt", None),
    ("net weight", None),
    ("gross weight", None),
    ("packages", None),
    ("line", None),
    ("sr no", None),
    ("s no", None),
    ("item no", None),
    ("no", None),
)

_MAX_PHRASE_WORDS = max(len(phrase.split()) for phrase, _ in _HEADER_PHRASES)

#: Vertical tolerance, in points, for treating words as being on the same row.
_ROW_TOLERANCE = 3.0


def _cluster_rows(words: list[tuple]) -> list[list[tuple]]:
    """Group PyMuPDF words into visual rows by their vertical midpoint."""
    rows: list[list[tuple]] = []
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        midpoint = (word[1] + word[3]) / 2
        if rows:
            last_midpoint = (rows[-1][0][1] + rows[-1][0][3]) / 2
            if abs(midpoint - last_midpoint) <= _ROW_TOLERANCE:
                rows[-1].append(word)
                continue
        rows.append([word])
    return [sorted(row, key=lambda w: w[0]) for row in rows]


#: Horizontal gap, in points, that separates two header columns. Words closer
#: than this belong to the same multi-word heading ("Unit Price", "Net Wt").
_COLUMN_GAP = 12.0


@dataclass(frozen=True)
class _Column:
    """One physical table column, whether or not it maps to a schema field."""

    left: float
    right: float
    heading: str
    #: None for a real column we do not model, e.g. "Line" or "Net Wt (KG)".
    field_name: str | None

    @property
    def centre(self) -> float:
        return (self.left + self.right) / 2


def _word_key(word: tuple) -> str:
    """Reduce a header word to comparable letters, dropping units like '(KG)'."""
    return re.sub(r"[^a-z]", "", word[4].lower())


def _identify_header(row: list[tuple]) -> list[_Column] | None:
    """Segment a header row into columns by greedy longest-phrase matching.

    Gap-based clustering was tried first and cannot work on these documents:
    the gap between two separate columns (~11pt, "Line" -> "Product") and the
    gap inside one heading (~3pt, "Product" -> "Description") overlap, and a
    right-aligned "Quantity" sits flush against "Unit" with no gap at all. Any
    single threshold therefore merges real columns or splits real headings.
    Phrase matching keys off the vocabulary instead of the geometry.

    Unmodelled columns are retained rather than discarded. Dropping them was a
    real bug: values under "Net Wt (KG)"/"Gross Wt (KG)" were reassigned to the
    nearest modelled column - "Line Total" - silently overwriting a financial
    figure with a weight.
    """
    words = sorted(row, key=lambda w: w[0])
    keys = [_word_key(word) for word in words]
    columns: list[_Column] = []
    claimed: set[str] = set()

    index = 0
    while index < len(words):
        if not keys[index]:
            index += 1
            continue
        matched_phrase: tuple[str, str | None, int] | None = None
        for span in range(min(_MAX_PHRASE_WORDS, len(words) - index), 0, -1):
            candidate = " ".join(k for k in keys[index : index + span] if k)
            for phrase, field_name in _HEADER_PHRASES:
                if candidate != phrase.replace(" ", " "):
                    continue
                if field_name is not None and field_name in claimed:
                    continue
                matched_phrase = (phrase, field_name, span)
                break
            if matched_phrase:
                break

        if matched_phrase is None:
            columns.append(
                _Column(words[index][0], words[index][2], words[index][4], None)
            )
            index += 1
            continue

        _, field_name, span = matched_phrase
        group = words[index : index + span]
        if field_name is not None:
            claimed.add(field_name)
        columns.append(
            _Column(
                group[0][0],
                group[-1][2],
                " ".join(word[4] for word in group),
                field_name,
            )
        )
        index += span

    mapped = [column for column in columns if column.field_name]
    if len(mapped) >= 3 and any(c.field_name == "description" for c in mapped):
        return columns
    return None


def _column_boundaries(columns: list[_Column]) -> list[float]:
    """Split points between adjacent columns, taken in the header's gaps.

    Nearest-centre assignment is wrong for real tables: a heading is often
    narrower than the data beneath it, so the first word of a wide cell can sit
    closer to the previous heading's centre. On a real fixture that dropped the
    leading word of "Raw cotton, other" into the unmodelled "Line" column.
    Splitting in the gap between headings assigns the whole cell correctly.
    """
    ordered = sorted(columns, key=lambda column: column.left)
    return [
        (ordered[index].right + ordered[index + 1].left) / 2
        for index in range(len(ordered) - 1)
    ]


def _assign_to_column(
    word: tuple, columns: list[_Column], boundaries: list[float]
) -> _Column | None:
    """Assign a word to the column region its centre falls inside."""
    if not columns:
        return None
    ordered = sorted(columns, key=lambda column: column.left)
    centre = (word[0] + word[2]) / 2
    for index, boundary in enumerate(boundaries):
        if centre < boundary:
            return ordered[index]
    return ordered[-1]


def reconstruct_line_items(page_words: list[list[tuple]]) -> list[dict[str, FieldResult]]:
    """Rebuild the line-item table from word coordinates, at zero token cost.

    Coordinates survive flattening far better than regex on extracted text: a
    table's columns are positional facts, whereas flattened text loses the
    association between a number and its column header. This is what removes
    the legacy one-LLM-call-per-row cost.

    Returns an empty list when no table could be identified, which the caller
    treats as "fall back to a single LLM call", never as "there are no items".
    """
    items: list[dict[str, FieldResult]] = []

    for words in page_words:
        if not words:
            continue
        rows = _cluster_rows(words)
        columns: list[_Column] | None = None

        for row in rows:
            if columns is None:
                columns = _identify_header(row)
                continue

            mapped_names = [c.field_name for c in columns if c.field_name]
            cells: dict[str, list[str]] = {name: [] for name in mapped_names}
            boundaries = _column_boundaries(columns)
            for word in row:
                assigned = _assign_to_column(word, columns, boundaries)
                # A word under an unmodelled column is dropped, not reassigned
                # to the nearest modelled one.
                if assigned is not None and assigned.field_name is not None:
                    cells[assigned.field_name].append(word[4])

            if not cells.get("description") or not any(
                cells.get(name) for name in ("quantity", "line_total", "unit_price")
            ):
                # A row with no description or no numbers is a subtotal line,
                # a page footer, or spacing - not a line item.
                continue

            item: dict[str, FieldResult] = {}
            for name, tokens in cells.items():
                raw = " ".join(tokens).strip()
                if not raw:
                    item[name] = FieldResult(reason="empty cell")
                    continue
                normalised = _normalise_value(name, raw)
                if normalised is None:
                    item[name] = FieldResult(
                        confidence="low",
                        method="regex_table",
                        candidates=[raw],
                        reason=f"cell text {raw!r} did not normalise for {name}",
                    )
                    continue
                item[name] = FieldResult(
                    value=normalised,
                    confidence="high",
                    method="regex_table",
                    candidates=[normalised],
                )
            items.append(item)

    return items


# --------------------------------------------------------------------------- #
# Document-level entrypoint
# --------------------------------------------------------------------------- #
def extract_document(
    text: str,
    *,
    page_words: list[list[tuple]] | None = None,
    fields: tuple[str, ...] = DOCUMENT_FIELDS,
) -> RegexExtraction:
    """Run the full deterministic pass over one document."""
    normalised = normalise_text(text)
    results = {name: extract_field(name, normalised) for name in fields}

    line_items: list[dict[str, FieldResult]] = []
    if page_words:
        try:
            line_items = reconstruct_line_items(page_words)
        except Exception:  # noqa: BLE001 - reconstruction is best-effort
            logger.warning("Line-item table reconstruction failed", exc_info=True)
            line_items = []

    return RegexExtraction(
        fields=results,
        line_items=line_items,
        normalised_text=normalised,
        table_reconstruction_failed=bool(page_words) and not line_items,
    )
