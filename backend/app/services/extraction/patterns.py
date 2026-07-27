"""Label-anchored regex patterns for Pakistani export/customs documents.

This is the free half of the hybrid extractor: every field resolved here is a
field never sent to Groq. Patterns are deliberately conservative.

The governing rule is **a pattern may never guess**. A wrong value silently
corrupts a compliance verdict, whereas ``None`` correctly escalates the field
to the LLM. So every pattern here either matches something unambiguously
printed on the page, or it declines. Concretely that means:

* label-anchored patterns are preferred, and are tiered above bare ones;
* a bare pattern that hits more than once in a document is *not* resolved here
  - the candidates are collected and the choice is handed to the LLM;
* OCR tolerance widens what a pattern can *match*; it never rewrites a captured
  value. Where a digits-only field captures an OCR-confusable letter, the value
  is kept verbatim and flagged ambiguous so it escalates rather than being
  silently "corrected". Repairing a legal or financial value with regex is
  exactly the failure mode this module exists to avoid.

Adding a field: add its patterns to ``FIELD_PATTERNS`` and, if it needs
post-processing, a normaliser in ``FIELD_NORMALISERS``. See EXTRACTION.md.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Callable, Final, Literal

# --------------------------------------------------------------------------- #
# Text normalisation
# --------------------------------------------------------------------------- #
# Zero-width and bidi control characters that survive PDF text extraction and
# break otherwise-correct patterns without being visible in a debugger.
_INVISIBLE = dict.fromkeys(
    [
        0x00AD,  # soft hyphen
        0x200B,  # zero width space
        0x200C,  # zero width non-joiner
        0x200D,  # zero width joiner
        0x200E,  # left-to-right mark
        0x200F,  # right-to-left mark
        0x2060,  # word joiner
        0xFEFF,  # zero width no-break space
    ]
)

_WHITESPACE_RUN = re.compile(r"[^\S\n]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def normalise_text(text: str) -> str:
    """Collapse OCR whitespace noise while preserving line structure.

    Line breaks are kept because label/value adjacency on a line is a real
    signal, but runs of spaces and tabs are collapsed so a pattern does not
    have to anticipate how many spaces Tesseract emitted. Unicode is folded to
    NFKC so full-width digits and ligatures compare equal to their ASCII forms.
    """
    folded = unicodedata.normalize("NFKC", text).translate(_INVISIBLE)
    collapsed = _WHITESPACE_RUN.sub(" ", folded)
    return _BLANK_LINES.sub("\n\n", collapsed)


# --------------------------------------------------------------------------- #
# OCR-confusable characters
# --------------------------------------------------------------------------- #
# Tesseract routinely confuses these glyph pairs. Digits-only fields accept the
# letter forms so the pattern still *matches*; the capture is then flagged
# ambiguous rather than rewritten.
OCR_DIGIT_CONFUSIONS: Final[dict[str, str]] = {
    "O": "0",
    "o": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "l": "1",
    "|": "1",
    "Z": "2",
    "S": "5",
    "B": "8",
    "G": "6",
}

# A digit, or a letter Tesseract commonly emits instead of one.
_D = "[0-9" + "".join(sorted(set(OCR_DIGIT_CONFUSIONS))).replace("|", r"\|") + "]"

#: Separator between a label and its value: colon, dash, dot, or nothing, in
#: any quantity, because OCR drops and doubles punctuation freely.
SEP: Final[str] = r"\s*[:.\-–—]*\s*"


def contains_ocr_confusable_digit(value: str) -> bool:
    """True when a digits-only capture contains a letter that may be a digit."""
    return any(character in OCR_DIGIT_CONFUSIONS for character in value)


# --------------------------------------------------------------------------- #
# Pattern registry
# --------------------------------------------------------------------------- #
PatternTier = Literal["labeled", "bare"]


@dataclass(frozen=True)
class FieldPattern:
    """One way of finding one field."""

    regex: re.Pattern[str]
    tier: PatternTier
    #: Human-readable note used by the coverage report and by EXTRACTION.md.
    note: str = ""


def _labeled(pattern: str, note: str = "") -> FieldPattern:
    return FieldPattern(
        regex=re.compile(pattern, re.IGNORECASE | re.MULTILINE),
        tier="labeled",
        note=note,
    )


#: Generic trailing nouns that extend ANY label ("Exporter Name", "Buyer
#: Name", "Company Details") without being tied to one field. Deliberately
#: small: each word here is swallowed into every field's label, so it must be
#: a word that is never itself the start of a legitimate value.
_LABEL_CONTINUATION_WORDS = r"name|details|information|particulars"


def _label_value(labels: str, value: str, note: str) -> tuple[FieldPattern, ...]:
    """Build line-anchored label/value patterns for one field.

    Anchoring to line boundaries is what stops a label prefix from being
    matched inside a *compound* label and the remainder of that label being
    captured as the value. Against real fixtures the unanchored form produced
    ``consignee_name = "/ Consignee"`` (from "Buyer / Consignee") and
    ``country_of_destination = "Country"`` (from "Destination Country") - both
    confidently wrong, which is far worse than returning nothing.

    ``labels`` may be a plain alternation; compound forms joined by ``/``, ``&``
    or a space are accepted automatically so "Buyer / Consignee" is consumed as
    one label rather than half-matched.
    """
    # A compound label is consumed whole, joined by "/", "&" or whitespace, but
    # only when the adjoining token is itself a known label word. That is what
    # makes "Buyer / Consignee" and "Destination Country" single labels instead
    # of a label plus a captured label fragment.
    #
    # The atomic group is load-bearing, not decoration. Without it the engine
    # consumes "Buyer / Consignee", fails to find the whitespace the same-line
    # form needs before the newline, then *backtracks* to just "Buyer" and
    # happily captures "/ Consignee" as the consignee name - reintroducing the
    # exact false value this design exists to prevent. Atomic matching makes
    # the label all-or-nothing, so a partial label can never leave its own
    # remainder behind as a value.
    #
    # Found live: "Exporter Name" / "Buyer Name" (a role word plus the generic
    # noun "Name") is a common real invoice heading, but "Name" is not itself
    # one of a field's role words, so the compound-join above never absorbed
    # it. The label matched only "Exporter", and the leftover " Name" on the
    # same line was then confidently captured by the OCR single-line pattern
    # below as the field's *value* - "Name" is not a label fragment there, it
    # is exactly what a value looks like. _LABEL_CONTINUATION_WORDS closes
    # that gap the same way the "/"-joined case is already closed: a small,
    # generic, field-independent set of trailing nouns is swallowed into the
    # label so it can never be mistaken for the value that follows on the
    # next line.
    label = (
        rf"(?>(?:{labels})"
        rf"(?:[ \t]*[/&][ \t]*(?:{labels})"
        rf"|[ \t]+(?:{labels}|{_LABEL_CONTINUATION_WORDS}))*)"
    )
    return (
        # Two-column PDF text layers extract as "Label\nValue".
        _labeled(
            rf"^[ \t]*{label}[ \t]*:?[ \t]*\r?\n[ \t]*({value})[ \t]*$",
            f"{note} (value on next line)",
        ),
        # Inline layouts with an explicit separator.
        _labeled(
            rf"^[ \t]*{label}[ \t]*[:\-–—][ \t]*({value})[ \t]*$",
            f"{note} (value after separator)",
        ),
        # OCR flattens a two-column row onto one line with only whitespace
        # between label and value, so the separator-less form is required to
        # read scanned documents at all. It is safe only because the label is
        # anchored at line start and consumed in full: the earlier unanchored
        # version captured "/ Consignee" as a consignee name.
        _labeled(
            rf"^[ \t]*{label}[ \t]+({value})[ \t]*$",
            f"{note} (value after whitespace, OCR single-line form)",
        ),
    )


def _bare(pattern: str, note: str = "") -> FieldPattern:
    return FieldPattern(
        regex=re.compile(pattern, re.IGNORECASE | re.MULTILINE),
        tier="bare",
        note=note,
    )


# Value shapes reused across fields.
_ID = r"[A-Z0-9][A-Z0-9\-\/\._]{2,30}"
_MONEY_VALUE = r"\d[\d,\s]*(?:\.\d{1,2})?"
_ORG_LINE = r"[^\n]{3,120}"

CURRENCY_CODES: Final[tuple[str, ...]] = (
    "USD", "PKR", "EUR", "GBP", "AED", "CNY", "JPY", "CHF", "SAR", "CAD", "AUD",
)
INCOTERMS_2020: Final[tuple[str, ...]] = (
    "EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP",
)
WEIGHT_UNITS: Final[tuple[str, ...]] = (
    "KGS", "KG", "MT", "M.T.", "LBS", "LB", "TONS", "TON", "TONNES",
)

_CURRENCY_ALT = "|".join(CURRENCY_CODES)
_INCOTERM_ALT = "|".join(INCOTERMS_2020)
_WEIGHT_ALT = r"KGS?|K\.G\.?S?|M\.?T\.?|LBS?|TONNES?|TONS?"

# Value shapes accepted after a label.
_V_ID = r"[A-Z0-9][A-Z0-9\-\/\._]{2,30}"
_V_ORG = r"[^\n]{3,120}"
_V_COUNTRY = r"[A-Za-z][A-Za-z .\-]{2,40}"
_V_PLACE = r"[A-Za-z][A-Za-z .,\-]{2,50}"
_V_DATE = r"[0-9A-Za-z][^\n]{5,29}"
#: A currency code may print before the amount ("USD 550.00") or after it
#: ("550.00 USD") - both are common on real invoices. Only the leading form
#: was accepted; a trailing currency (with no weight unit) failed the whole
#: line and left fields like invoice_total unresolved even though the value
#: was right there on the page.
_V_MONEY = (
    rf"(?:{_CURRENCY_ALT}|\$|Rs\.?)?[ \t]*{_MONEY_VALUE}"
    rf"(?:[ \t]*(?:{_WEIGHT_ALT}|{_CURRENCY_ALT}))?"
)
# Weight fields only: a bare 1-2 digit integer with neither a decimal point
# nor a unit is too weak to trust as a weight. Found live: a "Gross Weight"
# *table column heading* sits on its own line directly above the item row,
# so the item row's leading line number ("1") landed on the very next line
# after it and satisfied the looser _V_MONEY pattern - genuinely ambiguous
# against the real "Declared Gross Weight Total\n80.00 KG" match elsewhere on
# the page, so the field correctly refused to pick one and stayed
# unresolved. Requiring a decimal or an explicit unit is how a real
# document's weight is printed; a coincidental row number is neither.
_V_WEIGHT = rf"(?:{_MONEY_VALUE}[ \t]*(?:{_WEIGHT_ALT})|\d[\d,\s]*\.\d{{1,2}})"
_V_NTN = rf"{_D}{{7}}[ \t]*-[ \t]*{_D}|{_D}{{13}}|{_D}{{7,8}}"
_V_PCT = rf"{_D}{{4}}[.\s\-]?{_D}{{2}}[.\s\-]?{_D}{{2}}"
_V_COUNT = rf"{_D}[\d,]{{0,7}}(?:[ \t]*[A-Za-z]{{3,10}})?"

FIELD_PATTERNS: Final[dict[str, tuple[FieldPattern, ...]]] = {
    # ---- identifiers --------------------------------------------------- #
    "invoice_number": _label_value(
        r"(?:commercial[ \t]+)?invoice[ \t]*(?:no\.?|num(?:ber)?|#)?"
        r"|inv[ \t]*(?:no\.?|#)|bill[ \t]*no\.?",
        _V_ID,
        "Invoice No",
    ),
    "bl_awb_number": _label_value(
        r"b\.?[ \t]*/?[ \t]*l[ \t]*(?:no\.?|number)?"
        r"|bill[ \t]+of[ \t]+lading[ \t]*(?:no\.?|number)?"
        r"|awb[ \t]*(?:no\.?|number)?|air[ \t]?way[ \t]?bill[ \t]*(?:no\.?)?",
        _V_ID,
        "B/L or AWB No",
    ),
    "gd_number": _label_value(
        r"g\.?[ \t]?d\.?[ \t]*(?:no\.?|number)?"
        r"|goods[ \t]+declaration[ \t]*(?:no\.?|number)?"
        r"|machine[ \t]+number",
        _V_ID,
        "GD No",
    ),
    "form_e_number": _label_value(
        r"form[ \t\-]?e[ \t]*(?:no\.?|number)?|e[ \t\-]?form[ \t]*(?:no\.?|number)?",
        _V_ID,
        "Form-E No",
    ),
    "exporter_ntn": _label_value(
        r"n\.?[ \t]?t\.?[ \t]?n\.?[ \t]*(?:no\.?|number)?"
        r"|national[ \t]+tax[ \t]+number",
        _V_NTN,
        "NTN",
    ),
    # ---- parties -------------------------------------------------------- #
    "exporter_name": _label_value(
        r"exporter|shipper|seller|consignor|beneficiary",
        _V_ORG,
        "Exporter/Shipper",
    ),
    "exporter_address": _label_value(
        r"exporter[ \t]+address|shipper[ \t]+address|address[ \t]+of[ \t]+exporter",
        _V_ORG,
        "Exporter Address",
    ),
    "consignee_name": _label_value(
        r"consignee|buyer|importer|bill[ \t]+to|sold[ \t]+to|applicant",
        _V_ORG,
        "Consignee/Buyer",
    ),
    "consignee_address": _label_value(
        r"consignee[ \t]+address|buyer[ \t]+address|address[ \t]+of[ \t]+consignee",
        _V_ORG,
        "Consignee Address",
    ),
    # ---- geography ------------------------------------------------------ #
    "country_of_origin": _label_value(
        r"country[ \t]+of[ \t]+origin|origin[ \t]+country",
        _V_COUNTRY,
        "Country of Origin",
    ),
    "country_of_destination": _label_value(
        r"country[ \t]+of[ \t]+destination|destination[ \t]+country"
        r"|final[ \t]+destination|destination",
        _V_COUNTRY,
        "Country of Destination",
    ),
    "port_of_loading": _label_value(
        r"port[ \t]+of[ \t]+(?:loading|lading|shipment)|loading[ \t]+port",
        _V_PLACE,
        "Port of Loading",
    ),
    "port_of_discharge": _label_value(
        r"port[ \t]+of[ \t]+(?:discharge|delivery)|discharge[ \t]+port"
        r"|port[ \t]+of[ \t]+destination",
        _V_PLACE,
        "Port of Discharge",
    ),
    # ---- commercial terms ----------------------------------------------- #
    "incoterm": (
        *_label_value(
            r"incoterms?(?:[ \t]*20(?:10|20))?|terms[ \t]+of[ \t]+delivery"
            r"|delivery[ \t]+terms|price[ \t]+terms|shipment[ \t]+terms",
            rf"(?:{_INCOTERM_ALT})\b[^\n]{{0,40}}",
            "Incoterm",
        ),
        _bare(rf"\b({_INCOTERM_ALT})\b(?=[ \t]|,|\.|$)", "Bare Incoterm token"),
    ),
    "currency": (
        *_label_value(r"currency|invoice[ \t]+currency", rf"{_CURRENCY_ALT}", "Currency"),
        _bare(rf"\b({_CURRENCY_ALT})\b", "Bare ISO currency code"),
    ),
    "total_invoice_value": _label_value(
        r"total[ \t]+invoice[ \t]+(?:value|amount)|invoice[ \t]+total"
        r"|grand[ \t]+total|total[ \t]+(?:value|amount)|total[ \t]+fob",
        _V_MONEY,
        "Total Invoice Value",
    ),
    # ---- quantities ------------------------------------------------------ #
    "gross_weight": _label_value(
        r"(?:declared[ \t]+)?gross[ \t]+w(?:eigh)?t\.?(?:[ \t]+total)?"
        r"|gross[ \t]+weight[ \t]*\((?:kgs?|m\.?t\.?)\)",
        _V_WEIGHT,
        "Gross Weight",
    ),
    "net_weight": _label_value(
        r"(?:declared[ \t]+)?net[ \t]+w(?:eigh)?t\.?(?:[ \t]+total)?"
        r"|net[ \t]+weight[ \t]*\((?:kgs?|m\.?t\.?)\)",
        _V_WEIGHT,
        "Net Weight",
    ),
    "number_of_packages": _label_value(
        r"(?:total[ \t]+)?(?:no\.?|number)[ \t]+of[ \t]+"
        r"(?:packages|packs|cartons|bales|pkgs|pieces)"
        r"|total[ \t]+(?:packages|cartons|bales|pkgs)"
        # Some invoices label this column simply "Packages". Safe here because
        # the pattern still requires line anchoring and a numeric value.
        r"|packages|packing[ \t]+units",
        _V_COUNT,
        "No. of Packages",
    ),
    # ---- dates ----------------------------------------------------------- #
    "invoice_date": _label_value(
        r"invoice[ \t]+date|inv\.?[ \t]+date|date[ \t]+of[ \t]+invoice"
        r"|bill[ \t]+date|dated?",
        _V_DATE,
        "Invoice Date",
    ),
    # ---- line-item level (also used on a single reconstructed row) ------- #
    "pct_code": (
        *_label_value(
            r"pct[ \t]*(?:code|no\.?)?|h\.?[ \t]?s\.?[ \t]*(?:code|no\.?)?"
            r"|hs[ \t]+code|tariff[ \t]*(?:code|heading)?",
            _V_PCT,
            "PCT/HS Code",
        ),
        _bare(r"\b(\d{4}\.\d{2}\.\d{2})\b", "Dotted 8-digit 5208.52.00"),
        _bare(r"\b(\d{4}\.\d{4})\b", "Dotted 4+4 5208.5200"),
    ),
    "unit": (
        _bare(
            r"\b(PCS|PIECES|KGS?|MTR|METERS?|METRES?|YDS?|YARDS?|DOZ|DOZENS?|"
            r"SETS?|CTNS?|CARTONS?|BALES?|ROLLS?|SQM|LBS?)\b",
            "Unit of measure token",
        ),
    ),
}


#: Fields whose printed form is definitionally digits-only. A capture here that
#: contains an OCR-confusable letter is reported ambiguous, never rewritten.
DIGITS_ONLY_FIELDS: Final[frozenset[str]] = frozenset(
    {"exporter_ntn", "number_of_packages"}
)

#: Fields that carry a legal or financial value. These are never resolved from
#: a bare (unlabelled) pattern, however unique the match looks - the label is
#: what proves the number means what we think it means.
LABEL_REQUIRED_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "total_invoice_value",
        "gross_weight",
        "net_weight",
        "number_of_packages",
        "exporter_ntn",
        "invoice_number",
        "bl_awb_number",
        "gd_number",
        "form_e_number",
    }
)

LINE_ITEM_FIELDS: Final[tuple[str, ...]] = (
    "description",
    "pct_code",
    "quantity",
    "unit",
    "unit_price",
    "line_total",
)

DOCUMENT_FIELDS: Final[tuple[str, ...]] = (
    "invoice_number",
    "invoice_date",
    "exporter_name",
    "exporter_address",
    "exporter_ntn",
    "consignee_name",
    "consignee_address",
    "country_of_origin",
    "country_of_destination",
    "port_of_loading",
    "port_of_discharge",
    "incoterm",
    "currency",
    "total_invoice_value",
    "gross_weight",
    "net_weight",
    "number_of_packages",
    "bl_awb_number",
    "gd_number",
    "form_e_number",
)


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
_MONTHS: Final[dict[str, int]] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_ISO_DATE = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[-/.\s](\d{1,2})[-/.\s](\d{2,4})\b")
_TEXT_MONTH_DATE = re.compile(
    r"\b(\d{1,2})[-\s.]*([A-Za-z]{3,9})[-\s.,]*(\d{2,4})\b"
)
_MONTH_TEXT_FIRST = re.compile(
    r"\b([A-Za-z]{3,9})[-\s.]*(\d{1,2})[-\s.,]*(\d{2,4})\b"
)


@dataclass(frozen=True)
class ParsedDate:
    """A date parse that knows whether it was ambiguous."""

    value: date | None
    #: True when DD/MM and MM/DD both yield a valid, different date.
    ambiguous: bool
    reason: str = ""


def _four_digit_year(raw: str) -> int | None:
    year = int(raw)
    if len(raw) == 4:
        return year
    # A 2-digit year is only unambiguous within a chosen century window.
    # Customs documents are contemporary, so 00-79 -> 2000s, 80-99 -> 1900s.
    return 2000 + year if year < 80 else 1900 + year


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date(raw: str) -> ParsedDate:
    """Parse a printed date to ISO, refusing to guess DD/MM vs MM/DD.

    Pakistani customs documents are DD/MM/YYYY, but a bare ``05/03/2026`` is
    genuinely both. Rather than assume the local convention and risk shifting a
    shipment date by months - which can flip a 180-day SRO deadline check - an
    ambiguous numeric date is returned with ``ambiguous=True`` so the caller
    escalates it to the LLM, which can read the surrounding document context.
    """
    text = raw.strip()

    iso = _ISO_DATE.search(text)
    if iso:
        iso_year, iso_month, iso_day = (int(part) for part in iso.groups())
        parsed = _safe_date(iso_year, iso_month, iso_day)
        if parsed is not None:
            return ParsedDate(parsed, ambiguous=False)

    for matcher, month_first in ((_TEXT_MONTH_DATE, False), (_MONTH_TEXT_FIRST, True)):
        match = matcher.search(text)
        if not match:
            continue
        first, second, third = match.groups()
        name = (first if month_first else second).lower()[:4]
        month = _MONTHS.get(name) or _MONTHS.get(name[:3])
        if month is None:
            continue
        day_raw = second if month_first else first
        text_year = _four_digit_year(third)
        if text_year is None:
            continue
        parsed = _safe_date(text_year, month, int(day_raw))
        if parsed is not None:
            # A spelled-out month cannot be ambiguous.
            return ParsedDate(parsed, ambiguous=False)

    numeric = _NUMERIC_DATE.search(text)
    if numeric:
        first, second, third_raw = numeric.groups()
        numeric_year = _four_digit_year(third_raw)
        if numeric_year is not None:
            day_month = _safe_date(numeric_year, int(second), int(first))  # DD/MM
            month_day = _safe_date(numeric_year, int(first), int(second))  # MM/DD
            if day_month is not None and month_day is not None:
                if day_month == month_day:
                    return ParsedDate(day_month, ambiguous=False)
                return ParsedDate(
                    None,
                    ambiguous=True,
                    reason=(
                        f"'{numeric.group(0)}' is valid as both "
                        f"{day_month.isoformat()} (DD/MM) and "
                        f"{month_day.isoformat()} (MM/DD)"
                    ),
                )
            # Only one interpretation is a real calendar date, so the format is
            # determined by the value itself rather than assumed.
            if day_month is not None:
                return ParsedDate(day_month, ambiguous=False)
            if month_day is not None:
                return ParsedDate(month_day, ambiguous=False)

    return ParsedDate(None, ambiguous=False, reason="no recognisable date")


# --------------------------------------------------------------------------- #
# Value normalisers
# --------------------------------------------------------------------------- #
_MONEY_CORE = re.compile(
    rf"^(?:{'|'.join(CURRENCY_CODES)}|\$|Rs\.?)?\s*"
    r"([\d,\s]*\d(?:\.\d{1,2})?)"
    rf"\s*(?:{_WEIGHT_ALT}|{'|'.join(CURRENCY_CODES)})?\.?$",
    re.IGNORECASE,
)


def normalise_money(raw: str) -> str | None:
    """Extract the numeric core from a printed amount or weight.

    A captured value legitimately carries its currency or unit ("USD 550.00",
    "75.00 KG") because that is how the document prints it. Stripping only
    separators left "USD550.00", which failed validation and silently lost the
    field. The affixes are removed; not one digit is altered.
    """
    match = _MONEY_CORE.match(raw.strip())
    if match is None:
        return None
    cleaned = match.group(1).replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        return None
    return cleaned


_COUNT_CORE = re.compile(r"^([\d,\s]*\d)\s*[A-Za-z.]*$")


def normalise_count(raw: str) -> str | None:
    """Extract a whole-number count printed with its noun ("5 CARTONS").

    Package counts are printed with the packaging word attached, which the
    money normaliser rejects because "CARTONS" is not a currency or a weight
    unit. That silently lost the field on every document that prints it.
    """
    match = _COUNT_CORE.match(raw.strip())
    if match is None:
        return None
    cleaned = match.group(1).replace(",", "").replace(" ", "")
    return cleaned if cleaned.isdigit() else None


def normalise_pct_code(raw: str) -> str | None:
    """Reduce a PCT/HS code to its digits, preserving all eight."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) not in (8, 6, 4):
        return None
    return digits


