"""Intent classification for the global regulatory assistant.

The pre-existing ``routing.py`` classifies questions that already have a
shipment attached, and its default branch is ``shipment_document_fact`` - a
sensible default there, and exactly the wrong one for a conversation with no
shipment, where it would silently reinterpret "what is Form-E?" as a question
about someone's invoice. This module is the shipment-free counterpart: the same
question space, but the default is *general regulatory information* and the
shipment intents are recognised only to be refused unless a shipment has been
explicitly selected.

Classification is deterministic and runs before retrieval, so the intent decides
which scope applies rather than being inferred from whatever the corpus happened
to return.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.services.assistant.domain_guard import DomainVerdict, check_domain
from app.services.assistant.destinations import extract_destination
from app.services.assistant.foundation import normalize_pct_code
from app.services.assistant.product_resolver import ProductResolution, resolve_product
from app.services.assistant.scopes import is_deterministic_compliance_scope

RegulatoryIntent = Literal[
    "general_regulatory_information",
    "regulatory_document_search",
    "supported_pct_guidance",
    "unsupported_pct_information",
    "shipment_document_fact",
    "shipment_audit_result",
    "combined_shipment_and_regulation",
    "audit_history",
    "product_clarification_required",
    "out_of_scope",
]

#: How the answer should be presented. Routing decided *what* to answer but
#: not *how*, so every informational intent used the same raw-passage dump -
#: which is why a checklist question came back as five regulatory quotations.
AnswerMode = Literal[
    "checklist",
    "clarification",
    "explanation",
    "evidence_lookup",
    "document_search",
    "refusal",
]

_PCT_IN_TEXT = re.compile(r"\b(\d{4})[.\s-]?(\d{4})\b")

_DOCUMENT_SEARCH = re.compile(
    r"\b(which|what|find|search|list|show)\b[^.?!]*"
    r"\b(pdf|pdfs|source|sources|document|documents|file|files|passage|passages|page|pages)\b",
    re.IGNORECASE,
)
_SEARCH_VERB = re.compile(
    r"\b(mention|mentions|mentioned|discuss|discusses|reference|references|"
    r"cite|cites|contain|contains|say|says|explain|explains|cover|covers)\b",
    re.IGNORECASE,
)

_COMPLIANCE_DECISION = re.compile(
    r"\b(is|are)\b[^.?!]*\b(compliant|allowed|permitted|legal|approved|cleared)\b"
    r"|\bwill\b[^.?!]*\bclear\b"
    r"|\bdo(es)?\s+(this|my|it)\b[^.?!]*\b(pass|comply|qualify)\b"
    r"|\bcan\s+i\s+(export|ship)\b",
    re.IGNORECASE,
)

#: "What documents do I need", "what paperwork should I prepare", "documents
#: needed for exporting X". Deliberately broader than _GUIDANCE_REQUEST and
#: checked before document search, because a checklist question is about the
#: user's own shipment, not about which PDFs mention a word.
_CHECKLIST_REQUEST = re.compile(
    r"\b(document|documents|paperwork|papers)\b[^.?!]*"
    r"\b(need|needed|require|required|prepare|prepared|preparing|submit|arrange|obtain)\b"
    r"|\b(need|require|prepare|preparing|arrange|obtain)\b[^.?!]*"
    r"\b(document|documents|paperwork|papers)\b"
    r"|\b(documents?|paperwork|papers)\s+(for|when|before)\b"    r"|\bwhat\s+(do\s+)?i\s+need\b"

    r"|\bchecklist\b",
    re.IGNORECASE,
)

#: "Which source says Form-E is required?" - one fact plus its citation,
#: as opposed to "find every document mentioning X", which is a search.
_EVIDENCE_LOOKUP = re.compile(
    r"\b(which|what|whose)\b[^.?!]*\b(source|sources|document|regulation|sro|page)\b"
    r"[^.?!]*\b(say|says|state|states|require|requires|mention|mentions|confirm|confirms|support|supports)\b",
    re.IGNORECASE,
)

#: Explicit corpus search: the user wants the document list itself.
_EXPLICIT_SEARCH = re.compile(
    r"\bfind\b|\bsearch\b|\blist (all|every|the)\b|\bevery indexed\b"
    r"|\ball (indexed )?(documents|sources)\b|\bwhich (pdf|pdfs|files?)\b",
    re.IGNORECASE,
)

_GUIDANCE_REQUEST = re.compile(
    r"\bwhat\s+documents?\b|\bwhich\s+documents?\b|\bdocuments?\s+(are\s+)?required\b"
    r"|\bwhat\s+(do\s+)?i\s+need\b|\bdocuments?\s+should\s+i\s+prepare\b"
    r"|\brequired\s+for\b",
    re.IGNORECASE,
)

_SHIPMENT_POSSESSIVE = re.compile(
    r"\bmy\s+(shipment|invoice|packing\s+list|consignment|documents?|coo|"
    r"certificate|form-?e|upload(ed)?)\b"
    r"|\bthis\s+shipment\b|\bthe\s+uploaded\b|\bmy\s+upload\b",
    re.IGNORECASE,
)
_AUDIT_RESULT = re.compile(
    r"\bdid\s+(my|this|the)\s+shipment\b|\bwhy\s+did\s+(it|my\s+shipment)\s+(pass|fail)\b"
    r"|\baudit\s+(result|status|outcome)\b|\bdid\s+it\s+pass\b",
    re.IGNORECASE,
)
_AUDIT_HISTORY = re.compile(
    r"\baudit\s+history\b|\bprevious\s+audit\b|\bearlier\s+revision\b"
    r"|\brevision\s+history\b|\bpast\s+(decisions|reviews)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IntentDecision:
    intent: RegulatoryIntent
    domain: DomainVerdict
    pct_code: str | None = None
    #: True when the intent needs a shipment the caller has not selected.
    requires_shipment_context: bool = False
    #: True when the user asked for a verdict ("is X compliant?") rather than
    #: for information. Outside the five supported codes this must be refused.
    compliance_decision_requested: bool = False
    #: How the answer should be presented.
    answer_mode: AnswerMode = "explanation"
    #: Product wording resolved to the supported catalog, when it was.
    product: ProductResolution | None = None
    #: Canonical destination named in the question, if any.
    destination: str | None = None


def extract_pct_code(text: str) -> str | None:
    """First eight-digit PCT-shaped token in the text, normalized."""
    match = _PCT_IN_TEXT.search(text or "")
    if not match:
        return None
    return normalize_pct_code(match.group(1) + match.group(2))


def classify_regulatory_intent(
    question: str,
    *,
    pct_filter: str | None = None,
    shipment_selected: bool = False,
) -> IntentDecision:
    """Route a question asked of the global regulatory assistant."""
    domain = check_domain(question)
    if not domain.in_domain:
        return IntentDecision("out_of_scope", domain)

    text = question or ""
    pct_code = normalize_pct_code(pct_filter) if pct_filter else extract_pct_code(text)
    supported = is_deterministic_compliance_scope(pct_code)
    verdict_requested = bool(_COMPLIANCE_DECISION.search(text))

    if _AUDIT_HISTORY.search(text):
        return IntentDecision(
            "audit_history", domain, pct_code, requires_shipment_context=not shipment_selected
        )
    if _AUDIT_RESULT.search(text):
        return IntentDecision(
            "shipment_audit_result",
            domain,
            pct_code,
            requires_shipment_context=not shipment_selected,
        )
    if _SHIPMENT_POSSESSIVE.search(text):
        # "Does my uploaded COO satisfy the configured rule?" needs both the
        # shipment and the rule; either way it cannot be served without one.
        combined = bool(
            re.search(r"\bsatisf|\brequir|\brule\b|\bregulation\b", text, re.IGNORECASE)
        )
        return IntentDecision(
            "combined_shipment_and_regulation" if combined else "shipment_document_fact",
            domain,
            pct_code,
            requires_shipment_context=not shipment_selected,
        )

    destination = extract_destination(text)
    checklist_wanted = bool(_CHECKLIST_REQUEST.search(text) or _GUIDANCE_REQUEST.search(text))

    # A checklist question is answered from the deterministic rules, so the
    # product has to be resolved to a supported code first. This runs before
    # document search: previously "what documents to prepare for cotton pants"
    # matched the document-search pattern and never reached guidance at all,
    # because a PCT code was only ever taken from digits typed in the question.
    product = resolve_product(text) if checklist_wanted else None
    if checklist_wanted and product is not None:
        if not pct_code and product.is_resolved:
            pct_code = product.pct_code
            supported = is_deterministic_compliance_scope(pct_code)
        elif not pct_code and product.is_ambiguous:
            return IntentDecision(
                "product_clarification_required",
                domain,
                None,
                answer_mode="clarification",
                product=product,
                destination=destination,
            )

    if pct_code and not supported:
        # Informational either way; the compliance-decision wording differs and
        # is applied by the caller.
        return IntentDecision(
            "unsupported_pct_information",
            domain,
            pct_code,
            compliance_decision_requested=verdict_requested,
            answer_mode="explanation",
            product=product,
            destination=destination,
        )

    if pct_code and supported:
        if checklist_wanted or verdict_requested:
            return IntentDecision(
                "supported_pct_guidance",
                domain,
                pct_code,
                compliance_decision_requested=verdict_requested,
                answer_mode="checklist",
                product=product,
                destination=destination,
            )
        return IntentDecision(
            "general_regulatory_information",
            domain,
            pct_code,
            answer_mode="explanation",
            destination=destination,
        )

    # A checklist question whose product could not be resolved is still not a
    # document search; it needs the product, not a pile of passages.
    if checklist_wanted and product is not None and not product.is_resolved:
        return IntentDecision(
            "product_clarification_required",
            domain,
            None,
            answer_mode="clarification",
            product=product,
            destination=destination,
        )

    # "Which source says Form-E is required?" wants one fact and its citation.
    if _EVIDENCE_LOOKUP.search(text) and not _EXPLICIT_SEARCH.search(text):
        return IntentDecision(
            "regulatory_document_search",
            domain,
            pct_code,
            answer_mode="evidence_lookup",
            destination=destination,
        )
    if _EXPLICIT_SEARCH.search(text) or (
        _DOCUMENT_SEARCH.search(text) and _SEARCH_VERB.search(text)
    ):
        return IntentDecision(
            "regulatory_document_search",
            domain,
            pct_code,
            answer_mode="document_search",
            destination=destination,
        )
    if _DOCUMENT_SEARCH.search(text):
        return IntentDecision(
            "regulatory_document_search",
            domain,
            pct_code,
            answer_mode="document_search",
            destination=destination,
        )

    return IntentDecision(
        "general_regulatory_information",
        domain,
        pct_code,
        answer_mode="explanation",
        destination=destination,
    )
