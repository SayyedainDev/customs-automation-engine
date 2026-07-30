"""Resolve an exporter's words to a supported PCT code.

Intent routing could previously reach the deterministic checklist only when the
user typed an eight-digit code. "What documents to prepare for cotton pants
export to USA" therefore fell through to document search, and even the exact
catalog name "Men's woven cotton trousers" did the same. Exporters do not speak
in tariff codes, so the checklist was effectively unreachable.

The matching here is deliberately narrow. Unrestricted fuzzy matching against
17 product names would happily map "cotton seed" onto a garment code and then
issue a compliance checklist for it, which is worse than refusing. So:

* aliases are an explicit, curated table - not derived from the catalog names;
* spelling repair only ever rewrites a token into a word already in that table,
  at edit distance 1, and only for tokens long enough for that to be meaningful;
* a match must carry a textile signal from the question itself;
* when several codes fit equally, the result is *ambiguous*, not a guess.

Nothing here decides compliance. It only chooses which supported code the
deterministic engine should be asked about, and says so when it cannot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.compliance.pct_catalog import supported_pct_codes, supported_pct_products

#: Words that place a question in textile/apparel territory. A product alias is
#: only honoured when one of these is present, so "paint" in a hardware question
#: cannot be repaired into "pants" and routed to a garment code.
_TEXTILE_SIGNALS = frozenset(
    """
    cotton textile textiles fabric cloth garment garments apparel clothing
    knitted woven denim yarn terry linen wear
    """.split()
)

#: Curated product vocabulary. Values are the supported codes a term can mean;
#: several codes means genuinely ambiguous, not "pick the first".
_ALIASES: dict[str, tuple[str, ...]] = {
    # Woven trousers - the case that exposed this gap.
    "pants": ("62034200", "62046290"),
    "trousers": ("62034200", "62046290"),
    "slacks": ("62034200", "62046290"),
    "breeches": ("62034200", "62046290"),
    # Knitted tops.
    "t-shirt": ("61091000",),
    "tshirt": ("61091000",),
    "tee": ("61091000",),
    "blouse": ("61061000",),
    "blouses": ("61061000",),
    "jersey": ("61102000",),
    "pullover": ("61102000",),
    "cardigan": ("61102000",),
    "sweater": ("61102000",),
    # Shirts split across knitted and woven chapters.
    "shirt": ("61051000", "62052090"),
    "shirts": ("61051000", "62052090"),
    # Made-ups.
    "towel": ("63026010",),
    "towels": ("63026010",),
    "blanket": ("63013000",),
    "blankets": ("63013000",),
    "bedsheet": ("63023110",),
    "bedsheets": ("63023110",),
    "bedlinen": ("63023110",),
    "jeans": ("62034200", "62046290"),
    # Materials and fabric.
    "yarn": ("52051100", "52052100"),
    "denim": ("52094200", "52114200"),
    "fabric": ("52085200", "52093100", "52094200", "52114200"),
}

#: Aliases that name a material or raw commodity rather than a finished
#: article. When a garment alias is present too, the garment is what the user
#: is asking about and the material is only describing it - "denim pants" are
#: trousers, not fabric. Matching took the first alias token in the sentence,
#: so "denim pants" resolved to denim fabric.
_MATERIAL_ALIASES = frozenset({"yarn", "denim", "fabric"})

#: Multi-word product names, checked before single-word aliases so the longer
#: and more specific reading wins. "Raw cotton" is a supported code in its own
#: right (52010090) but had no vocabulary at all, so the single product whose
#: rules CACE models most fully could not be named in words.
_PHRASE_ALIASES: tuple[tuple[re.Pattern[str], str, tuple[str, ...]], ...] = (
    (re.compile(r"\bt\s+shirts?\b"), "t-shirt", ("61091000",)),
    (re.compile(r"\braw\s+cotton\b"), "raw cotton", ("52010090",)),
    (re.compile(r"\bcotton\s+lint\b"), "cotton lint", ("52010090",)),
    (re.compile(r"\bcombed\s+(cotton\s+)?yarn\b"), "combed yarn", ("52052100",)),
    (re.compile(r"\bterry\s+towels?\b"), "terry towel", ("63026010",)),
    (re.compile(r"\bbed\s+sheets?\b"), "bed sheet", ("63023110",)),
)

#: Qualifiers that disambiguate an alias, applied only within its candidates.
_MENS = frozenset({"men", "mens", "man", "male", "boy", "boys", "gents", "gentlemen"})
_WOMENS = frozenset({"women", "womens", "woman", "female", "girl", "girls", "ladies", "lady"})
_KNITTED = frozenset({"knitted", "knit", "crocheted", "jersey"})
_WOVEN = frozenset({"woven", "weave"})

#: Spelling repair is limited to this vocabulary, so a repair can only ever
#: produce a word the resolver already understands.
_REPAIRABLE = frozenset(_ALIASES) | _TEXTILE_SIGNALS | _MENS | _WOMENS | _KNITTED | _WOVEN

#: Below this length an edit-distance-1 neighbour is usually a different word.
_MIN_REPAIR_LENGTH = 4

_WORD = re.compile(r"[a-z][a-z'-]*")


@dataclass(frozen=True)
class ProductResolution:
    """What the user's wording could mean in the supported catalog."""

    #: Exactly one supported code, when the wording is unambiguous.
    pct_code: str | None = None
    #: Several supported codes when the wording fits more than one.
    candidates: tuple[str, ...] = ()
    #: {typed word: word used instead}, surfaced to the user rather than hidden.
    corrections: dict[str, str] = field(default_factory=dict)
    #: The alias that matched, for explaining the interpretation.
    matched_term: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.pct_code is not None

    @property
    def is_ambiguous(self) -> bool:
        return self.pct_code is None and len(self.candidates) > 1

    def product_name(self) -> str | None:
        if not self.pct_code:
            return None
        return supported_pct_products().get(self.pct_code)


