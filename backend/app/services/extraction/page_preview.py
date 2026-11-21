"""PDF page rendering and bounding-box highlight locator for provenance review.

Locates exact or canonically equivalent occurrences of extracted/disputed values
using persisted PyMuPDF word tuples ``(x0, y0, x1, y1, text, ...)``, ranks and
disambiguates matches using column/label context when ``field_name`` is
supplied, and renders PNG previews of individual PDF pages on demand.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any
from uuid import UUID

import pymupdf

from app.core.exceptions import DocumentPageNotFoundError, PdfExtractionError
from app.schemas.documents import DocumentPagePreviewResponse, HighlightBox

_LINE_Y_TOLERANCE_PT = 3.0
_HEADER_X_TOLERANCE_PT = 35.0
_BBOX_PAD_PT = 2.0

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_PURE_DECIMAL_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EDGE_SYMBOLS_RE = re.compile(
    r"^(?:USD|PKR|EUR|GBP|RMB|CNY|RS\.?|KG|KGS|PCS|PC|CTNS|CTN|PKGS|PKG|MT|[$€£¥%(),:;])+|"
    r"(?:USD|PKR|EUR|GBP|RMB|CNY|RS\.?|KG|KGS|PCS|PC|CTNS|CTN|PKGS|PKG|MT|[$€£¥%(),:;])+$",
    re.IGNORECASE,
)

# Column headers and inline labels mapped by leaf field name. Specific prefixes
# like "net" vs "gross" intentionally avoid bare "weight" so Net Weight and
# Gross Weight columns never boost each other.
_FIELD_CONTEXT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "quantity": ("qty", "quantity", "units", "pcs", "pieces"),
    "net_weight": ("net", "net wt", "net weight", "n w", "nw"),
    "declared_net_weight_total": ("declared net", "net weight", "net wt", "total net"),
    "gross_weight": ("gross", "gross wt", "gross weight", "g w", "gw"),
    "declared_gross_weight_total": ("declared gross", "gross weight", "gross wt", "total gross"),
    "pct_code": ("pct", "hs", "hs code", "pct code", "tariff"),
    "unit_price": ("unit price", "rate", "price"),
    "line_total": ("line total", "amount", "value", "total"),
    "invoice_total": ("invoice total", "total amount", "total value", "grand total", "fob", "cif"),
    "package_count": ("packages", "cartons", "ctns", "pkgs", "package count", "bales"),
    "invoice_number": ("invoice no", "invoice #", "inv no", "commercial invoice"),
    "invoice_date": ("invoice date", "dated", "date"),
    "destination_country": ("destination", "country of destination", "final destination", "discharge"),
    "exporter_name": ("exporter", "shipper", "consignor", "seller"),
    "buyer_name": ("buyer", "consignee", "importer", "sold to", "notify"),
}


@dataclass(frozen=True)
class _WordBox:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    tokens: tuple[str, ...]

    @property
    def y_center(self) -> float:
        return (self.y0 + self.y1) / 2.0


@dataclass(frozen=True)
class _CandidateBox:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    match_type: str
    line_index: int


def _alnum_tokens(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(text.lower()))


def _digits_only(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _parse_decimal_core(text: str) -> Decimal | None:
    cleaned = text.replace(",", "").strip()
    # Peel off leading/trailing unit or currency markers iteratively.
    for _ in range(3):
        updated = _EDGE_SYMBOLS_RE.sub("", cleaned).strip()
        if updated == cleaned:
            break
        cleaned = updated
    if not cleaned or not _PURE_DECIMAL_RE.match(cleaned):
        return None
    try:
        dec = Decimal(cleaned)
    except InvalidOperation:
        return None
    return dec if dec.is_finite() else None


def _parse_words(raw_words: Sequence[Sequence[Any]]) -> list[list[_WordBox]]:
    parsed: list[_WordBox] = []
    for raw in raw_words:
        if len(raw) < 5:
            continue
        try:
            x0 = float(raw[0])
            y0 = float(raw[1])
            x1 = float(raw[2])
            y1 = float(raw[3])
        except (TypeError, ValueError):
            continue
        text = str(raw[4]).strip()
        if not text:
            continue
        parsed.append(
            _WordBox(
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                text=text,
                tokens=_alnum_tokens(text),
            )
        )
    if not parsed:
        return []

    parsed.sort(key=lambda w: (w.y_center, w.x0))
    lines: list[list[_WordBox]] = []
    line_centers: list[float] = []
    for word in parsed:
        if not lines or abs(word.y_center - line_centers[-1]) > _LINE_Y_TOLERANCE_PT:
            lines.append([word])
            line_centers.append(word.y_center)
        else:
            current = lines[-1]
            current.append(word)
            line_centers[-1] = sum(w.y_center for w in current) / len(current)

    for line in lines:
        line.sort(key=lambda w: w.x0)
    return lines


def _leaf_field_key(field_name: str | None) -> str:
    if not field_name:
        return ""
    leaf = field_name.strip().rsplit(".", 1)[-1]
    return leaf.split("[", 1)[0].strip().lower()


def _date_variants(iso_value: str) -> list[str]:
    stripped = iso_value.strip()
    if not _ISO_DATE_RE.match(stripped):
        return []
    try:
        dt = date.fromisoformat(stripped)
    except ValueError:
        return []
    y = dt.year
    m = dt.month
    d = dt.day
    mon_short = dt.strftime("%b")
    mon_full = dt.strftime("%B")
    return [
        stripped,
        f"{d:02d}-{m:02d}-{y}",
        f"{d:02d}/{m:02d}/{y}",
        f"{d:02d}.{m:02d}.{y}",
        f"{y}/{m:02d}/{d:02d}",
        f"{d:02d} {mon_short} {y}",
        f"{d} {mon_short} {y}",
        f"{d:02d} {mon_full} {y}",
        f"{d} {mon_full} {y}",
        f"{mon_short} {d:02d} {y}",
        f"{mon_short} {d} {y}",
        f"{mon_full} {d:02d} {y}",
        f"{mon_full} {d} {y}",
    ]


def _match_phrase_on_lines(
    lines: list[list[_WordBox]],
    query_tokens: tuple[str, ...],
    *,
    match_type: str,
) -> list[_CandidateBox]:
    if not query_tokens:
        return []
    joined_query = "".join(query_tokens)
    candidates: list[_CandidateBox] = []
    for line_idx, line in enumerate(lines):
        n = len(line)
        i = 0
        while i < n:
            matched_end: int | None = None
            acc_tokens: list[str] = []
            acc_joined = ""
            for j in range(i, n):
                w = line[j]
                if not w.tokens:
                    break
                acc_tokens.extend(w.tokens)
                acc_joined += "".join(w.tokens)
                if tuple(acc_tokens) == query_tokens or (
                    len(joined_query) >= 3 and acc_joined == joined_query
                ):
                    matched_end = j
                    break
                if len(acc_tokens) > len(query_tokens) and len(acc_joined) > len(joined_query):
                    break
                # Stop extending if already mismatched on prefix
                if (
                    len(acc_tokens) <= len(query_tokens)
                    and tuple(acc_tokens) != query_tokens[: len(acc_tokens)]
                    and not joined_query.startswith(acc_joined)
                ):
                    break
            if matched_end is not None:
                span = line[i : matched_end + 1]
                candidates.append(
                    _CandidateBox(
                        x0=min(w.x0 for w in span),
                        y0=min(w.y0 for w in span),
                        x1=max(w.x1 for w in span),
                        y1=max(w.y1 for w in span),
                        text=" ".join(w.text for w in span),
                        match_type=match_type,
                        line_index=line_idx,
                    )
                )
                i = matched_end + 1
            else:
                i += 1
    return candidates


def _match_pct_on_lines(
    lines: list[list[_WordBox]], pct_digits: str
) -> list[_CandidateBox]:
    candidates: list[_CandidateBox] = []
    for line_idx, line in enumerate(lines):
        i = 0
        n = len(line)
        while i < n:
            w = line[i]
            d1 = _digits_only(w.text)
            # Ensure the word does not contain letters (only digits and punctuation)
            if d1 == pct_digits and not any(ch.isalpha() for ch in w.text):
                candidates.append(
                    _CandidateBox(
                        x0=w.x0,
                        y0=w.y0,
                        x1=w.x1,
                        y1=w.y1,
                        text=w.text,
                        match_type="pct_code",
                        line_index=line_idx,
                    )
                )
                i += 1
                continue
            if (
                i + 1 < n
                and 2 <= len(d1) < 8
                and not any(ch.isalpha() for ch in w.text)
            ):
                w2 = line[i + 1]
                d2 = _digits_only(w2.text)
                if (
                    len(d2) >= 2
                    and not any(ch.isalpha() for ch in w2.text)
                    and d1 + d2 == pct_digits
                ):
                    candidates.append(
                        _CandidateBox(
                            x0=min(w.x0, w2.x0),
                            y0=min(w.y0, w2.y0),
                            x1=max(w.x1, w2.x1),
                            y1=max(w.y1, w2.y1),
                            text=f"{w.text} {w2.text}",
                            match_type="pct_code",
                            line_index=line_idx,
                        )
                    )
                    i += 2
                    continue
            i += 1
    return candidates


def _match_decimal_on_lines(
    lines: list[list[_WordBox]], target: Decimal
) -> list[_CandidateBox]:
    candidates: list[_CandidateBox] = []
    for line_idx, line in enumerate(lines):
        for w in line:
            parsed = _parse_decimal_core(w.text)
            if parsed is not None and parsed == target:
                candidates.append(
                    _CandidateBox(
                        x0=w.x0,
                        y0=w.y0,
                        x1=w.x1,
                        y1=w.y1,
                        text=w.text,
                        match_type="numeric_value",
                        line_index=line_idx,
                    )
                )
    return candidates


def _match_token_fallback(
    lines: list[list[_WordBox]], long_tokens: set[str]
) -> list[_CandidateBox]:
    if not long_tokens:
        return []
    candidates: list[_CandidateBox] = []
    for line_idx, line in enumerate(lines):
        matched_words = [
            w for w in line if any(tok in long_tokens for tok in w.tokens)
        ]
        if not matched_words:
            continue
        run: list[_WordBox] = [matched_words[0]]
        for w in matched_words[1:]:
            if w.x0 - run[-1].x1 <= 12.0:
                run.append(w)
            else:
                candidates.append(
                    _CandidateBox(
                        x0=min(x.x0 for x in run),
                        y0=min(x.y0 for x in run),
                        x1=max(x.x1 for x in run),
                        y1=max(x.y1 for x in run),
                        text=" ".join(x.text for x in run),
                        match_type="token",
                        line_index=line_idx,
                    )
                )
                run = [w]
        candidates.append(
            _CandidateBox(
                x0=min(x.x0 for x in run),
                y0=min(x.y0 for x in run),
                x1=max(x.x1 for x in run),
                y1=max(x.y1 for x in run),
                text=" ".join(x.text for x in run),
                match_type="token",
                line_index=line_idx,
            )
        )
    return candidates


def _rank_and_filter_candidates(
    candidates: list[_CandidateBox],
    lines: list[list[_WordBox]],
    keywords: tuple[str, ...],
) -> list[_CandidateBox]:
    if not keywords or len(candidates) <= 1:
        return candidates

    # Pre-index header/label keyword occurrences across lines (1 to 3 words).
    keyword_spans: list[tuple[int, float, float, float]] = []
    for li, line in enumerate(lines):
        n = len(line)
        for i in range(n):
            for span_len in (1, 2, 3):
                if i + span_len > n:
                    continue
                span = line[i : i + span_len]
                phrase = " ".join(" ".join(w.tokens) for w in span if w.tokens).strip()
                if phrase in keywords:
                    keyword_spans.append(
                        (
                            li,
                            min(w.x0 for w in span),
                            max(w.x1 for w in span),
                            max(w.y1 for w in span),
                        )
                    )

    if not keyword_spans:
        return candidates

    scored: list[tuple[int, float, _CandidateBox]] = []
    for cand in candidates:
        cand_cx = (cand.x0 + cand.x1) / 2.0
        best_score = 0
        best_dist = float("inf")
        for kw_line, kw_x0, kw_x1, kw_y1 in keyword_spans:
            if kw_line == cand.line_index and kw_x1 <= cand.x0 + 4.0:
                score = 3
                dist = max(0.0, cand.x0 - kw_x1)
            elif kw_line < cand.line_index and kw_y1 < cand.y0 and (
                kw_x0 - _HEADER_X_TOLERANCE_PT <= cand.x1
                and cand.x0 <= kw_x1 + _HEADER_X_TOLERANCE_PT
            ):
                score = 2
                kw_cx = (kw_x0 + kw_x1) / 2.0
                dist = abs(kw_cx - cand_cx)
            else:
                continue
            if score > best_score or (score == best_score and dist < best_dist):
                best_score = score
                best_dist = dist
        scored.append((best_score, best_dist, cand))

    if any(score >= 2 for score, _, _ in scored):
        scored = [item for item in scored if item[0] > 0]

    scored.sort(key=lambda item: (-item[0], item[1], item[2].y0, item[2].x0))
    return [item[2] for item in scored]


def locate_highlight_boxes(
    words: Sequence[Sequence[Any]],
    *,
    page_width: float,
    page_height: float,
    query: str | None,
    field_name: str | None = None,
    rect_x0: float = 0.0,
    rect_y0: float = 0.0,
    max_boxes: int = 5,
) -> list[HighlightBox]:
    """Locate bounding boxes matching ``query`` on a single PDF page."""
    raw_query = (query or "").strip()
    if not raw_query or page_width <= 0 or page_height <= 0 or max_boxes <= 0:
        return []

    lines = _parse_words(words)
    if not lines:
        return []

    query_tokens = _alnum_tokens(raw_query)
    pct_digits = _digits_only(raw_query)

    candidates: list[_CandidateBox] = []
    # Tier 1: 8-digit PCT / HS code
    if (
        len(pct_digits) == 8
        and not any(ch.isalpha() for ch in raw_query)
        and (
            _leaf_field_key(field_name) in ("", "pct_code")
            or "." in raw_query
            or len(raw_query) == 8
        )
    ):
        candidates = _match_pct_on_lines(lines, pct_digits)

    # Tier 2: exact/normalized phrase match
    if not candidates and query_tokens:
        candidates = _match_phrase_on_lines(
            lines, query_tokens, match_type="exact_phrase"
        )

    # Tier 3: canonical numeric Decimal equality (e.g. "75" == "75.00")
    if not candidates:
        dec_query = _parse_decimal_core(raw_query)
        if dec_query is not None:
            candidates = _match_decimal_on_lines(lines, dec_query)

    # Tier 4: ISO date variants
    if not candidates:
        for variant in _date_variants(raw_query):
            variant_tokens = _alnum_tokens(variant)
            matched = _match_phrase_on_lines(
                lines, variant_tokens, match_type="date"
            )
            if matched:
                candidates.extend(matched)
        if candidates:
            seen_coords: set[tuple[float, float, float, float]] = set()
            deduped: list[_CandidateBox] = []
            for cand in candidates:
                key = (cand.x0, cand.y0, cand.x1, cand.y1)
                if key not in seen_coords:
                    seen_coords.add(key)
                    deduped.append(cand)
            candidates = deduped

    # Tier 5: distinctive token fallback (len >= 4)
    if not candidates:
        long_tokens = {tok for tok in query_tokens if len(tok) >= 4}
        candidates = _match_token_fallback(lines, long_tokens)

    if not candidates:
        return []

    leaf_key = _leaf_field_key(field_name)
    keywords = _FIELD_CONTEXT_KEYWORDS.get(leaf_key, ())
    ranked = _rank_and_filter_candidates(candidates, lines, keywords)[:max_boxes]

    min_x, max_x = rect_x0, rect_x0 + page_width
    min_y, max_y = rect_y0, rect_y0 + page_height

    boxes: list[HighlightBox] = []
    for cand in ranked:
        px0 = max(min_x, cand.x0 - _BBOX_PAD_PT)
        py0 = max(min_y, cand.y0 - _BBOX_PAD_PT)
        px1 = min(max_x, cand.x1 + _BBOX_PAD_PT)
        py1 = min(max_y, cand.y1 + _BBOX_PAD_PT)
        boxes.append(
            HighlightBox(
                x0=round(px0, 2),
                y0=round(py0, 2),
                x1=round(px1, 2),
                y1=round(py1, 2),
                left_pct=round(((px0 - rect_x0) / page_width) * 100.0, 3),
                top_pct=round(((py0 - rect_y0) / page_height) * 100.0, 3),
                width_pct=round(((px1 - px0) / page_width) * 100.0, 3),
                height_pct=round(((py1 - py0) / page_height) * 100.0, 3),
                text=cand.text,
                match_type=cand.match_type,
            )
        )
    return boxes


def _stored_words_for_page(
    stored_pages: list[dict[str, Any]] | None, page_number: int
) -> list[Any] | None:
    if not stored_pages:
        return None
    for page_entry in stored_pages:
        if not isinstance(page_entry, dict):
            continue
        if page_entry.get("page_number") == page_number:
            words = page_entry.get("words")
            if isinstance(words, list) and words:
                return words
    return None


def build_pdf_page_preview(
    pdf_path: Path,
    *,
    document_id: UUID,
    original_filename: str,
    page_number: int,
    stored_pages: list[dict[str, Any]] | None = None,
    highlight: str | None = None,
    field_name: str | None = None,
) -> DocumentPagePreviewResponse:
    """Compute page geometry, PNG image URL, and bounding-box highlights for one PDF page."""
    try:
        document = pymupdf.open(pdf_path)
    except Exception as exc:
        raise PdfExtractionError("The stored PDF could not be opened for preview.") from exc

    try:
        page_count = int(document.page_count)
        if page_number < 1 or page_number > page_count:
            raise DocumentPageNotFoundError(
                f"Page {page_number} is outside the PDF page range (1-{page_count})."
            )
        page = document.load_page(page_number - 1)
        rect = page.rect
        page_width = float(rect.width)
        page_height = float(rect.height)
        page_words: Sequence[Sequence[Any]] | None = _stored_words_for_page(
            stored_pages, page_number
        )
        if not page_words:
            try:
                page_words = page.get_text("words")
            except Exception:
                page_words = ()
    finally:
        document.close()

    words_seq = page_words or ()
    highlights = locate_highlight_boxes(
        words_seq,
        page_width=page_width,
        page_height=page_height,
        query=highlight,
        field_name=field_name,
        rect_x0=float(rect.x0),
        rect_y0=float(rect.y0),
    )
    cleaned_query = highlight.strip() if isinstance(highlight, str) and highlight.strip() else None
    return DocumentPagePreviewResponse(
        document_id=document_id,
        original_filename=original_filename,
        page_number=page_number,
        page_count=page_count,
        page_width=round(page_width, 2),
        page_height=round(page_height, 2),
        image_url=f"/documents/uploads/{document_id}/pages/{page_number}/image",
        word_count=len(words_seq),
        highlight_query=cleaned_query,
        highlights=highlights,
    )


def render_pdf_page_png(
    pdf_path: Path,
    *,
    page_number: int,
    dpi: int = 150,
) -> bytes:
    """Render one PDF page to PNG bytes."""
    try:
        document = pymupdf.open(pdf_path)
    except Exception as exc:
        raise PdfExtractionError("The stored PDF could not be opened for rendering.") from exc

    try:
        page_count = int(document.page_count)
        if page_number < 1 or page_number > page_count:
            raise DocumentPageNotFoundError(
                f"Page {page_number} is outside the PDF page range (1-{page_count})."
            )
        page = document.load_page(page_number - 1)
        scale = dpi / 72.0
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale),
            colorspace=pymupdf.csRGB,
            alpha=False,
        )
        return bytes(pixmap.tobytes("png"))
    except DocumentPageNotFoundError:
        raise
    except Exception as exc:
        raise PdfExtractionError(
            f"PDF page {page_number} could not be rendered."
        ) from exc
    finally:
        document.close()