def normalise_identifier(raw: str) -> str | None:
    """Accept a document reference only if it actually looks like one.

    Patterns are compiled with IGNORECASE, so a character class written as
    ``[A-Z0-9]`` also matches lowercase - which let ordinary words be captured
    as identifiers. Against real fixtures the heading "COMMERCIAL INVOICE"
    matched the invoice-number label and the following line, "Exporter", was
    captured as the invoice number. Requiring at least one digit is what an
    invoice/GD/Form-E/B-L reference always has and a prose word does not.
    """
    value = raw.strip().rstrip(".,;:").strip()
    if not value or not any(character.isdigit() for character in value):
        return None
    return value


def normalise_org(raw: str) -> str | None:
    """Trim a captured organisation line without inventing structure."""
    value = " ".join(raw.split()).strip(" .,;:-")
    # A capture that is only punctuation or a stray label fragment is not a name.
    if len(value) < 3 or not re.search(r"[A-Za-z]{2}", value):
        return None
    return value


def normalise_country(raw: str) -> str | None:
    value = " ".join(raw.split()).strip(" .,;:-")
    if len(value) < 3 or not re.fullmatch(r"[A-Za-z][A-Za-z .\-]*", value):
        return None
    return value


def normalise_upper_token(raw: str) -> str | None:
    return raw.strip().upper() or None


FIELD_NORMALISERS: Final[dict[str, Callable[[str], str | None]]] = {
    "total_invoice_value": normalise_money,
    "gross_weight": normalise_money,
    "net_weight": normalise_money,
    "unit_price": normalise_money,
    "line_total": normalise_money,
    "quantity": normalise_money,
    "number_of_packages": normalise_count,
    "pct_code": normalise_pct_code,
    "invoice_number": normalise_identifier,
    "bl_awb_number": normalise_identifier,
    "gd_number": normalise_identifier,
    "form_e_number": normalise_identifier,
    "exporter_ntn": normalise_identifier,
    "exporter_name": normalise_org,
    "consignee_name": normalise_org,
    "exporter_address": normalise_org,
    "consignee_address": normalise_org,
    "port_of_loading": normalise_org,
    "port_of_discharge": normalise_org,
    "country_of_origin": normalise_country,
    "country_of_destination": normalise_country,
    "incoterm": normalise_upper_token,
    "currency": normalise_upper_token,
    "unit": normalise_upper_token,
    "description": normalise_org,
}
