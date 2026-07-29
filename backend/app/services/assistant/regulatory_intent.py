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
from app.services.assistant.foundation import normalize_pct_code
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
    "out_of_scope",
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

    if pct_code and not supported:
        # Informational either way; the compliance-decision wording differs and
        # is applied by the caller.
        return IntentDecision(
            "unsupported_pct_information",
            domain,
            pct_code,
            compliance_decision_requested=verdict_requested,
        )

    if pct_code and supported:
        if _GUIDANCE_REQUEST.search(text) or verdict_requested:
            return IntentDecision(
                "supported_pct_guidance",
                domain,
                pct_code,
                compliance_decision_requested=verdict_requested,
            )
        return IntentDecision("general_regulatory_information", domain, pct_code)

    if _DOCUMENT_SEARCH.search(text) and _SEARCH_VERB.search(text):
        return IntentDecision("regulatory_document_search", domain, pct_code)
    if _DOCUMENT_SEARCH.search(text):
        return IntentDecision("regulatory_document_search", domain, pct_code)

    return IntentDecision("general_regulatory_information", domain, pct_code)
