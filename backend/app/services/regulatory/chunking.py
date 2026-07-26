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

PARENT_TARGET_CHARS = 1800
PARENT_HARD_CAP = 3200
CHILD_TARGET_CHARS = 480

# A block that begins with one of these markers starts a fresh parent section.
_SECTION_START = re.compile(
    r"^\s*(S\.?\s*R\.?\s*O\.?|Sr\.?\s*No\.?|Schedule|SCHEDULE|===\s*Page|\(\s*[ivx]+\s*\))",
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
        if starts_section and current:
            flush()
        if current and current_len + block_len > PARENT_TARGET_CHARS:
            flush()
        current.append(block)
        current_len += block_len
        if current_len >= PARENT_HARD_CAP:
            flush()
    flush()
    return parents


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
