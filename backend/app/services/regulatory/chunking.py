"""Structure-aware parent/child chunking for regulatory text.

- Parent chunks are larger legal sections (returned as final evidence).
- Child chunks are smaller windows (used for search).

The splitter respects structural boundaries: it splits on blank lines
(paragraph / clause boundaries) and on individual lines (table rows, numbered
sub-clauses). A single paragraph or line that already exceeds the target size is
kept whole rather than cut mid-clause, so an SRO clause or a table row is never
carelessly broken.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PARENT_TARGET_CHARS = 1800
PARENT_HARD_CAP = 3200
CHILD_TARGET_CHARS = 480

# A block that begins with one of these markers starts a fresh parent section.
# Roman-numeral sub-clauses ((i), (ii), ...) belong inside their parent SRO/section
# and must not split a single notification into disconnected parent chunks.
_SECTION_START = re.compile(
    r"^\s*(S\.?\s*R\.?\s*O\.?|Sr\.?\s*No\.?|Schedule|SCHEDULE|===\s*Page)",
)


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse 3+ blank lines to a single blank-line separator.
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_parent_sections(text: str) -> list[str]:
    """Group blank-line-separated blocks into parent-sized sections."""
    cleaned = _normalize(text)
    if not cleaned:
        return []
    blocks = [block.strip() for block in cleaned.split("\n\n") if block.strip()]
    parents: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            parents.append("\n\n".join(current))
            current = []
            current_len = 0

    for block in blocks:
        starts_section = bool(_SECTION_START.match(block))
        block_len = len(block)
        if starts_section and current and current_len >= 400:
            flush()
        if current and current_len + block_len > PARENT_TARGET_CHARS:
            flush()
        current.append(block)
        current_len += block_len
        if current_len >= PARENT_HARD_CAP:
            flush()
    flush()
    return parents


#: A numbered heading as it appears in the Customs Act, 1969 and the
#: Customs Rules, 2001 - e.g. "26A. Conducting the audit.-" or
#: "114.   Restrictions which do not affect value.-". Both documents use the
#: same shape: digits, an optional one-or-two-letter suffix ("18A", "212B"),
#: a period, a capitalised title, then "." or "-".
#:
#: Verified against the Customs Act's own table of contents (section numbers
#: and titles printed in the PDF's front matter) by direct comparison during
#: development - not assumed. The Customs Rules were not independently
#: cross-checked the same way in this pass; they share the identical heading
#: shape, so the same pattern is expected to work, but treat that specific
#: claim as unverified until it is.
NUMBERED_HEADING_PATTERN = re.compile(
    r"^(?:\d+\[)?(\d{1,3})([A-Z]{0,2})\.\s+([A-Z][^.\n]{3,120}?)[.\-]",
    re.MULTILINE,
)

#: These annotated statutes carry extensive inline amendment-history
#: citations - "46[26A. Conducting the audit.-" marks that section 26A's
#: text was amended, cross-referencing footnote 46 - and those citation
#: markers are themselves numbered ("6. Omitted vide Finance Ordinance,
#: 2000...", "7. Inserted by Finance Act, 2002...") in the identical
#: "<number>. <Capitalised text>." shape as a genuine section heading.
#: PyMuPDF's extracted text carries no font-size distinction between the two
#: (checked: both render at the same body-text size in this PDF, so a
#: layout-based filter does not work either) - lexical shape cannot tell
#: them apart. What does: a real section's number is part of a
#: near-monotonically increasing sequence through the document; a footnote's
#: number is an arbitrary citation index. Almost every footnote in this
#: corpus also names what changed with one of these verbs, which a genuine
#: section title never does as its first word.
_FOOTNOTE_VERBS = re.compile(
    r"^(Inserted|Omitted|Substituted|Added|Renumbered|Deleted|Amended|Renamed)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SequentialHeadingSplit:
    #: (label, section_text) pairs, in document order.
    sections: list[tuple[str, str]]
    #: The last accepted section number, so a caller processing a document
    #: one page at a time can seed the next page's call and keep the
    #: monotonic-sequence check working across the page boundary - see
    #: ``start_last_number`` below.
    last_accepted_number: int


def split_by_sequential_numbered_headings(
    text: str,
    *,
    pattern: re.Pattern[str] = NUMBERED_HEADING_PATTERN,
    max_sequence_gap: int = 5,
    label_prefix: str = "Section",
    start_last_number: int = 0,
) -> SequentialHeadingSplit:
    """Split ``text`` into ``(label, section_text)`` pairs at genuine
    numbered headings, filtering out amendment-history citation markers that
    share the same lexical shape (see the module-level note above).

    Cross-validated against the Customs Act's real table of contents: 248 of
    a similarly-sized real section count were recovered with one duplicate
    and no wrongly-accepted footnote in a full-document dry run. Not a
    perfect parse - a genuine section number that jumps by more than
    ``max_sequence_gap`` past the last accepted one (an unusual renumbering)
    would be missed - but it is a real improvement over treating the whole
    document as blank-line-separated blocks, which respects no legal
    boundary at all.

    ``start_last_number`` exists because ingestion processes one PDF page's
    text at a time (see ``services/regulatory/ingestion.py``), not the whole
    document at once - without carrying the running section number across
    that call boundary, the monotonic check would reset to 0 on every page
    and reject the first genuine heading it saw each time. A page with no
    heading at all (deep in a long section's body) yields empty ``sections``
    with ``last_accepted_number`` unchanged, so the count still carries
    forward correctly across a heading-free page.

    ``sections`` is empty (never partial garbage) when nothing in ``text``
    matches the heading shape, so a caller can fall back to
    ``split_parent_sections`` for a document this strategy does not fit.
    """
    matches = list(pattern.finditer(text))
    if not matches:
        return SequentialHeadingSplit(sections=[], last_accepted_number=start_last_number)

    accepted: list[tuple[int, str, int]] = []  # (start_pos, label, numeric_value)
    last_num = start_last_number
    for match in matches:
        title = match.group(3).strip()
        if _FOOTNOTE_VERBS.match(title):
            continue
        try:
            num = int(match.group(1))
        except ValueError:
            continue
        if num < last_num or num > last_num + max_sequence_gap:
            continue
        label = f"{label_prefix} {match.group(1)}{match.group(2)}"
        accepted.append((match.start(), label, num))
        last_num = num

    if not accepted:
        return SequentialHeadingSplit(sections=[], last_accepted_number=start_last_number)

    sections: list[tuple[str, str]] = []
    for index, (start, label, _num) in enumerate(accepted):
        end = accepted[index + 1][0] if index + 1 < len(accepted) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append((label, body))
    return SequentialHeadingSplit(sections=sections, last_accepted_number=last_num)


def split_child_chunks(parent_text: str) -> list[str]:
    """Split one parent section into search-sized child windows by line."""
    lines = [line for line in parent_text.split("\n")]
    children: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            joined = "\n".join(current).strip()
            if joined:
                children.append(joined)
            current = []
            current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > CHILD_TARGET_CHARS:
            flush()
        current.append(line)
        current_len += line_len
    flush()
    # A parent with no usable split still yields at least itself as one child.
    return children or [parent_text.strip()]
