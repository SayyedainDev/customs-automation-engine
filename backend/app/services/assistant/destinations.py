"""Canonical destination names for questions written in free text.

Destination used to be pulled out with a regex for "any capitalised word after
to/for". On the query that prompted this fix it returned "Cotton" - the word
right after "for" - while the actual destination, typed lowercase as "usa", was
invisible. A wrong destination silently changes which destination rules the
deterministic engine evaluates, so this is not a cosmetic bug.

Matching is against a known-country vocabulary rather than capitalisation, so a
product word can never be read as a destination, and "usa", "U.S.A.", "United
States" and "America" all reach the same canonical value the configured rules
use.
"""

from __future__ import annotations

import re

#: Canonical name -> the spellings a user might type. Canonical values match
#: the destination_country values already present in the configured rules.
_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "USA": (
        "usa", "u s a", "us", "u s", "united states", "united states of america",
        "america", "the united states", "stateside",
    ),
    "China": ("china", "prc", "peoples republic of china", "mainland china"),
    "Afghanistan": ("afghanistan", "kabul"),
    "United Kingdom": ("uk", "u k", "united kingdom", "britain", "great britain", "england"),
    "Germany": ("germany", "deutschland"),
    "European Union": ("eu", "european union", "europe"),
    "Bangladesh": ("bangladesh",),
    "Turkey": ("turkey", "turkiye", "türkiye"),
    "United Arab Emirates": ("uae", "u a e", "united arab emirates", "dubai"),
    "Canada": ("canada",),
    "Australia": ("australia",),
    "Italy": ("italy",),
    "France": ("france",),
    "Spain": ("spain",),
    "Netherlands": ("netherlands", "holland"),
    "Japan": ("japan",),
    "South Korea": ("south korea", "korea"),
    "Saudi Arabia": ("saudi arabia", "ksa"),
    "Sri Lanka": ("sri lanka",),
    "India": ("india",),
}

#: Origin, not destination. "from pakistan" must never be read as where the
#: goods are going.
_ORIGIN_MARKERS = ("from",)

_PAKISTAN = ("pakistan", "pak")


def _normalized(text: str | None) -> str:
    """Lower-cased, punctuation flattened, so 'U.S.A.' becomes 'u s a '."""
    return " " + re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).strip() + " "


def canonical_destination(value: str | None) -> str | None:
    """Canonical country name for a user-supplied destination, if recognised."""
    if not value:
        return None
    haystack = _normalized(value)
    for canonical, aliases in _COUNTRY_ALIASES.items():
        for alias in aliases:
            if f" {alias} " in haystack:
                return canonical
    return None


def extract_destination(question: str) -> str | None:
    """The destination country a question names, ignoring its origin country.

    Returns a canonical name, or None when no known country is named. Returning
    None is correct behaviour: the caller then asks rather than evaluating
    destination rules against a word that happened to be capitalised.
    """
    haystack = _normalized(question)

    # Strip "from <country>" spans so the origin cannot win.
    for marker in _ORIGIN_MARKERS:
        haystack = re.sub(
            rf" {marker} (?:the )?([a-z]+(?: [a-z]+)?) ", " ", haystack
        )

    best: tuple[int, str] | None = None
    for canonical, aliases in _COUNTRY_ALIASES.items():
        for alias in aliases:
            index = haystack.find(f" {alias} ")
            if index == -1:
                continue
            # Longer aliases win, so "united states of america" beats "us".
            if best is None or len(alias) > len(best[0].__str__()):
                best = (len(alias), canonical)
    if best:
        return best[1]
    return None


def is_pakistan(value: str | None) -> bool:
    haystack = _normalized(value)
    return any(f" {name} " in haystack for name in _PAKISTAN)
