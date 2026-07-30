"""Layered scope guard for the global regulatory assistant.

Layer 1 of the four-layer guard described in the product scope: a deterministic
check that a question is about customs, trade, export documentation, regulatory
evidence or a CACE shipment - decided before any retrieval runs and without
asking a language model, so the boundary cannot be talked out of by the prompt.

The design constraint that shapes it: a flat keyword allowlist is either too
small (rejecting "what paperwork proves where exported goods originated?"
because it never says "certificate of origin") or too large (accepting anything
containing "document"). So two signals are combined instead:

* an anchor vocabulary of terms that are unambiguously trade/customs domain -
  any one of them admits the question on its own; and
* a pair of weaker vocabularies - a trade *context* and a document/process
  *concept* - where one of each together admits the question.

"Which document records what is packed inside each carton?" has no anchor, but
pairs ``document`` with ``carton``/``packed`` and is admitted. "Who won the
football match?" has neither, and "write a python function" is caught by an
out-of-domain intent list that overrides both signals, so smuggling a trade
word into a coding request does not buy access.

Layer 4 (answer validation) also lives here, because the claims a regulatory
answer may not make are the mirror image of the scope it is allowed to cover.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

OFF_TOPIC_MESSAGE = (
    "I can only answer questions about customs, trade, export documentation, "
    "regulatory evidence and shipments handled by CACE."
)

#: Any one of these admits the question. Stems, so "exporter"/"exporting"/
#: "exported" all match "export".
_ANCHOR_STEMS = (
    "customs",
    "export",
    "import",
    "re-export",
    "tariff",
    "hs code",
    "harmonized system",
    "harmonised system",
    "pct",
    "psw",
    "pakistan single window",
    "single window",
    "tdap",
    "trade development authority",
    "sbp",
    "state bank",
    "form-e",
    "form e",
    "e-form",
    "certificate of origin",
    "coo",
    "phytosanitary",
    "packing list",
    "commercial invoice",
    "bill of lading",
    "airway bill",
    "letter of credit",
    "goods declaration",
    "customs clearance",
    "export policy order",
    "sro ",
    "statutory regulatory order",
    "incoterm",
    "duty drawback",
    "chamber of commerce",
    "consignee",
    "consignor",
    "consignment",
    "shipment",
    "shipper",
    "freight",
    "customs duty",
    "plant quarantine",
    "quarantine",
    "free trade agreement",
    "preferential tariff",
    "hs classification",
    "trade regulation",
    "regulatory corpus",
    "cace",
)

#: Weak signal A - the question is situated in cross-border movement of goods.
_TRADE_CONTEXT = (
    "ship",
    "shipping",
    "shipped",
    "cargo",
    "carton",
    "crate",
    "pallet",
    "container",
    "packed",
    "packing",
    "packaging",
    "abroad",
    "overseas",
    "foreign",
    "cross-border",
    "border",
    "port",
    "vessel",
    "warehouse",
    "bonded",
    "transit",
    "transshipment",
    "goods",
    "merchandise",
    "commodity",
    "commodities",
    "declared",
    "declare",
    "declaration",
    "origin",
    "originated",
    "duty",
    "duties",
    "clearance",
    "cleared",
    "exporter",
    "importer",
    "buyer",
    "seller",
    "trade",
    "tariff",
    "upload",
    "uploaded",
    "textile",
    "cotton",
    "yarn",
    "denim",
    "goods declaration",
)

#: Weak signal B - the question is about a document, rule or process.
_DOC_CONCEPT = (
    "document",
    "documentation",
    "paperwork",
    "form",
    "certificate",
    "certification",
    "invoice",
    "permit",
    "licence",
    "license",
    "approval",
    "declaration",
    "rule",
    "rules",
    "regulation",
    "regulatory",
    "requirement",
    "required",
    "policy",
    "procedure",
    "process",
    "evidence",
    "record",
    "records",
    "proof",
    "prove",
    "proves",
    "source",
    "sources",
    "indexed",
    "corpus",
    "clause",
    "law",
    "legal",
    "authority",
    "compliance",
    "compliant",
    "audit",
    "checklist",
    "issued",
    "issuing",
)

#: Overrides both signals. These are requests for something CACE does not do,
#: even when trade vocabulary is present ("write a python parser for invoices").
_OUT_OF_DOMAIN_INTENT = (
    r"\bwrite\b[^.?!]*\b(code|function|script|program|poem|song|story|essay|joke)\b",
    r"\b(python|javascript|typescript|java|c\+\+|golang|rust|sql query|regex)\b",
    r"\b(football|cricket|soccer|basketball|tennis|match result|score|world cup)\b",
    r"\b(movie|movies|film|films|netflix|song|lyrics|album|celebrity)\b",
    r"\b(recipe|cook|cooking|restaurant)\b",
    r"\b(repair|fix)\b[^.?!]*\b(phone|laptop|car|bike|tv|computer)\b",
    r"\b(love poem|dating|horoscope|astrology)\b",
    r"\bsort(ing)?\s+(function|algorithm|array|list)\b",
)

#: Layer 4 also uses these: an instruction embedded in a retrieved passage is
#: content to display, never a directive to follow.
_INJECTION_PATTERNS = (
    r"ignore\s+(all\s+|any\s+|your\s+|the\s+)?(previous\s+|prior\s+|above\s+)?instructions?",
    r"disregard\s+(all\s+|any\s+|the\s+)?(previous\s+|prior\s+|above\s+)?(instructions?|rules?)",
    r"forget\s+(everything|all\s+previous|your\s+instructions)",
    r"you\s+are\s+now\s+",
    r"pretend\s+(you\s+are|to\s+be)",
    r"act\s+as\s+(if|a|an)\s+",
    r"system\s+prompt",
    r"jailbreak",
    r"override\s+(your|the)\s+(instructions?|rules?|settings?)",
    r"new\s+instructions?\s*:",
    r"set\s+(the\s+)?status\s+to\s+",
    r"mark\s+(this\s+)?shipment\s+(as\s+)?(approved|passed|cleared)",
)

#: An eight-digit tariff code, written plainly or as 6203.4200. Users drop
#: the word "PCT" constantly ("is 62034200 compliant?"), and without this a
#: question that is unmistakably about customs classification has no anchor
#: term at all and gets refused as off-topic.
_TARIFF_CODE = re.compile(r"\b\d{4}[.\s-]?\d{4}\b")

_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)
_OUT_OF_DOMAIN_RE = re.compile("|".join(_OUT_OF_DOMAIN_INTENT), re.IGNORECASE)

#: Claims a grounded regulatory answer is never allowed to make.
_FORBIDDEN_CLAIM_PATTERNS = (
    (r"will\s+(certainly\s+|definitely\s+)?clear\s+customs", "customs-clearance claim"),
    (r"guarantee[sd]?\s+(clearance|approval|compliance)", "clearance guarantee"),
    (r"\bis\s+(fully\s+)?compliant\b", "compliance verdict"),
    (r"\bis\s+(legally\s+)?(definitely|certainly)\s+required\b", "unsupported legal claim"),
    (r"\blegally\s+required\b", "unsupported legal claim"),
    (r"this\s+shipment\s+(will|shall)\s+pass", "compliance verdict"),
    (r"\bverified\s+with\s+the\s+issuing\s+authority\b", "external-authenticity claim"),
    (r"\b(is|are)\s+authentic\b", "external-authenticity claim"),
    (r"\bcertified\s+authentic\b", "external-authenticity claim"),
    (r"\bcace\s+(approves|certifies|clears)\b", "compliance verdict"),
    # Completeness is a claim about everything the corpus does *not* say, which
    # retrieved passages can never support. A live answer about denim pants
    # ended "These are the only documents required for denim pants export
    # according to the indexed sources" - an exporter who trusted that would
    # arrive at customs short of paperwork.
    (
        r"\b(these|those)\s+are\s+the\s+only\s+documents?\b"
        r"|\bthe\s+only\s+documents?\s+(you\s+)?(need|needed|require[ds]?)\b"
        r"|\bnothing\s+else\s+is\s+(needed|required)\b"
        r"|\bno\s+other\s+documents?\s+(are|is)\s+(needed|required)\b"
        r"|\bthis\s+is\s+(a\s+)?(the\s+)?(complete|exhaustive|full)\s+list\b",
        "completeness claim",
    ),
)


@dataclass(frozen=True)
class DomainVerdict:
    in_domain: bool
    reason: str
    injection_detected: bool = False
    matched_signals: tuple[str, ...] = ()


@dataclass
class AnswerValidation:
    ok: bool
    violations: list[str] = field(default_factory=list)


def _normalize(question: str) -> str:
    return re.sub(r"\s+", " ", question or "").strip().casefold()


def contains_injection(text: str | None) -> bool:
    """Whether text carries an instruction aimed at the assistant itself."""
    return bool(text) and bool(_INJECTION_RE.search(text or ""))


def _stem_hits(haystack: str, vocabulary: tuple[str, ...]) -> list[str]:
    """Match multi-word terms literally, short terms exactly, others as stems.

    Short terms are anchored at both ends because a three-letter prefix match
    is mostly false positives ("coo" inside "cooking", "sro" inside "srock").
    Longer terms match as a prefix so one entry covers export/exporter/exported.
    """
    hits: list[str] = []
    for term in vocabulary:
        if " " in term or "-" in term or "/" in term:
            if term in haystack:
                hits.append(term)
            continue
        pattern = (
            rf"\b{re.escape(term)}\b" if len(term) <= 4 else rf"\b{re.escape(term)}"
        )
        if re.search(pattern, haystack):
            hits.append(term)
    return hits


def check_domain(question: str) -> DomainVerdict:
    """Layer 1: decide whether the question is inside CACE's subject scope."""
    text = _normalize(question)
    if not text:
        return DomainVerdict(False, "empty question")

    injection = contains_injection(text)

    if _OUT_OF_DOMAIN_RE.search(text):
        return DomainVerdict(
            False,
            "request is for content outside customs and trade scope",
            injection_detected=injection,
        )
    if injection:
        # A prompt-injection attempt is refused on its own terms even when it is
        # dressed in trade vocabulary.
        return DomainVerdict(
            False,
            "request attempts to override assistant instructions",
            injection_detected=True,
        )

    anchors = _stem_hits(text, _ANCHOR_STEMS)
    if anchors:
        return DomainVerdict(True, "matched domain anchor term", matched_signals=tuple(anchors[:4]))

    context = _stem_hits(text, _TRADE_CONTEXT)
    concepts = _stem_hits(text, _DOC_CONCEPT)
    if _TARIFF_CODE.search(text):
        # A tariff code is a trade signal, but on its own it is just a number,
        # so it still has to pair with a document/regulatory concept below.
        context = context + ["tariff-code"]
    if context and concepts:
        return DomainVerdict(
            True,
            "matched trade context and document/regulatory concept",
            matched_signals=tuple((context[:2] + concepts[:2])),
        )

    return DomainVerdict(False, "no customs, trade or regulatory signal found")


def validate_answer(answer: str, *, has_accepted_evidence: bool) -> AnswerValidation:
    """Layer 4: reject claims a grounded regulatory answer may not make."""
    violations: list[str] = []
    lowered = answer.casefold()
    for pattern, label in _FORBIDDEN_CLAIM_PATTERNS:
        if re.search(pattern, lowered):
            violations.append(label)
    if contains_injection(answer):
        violations.append("answer echoes an embedded instruction")
    if not has_accepted_evidence and re.search(
        r"according to|the regulation states|the corpus states", lowered
    ):
        violations.append("uncited regulatory claim")
    return AnswerValidation(ok=not violations, violations=sorted(set(violations)))
