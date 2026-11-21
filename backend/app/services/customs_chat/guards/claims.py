"""Layer 4 building blocks: claims a generated answer may never make.

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 10.4. Two independent checks:

* ``forbidden_claim_violations`` - a fixed list of claims no answer may make
  regardless of mode (compliance verdicts, authenticity claims, completeness
  claims). Ported from ``services/assistant/domain_guard.py``, which is
  well-researched prior art for this exact list.
* ``ungrounded_numbers`` - the mechanical enforcement of the one rule the
  whole Ring 2 design depends on being safe (docs section 3.3 rule 5): a
  GENERAL-mode answer may explain concepts freely but must never state a
  specific Pakistani number - a duty rate, a deadline, an SRO or section
  reference - because nothing retrieved backs it. A GROUNDED-mode answer may
  state numbers, but only ones that actually appear in the evidence passed
  to the model.
"""

from __future__ import annotations

import re

_FORBIDDEN_CLAIM_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"will\s+(certainly\s+|definitely\s+)?clear\s+customs", "customs-clearance claim"),
    (r"guarantee[sd]?\s+(clearance|approval|compliance)", "clearance guarantee"),
    (r"\bis\s+(fully\s+)?compliant\b", "compliance verdict"),
    (r"\bis\s+(legally\s+)?(definitely|certainly)\s+required\b", "unsupported legal claim"),
    (r"\blegally\s+required\b", "unsupported legal claim"),
    (r"this\s+shipment\s+(will|shall)\s+pass", "compliance verdict"),
    (r"\bverified\s+with\s+the\s+issuing\s+authority\b", "external-authenticity claim"),
    (r"\b(is|are)\s+authentic\b", "external-authenticity claim"),
    (r"\bcertified\s+authentic\b", "external-authenticity claim"),
    (r"\b(cace|customs assistant)\s+(approves|certifies|clears)\b", "compliance verdict"),
    (
        r"\b(these|those)\s+are\s+the\s+only\s+documents?\b"
        r"|\bthe\s+only\s+documents?\s+(you\s+)?(need|needed|require[ds]?)\b"
        r"|\bnothing\s+else\s+is\s+(needed|required)\b"
        r"|\bno\s+other\s+documents?\s+(are|is)\s+(needed|required)\b"
        r"|\bthis\s+is\s+(a\s+)?(the\s+)?(complete|exhaustive|full)\s+list\b"
        r"|\bno\s+(extra|further|additional|other)\s+documents?\b"
        r"|\bjust\s+prepare\s+these\b"
        r"|\bonly\s+need\s+(these|the\s+above)\b",
        "completeness claim",
    ),
    (r"\bofficer\s+will\s+(definitely|certainly)\b", "outcome guarantee"),
    (r"\byou\s+will\s+not\s+be\s+(charged|fined|penalised|penalized)\b", "outcome guarantee"),
)

_FORBIDDEN_CLAIM_RE = tuple(
    (re.compile(pattern, re.IGNORECASE), label) for pattern, label in _FORBIDDEN_CLAIM_PATTERNS
)


def forbidden_claim_violations(answer: str) -> list[str]:
    lowered = answer.casefold()
    return sorted({label for pattern, label in _FORBIDDEN_CLAIM_RE if pattern.search(lowered)})