def product_vocabulary() -> frozenset[str]:
    """Every word this resolver recognises as naming or describing a product.

    Callers use it to tell "the question is just a product name" from "the
    question is about something else and happens to name a product".
    """
    phrase_words = {word for _p, term, _c in _PHRASE_ALIASES for word in term.split()}
    # Plurals count as naming the product, so "Tshirts" on its own reads as a
    # bare product mention rather than as a question with leftover words.
    plurals = {f"{word}s" for word in _REPAIRABLE} | {
        f"{word}es" for word in _REPAIRABLE
    }
    return frozenset(_REPAIRABLE | phrase_words | plurals)


def _edit_distance_within_one(a: str, b: str) -> bool:
    """True when a and b differ by at most one insert, delete or substitution."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    shorter, longer = (a, b) if la < lb else (b, a)
    i = j = 0
    skipped = False
    while i < len(shorter) and j < len(longer):
        if shorter[i] != longer[j]:
            if skipped:
                return False
            skipped = True
            j += 1
            continue
        i += 1
        j += 1
    return True


def _singular(token: str) -> str | None:
    """The known vocabulary word this token is the plural of, if it is one."""
    for stem in (token[:-3] + "y", token[:-2], token[:-1]):
        # "jerseys" -> "jersey" via the -es/-s forms; the -ies form covers
        # nothing in this table today but costs nothing and is not wrong.
        if token.endswith("ies") and stem.endswith("y") and stem in _REPAIRABLE:
            return stem
        if token.endswith("es") and stem in _REPAIRABLE and len(stem) > 2:
            return stem
        if token.endswith("s") and stem in _REPAIRABLE and len(stem) > 2:
            return stem
    return None


def _repair(token: str) -> str | None:
    """The single vocabulary word this token is an obvious misspelling of."""
    if len(token) < _MIN_REPAIR_LENGTH or token in _REPAIRABLE:
        return None
    matches = [
        word
        for word in _REPAIRABLE
        if len(word) >= _MIN_REPAIR_LENGTH and _edit_distance_within_one(token, word)
    ]
    # Exactly one candidate, or the repair is a guess.
    return matches[0] if len(matches) == 1 else None


def resolve_product(question: str) -> ProductResolution:
    """Map free-text product wording to a supported PCT code, or report why not."""
    raw_tokens = _WORD.findall((question or "").casefold())
    tokens: list[str] = []
    corrections: dict[str, str] = {}
    for token in raw_tokens:
        normalized = token.replace("'", "")
        if normalized in _REPAIRABLE:
            tokens.append(normalized)
            continue
        # A plural is not a misspelling, and it has to be recognised as such
        # *before* spelling repair. "t-shirts" was repaired to "t-shirt" and
        # so counted as a correction, while "tshirts" sat at edit distance 1
        # from both "tshirt" and "shirts" and was therefore rejected as an
        # ambiguous repair - a plain plural of a catalog product resolved to
        # nothing. Only strip the s when what remains is already known.
        singular = _singular(normalized)
        if singular is not None:
            tokens.append(singular)
            continue
        repaired = _repair(normalized)
        if repaired:
            corrections[token] = repaired
            tokens.append(repaired)
        else:
            tokens.append(normalized)

    present = set(tokens)
    # A word from the product table is itself the textile context. Requiring a
    # *separate* signal word meant "Tshirts", "shirts" and "T-shirts" resolved
    # to nothing at all, because the question never said "cotton" or "textile"
    # - so asking about the catalog's own products in the most natural way
    # possible failed. The signal is still required for a *repaired* token,
    # which is where the guard actually earns its keep: it stops "paint" in a
    # hardware question being rewritten into "pants" and routed to a garment.
    direct_alias = bool(present & set(_ALIASES))
    repaired_only = set(corrections.values()) & set(_ALIASES)
    if not direct_alias and not (present & _TEXTILE_SIGNALS):
        # No textile context: refuse to map anything, however close the spelling.
        return ProductResolution(corrections={})
    if repaired_only and not (present & _TEXTILE_SIGNALS) and not (
        (present & set(_ALIASES)) - repaired_only
    ):
        # The only product word here came from a spelling repair, with nothing
        # else placing the question in textiles. Too weak to act on.
        return ProductResolution(corrections={})

    # A multi-word name is more specific than any single word inside it.
    normalized_text = " ".join(tokens)
    phrase = next(
        (
            (term, codes)
            for pattern, term, codes in _PHRASE_ALIASES
            if pattern.search(normalized_text)
        ),
        None,
    )
    if phrase is not None:
        matched_term, phrase_codes = phrase
        supported_now = set(supported_pct_codes())
        usable = [code for code in phrase_codes if code in supported_now]
        if len(usable) == 1:
            return ProductResolution(
                pct_code=usable[0], corrections=corrections, matched_term=matched_term
            )
        if usable:
            return ProductResolution(
                candidates=tuple(sorted(usable)),
                corrections=corrections,
                matched_term=matched_term,
            )

    alias_tokens = [t for t in tokens if t in _ALIASES]
    # A garment named alongside its material is a question about the garment.
    article_tokens = [t for t in alias_tokens if t not in _MATERIAL_ALIASES]
    preferred = article_tokens or alias_tokens
    if not preferred:
        return ProductResolution(corrections=corrections)
    matched_term = preferred[0]

    candidates = list(_ALIASES[matched_term])
    supported = set(supported_pct_codes())
    candidates = [code for code in candidates if code in supported]
    if not candidates:
        return ProductResolution(corrections=corrections, matched_term=matched_term)

    # Narrow with qualifiers, but only within the alias's own candidates.
    def _narrow(keep: set[str]) -> None:
        nonlocal candidates
        filtered = [c for c in candidates if c in keep]
        if filtered:
            candidates = filtered

    if present & _MENS:
        _narrow({"62034200", "61051000", "62052090", "61091000"})
    elif present & _WOMENS:
        _narrow({"62046290", "61061000"})
    if present & _KNITTED:
        _narrow({"61051000", "61061000", "61091000", "61102000"})
    elif present & _WOVEN:
        _narrow({"62034200", "62046290", "62052090", "52085200", "52093100",
                 "52094200", "52114200"})

    if len(candidates) == 1:
        return ProductResolution(
            pct_code=candidates[0], corrections=corrections, matched_term=matched_term
        )
    return ProductResolution(
        candidates=tuple(sorted(candidates)),
        corrections=corrections,
        matched_term=matched_term,
    )


def textile_signals() -> frozenset[str]:
    """The textile-family words that place a question in this domain."""
    return _TEXTILE_SIGNALS
