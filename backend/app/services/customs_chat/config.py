"""Fixed response text and static suggestion pools for the pipeline.

Everything here is deterministic - never model output - because these
strings are used on the paths that must be reliable even when Groq is down:
refusals, provider-outage notices, and starter suggestions. Tunable
thresholds and model IDs live in ``core/config.py`` (``Settings``), not here;
this module is response *content*, not configuration.
"""

from __future__ import annotations

#: Layer 2 scope refusal and Layer 1 injection refusal share this message.
#: Deliberately generic rather than naming which check fired: an attacker
#: gets no signal about which layer caught them, and an honest off-topic
#: question gets the same short, friendly deflection either way.
OUT_OF_SCOPE_MESSAGE = (
    "That's outside what I cover - I only handle Pakistan customs questions. "
    "I can help with things like import duties, export documents, or what to "
    "do if a parcel is held at customs."
)

#: A generated answer failed the output guard badly enough (or exhausted its
#: retry/downgrade budget) that nothing safe could be shown.
GUARD_REFUSAL_MESSAGE = (
    "I could not confirm a safe, accurate answer to that just now. Could you "
    "rephrase your question, or ask something more specific about Pakistan "
    "customs?"
)

#: Groq is unreachable (no key, rate limited, outage). If evidence was
#: retrieved, the caller still shows source names/excerpts beneath this.
GENERATION_UNAVAILABLE_MESSAGE = (
    "The assistant is temporarily unavailable to write an explanation right "
    "now. Here is what the indexed sources say, if anything was found."
)

#: Appended when a GROUNDED answer was downgraded to GENERAL mid-pipeline
#: (the LLM verifier flagged low groundedness after the deterministic guard
#: already passed it) - the model's own citations are withheld because they
#: did not hold up, but the answer text itself already qualifies as general
#: guidance.
VERIFIER_DOWNGRADE_DISCLAIMER = (
    "This answer could not be fully confirmed against the indexed regulatory "
    "sources - treat it as general guidance and confirm with Pakistan "
    "Customs or a licensed customs agent."
)

STARTER_SUGGESTIONS: tuple[str, ...] = (
    "What documents do I need to export cotton yarn?",
    "How much duty do I pay on importing a laptop?",
    "What is a Goods Declaration?",
    "My parcel is held at customs - what do I do?",
    "What is the difference between Form-E and a Certificate of Origin?",
    "What is a bonded warehouse?",
    "Can I bring my own vehicle into Pakistan?",
    "What is duty drawback?",
)

_FOLLOW_UPS_BY_INTENT: dict[str, tuple[str, ...]] = {
    "customs_knowledge": (
        "What documents are usually needed for this?",
        "Who handles this at Pakistan Customs?",
    ),
    "document_help": (
        "Which authority issues this document?",
        "What happens if this document is missing?",
    ),
    "tariff_lookup": (
        "What is an HS code and how is it assigned?",
        "Are there other taxes charged at import besides duty?",
    ),
    "compliance_check": (
        "What documents are usually required for this?",
        "What happens if a shipment is held for review?",
    ),
    "meta": STARTER_SUGGESTIONS[:2],
    "out_of_scope": STARTER_SUGGESTIONS[:2],
}


def follow_ups_for(intent: str) -> tuple[str, ...]:
    return _FOLLOW_UPS_BY_INTENT.get(intent, STARTER_SUGGESTIONS[:2])