# --- Number grounding -------------------------------------------------------
#
# Matches: percentages (17%, 5 percent), currency amounts (Rs. 50,000,
# PKR 20000, $500), day/month/year durations ("30 days", "6 months"),
# SRO/statutory references ("SRO 957(I)/2021"), and bare Section/Rule
# citations ("Section 32", "Rule 296"). Deliberately broad - a false
# positive here just means a true, correctly-cited number gets asked to
# prove itself against the evidence, which it will; a false negative lets an
# invented figure through, which is the one failure this exists to prevent.
#: Split into two alternatives rather than one ``(?:%|percent)\b`` group: a
#: trailing ``\b`` immediately after ``%`` never matches, because ``%`` and
#: the whitespace/punctuation that normally follows it in prose are both
#: non-word characters, so there is no word/non-word transition for ``\b`` to
#: anchor on. "percent" is a real word and keeps its boundary check.
_PERCENT = re.compile(
    r"\b\d{1,3}(?:\.\d+)?\s?%|\b\d{1,3}(?:\.\d+)?\s?percent\b", re.IGNORECASE
)
_CURRENCY = re.compile(
    r"(?:rs\.?|pkr|usd|\$)\s?\d[\d,]*(?:\.\d+)?\b", re.IGNORECASE
)
_DURATION = re.compile(
    r"\b\d{1,4}\s?(?:day|days|month|months|year|years|working\s+day|working\s+days)\b",
    re.IGNORECASE,
)
_SRO_REF = re.compile(
    r"\bsro\.?\s?\d{1,5}\s?\(?i{1,3}\)?\s?[/\-]?\s?\d{4}\b", re.IGNORECASE
)
#: No trailing ``\b``: when the optional ``(\d+)`` sub-clause is present the
#: match ends on ``)``, a non-word character, and the same "``\b`` right
#: after a non-word char never matches" problem as ``_PERCENT`` applies. The
#: leading ``\b`` plus the bounded ``[a-z]?`` are specific enough on their
#: own without it.
_SECTION_REF = re.compile(
    r"\b(?:section|sec\.?|rule|article|clause|schedule)\s+\d+[a-z]?(?:\(\d+\))?",
    re.IGNORECASE,
)
_TARIFF_CODE = re.compile(r"\b\d{4}[.\s-]?\d{4}\b")

_NUMBER_PATTERNS: tuple[re.Pattern[str], ...] = (
    _PERCENT,
    _CURRENCY,
    _DURATION,
    _SRO_REF,
    _SECTION_REF,
    _TARIFF_CODE,
)


def extract_numeric_claims(text: str) -> list[str]:
    """Every specific-figure span in ``text`` - percentages, money, durations,
    SRO/section references, tariff codes - each stripped and lowercased."""
    found: list[str] = []
    for pattern in _NUMBER_PATTERNS:
        found.extend(match.group(0).strip().lower() for match in pattern.finditer(text))
    return found


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text)


def _claim_in_evidence(claim: str, evidence_norm: str) -> bool:
    claim_norm = " ".join(claim.split())
    if not claim_norm:
        return False
    escaped = re.sub(r"\\ ", r"\\s+", re.escape(claim_norm))
    prefix = r"(?<![a-z0-9.])" if claim_norm[0].isalnum() else ""
    if claim_norm[-1].isdigit():
        suffix = r"(?![a-z0-9]|[.,]\d)"
    elif claim_norm[-1].isalpha():
        suffix = r"(?![a-z0-9])"
    else:
        suffix = ""
    return re.search(f"{prefix}{escaped}{suffix}", evidence_norm) is not None


def ungrounded_numbers(answer: str, evidence_text: str) -> list[str]:
    """Numeric claims in ``answer`` that do not appear anywhere in the
    evidence actually shown to the model. Used only for GROUNDED-mode
    answers; GENERAL-mode answers are checked with ``general_mode_numbers``
    instead, which is stricter (zero tolerance, no evidence to check against).

    A tariff-code-shaped claim is also matched on digits alone against actual
    8-digit tariff tokens in the evidence: the structured tariff-lookup tool's
    evidence always renders a code as eight plain digits ("62034200"), while a
    model often writes it in display form ("6203.4200").
    """
    evidence_norm = " ".join(evidence_text.lower().split())
    evidence_tariff_codes = {
        _digits_only(match.group(0))
        for match in _TARIFF_CODE.finditer(evidence_text)
    }
    ungrounded: list[str] = []
    for claim in extract_numeric_claims(answer):
        if _claim_in_evidence(claim, evidence_norm):
            continue
        claim_digits = _digits_only(claim)
        if _TARIFF_CODE.fullmatch(claim) and len(claim_digits) == 8 and claim_digits in evidence_tariff_codes:
            continue
        ungrounded.append(claim)
    return ungrounded


def general_mode_numbers(answer: str) -> list[str]:
    """Any specific-figure claim at all in a GENERAL-mode answer.

    GENERAL mode has no evidence behind it by definition (see
    docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 5, Ring 2), so *any* number
    this pattern set catches is ungrounded by construction - the prompt
    tells the model never to state one, and this is the mechanical backstop
    for when it does anyway.
    """
    return extract_numeric_claims(answer)
