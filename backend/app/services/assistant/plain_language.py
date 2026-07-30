"""Plain-language display boundary for Ask CACE answers.

Asked "why is form e and COO is required", Ask CACE replied with this:

    Certificate required: value=conditional; TDAP certificate of origin or
    REX-related evidence when required by the destination market or
    preferential scheme. Approval required: value=False;...

Every word of that is traceable to a real indexed passage, and none of it is
usable by an exporter. The leak is not a stray ``str(obj)`` call: the curated
product summary is *rendered into corpus text* at ingestion (see
``sources.py::_render_product``), so ``value=False`` is genuinely part of the
stored passage. Quoting the passage verbatim as the answer therefore published
an internal serialization.

Two things live here as a result:

* ``sanitize_for_display`` - a hard boundary. No text reaches a user without
  passing through it, so a configuration token cannot become prose again, from
  this corpus or a future one.
* ``explain_concepts`` - deterministic plain-language templates for the concepts
  people actually ask about. These are written prose, not summarised passages,
  so the answer is a real answer rather than a quotation that happens to be
  nearby. They need no language model, which keeps wording stable and testable.

Retrieved passages are still cited; they are just no longer the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Display vocabulary
# --------------------------------------------------------------------------- #

#: Internal document identifiers to the words an exporter recognises.
DOCUMENT_LABELS: dict[str, str] = {
    "form_e": "Form-E / PSW Export Declaration",
    "certificate_of_origin": "Certificate of Origin",
    "certificate_required": "Certificate of Origin",
    "commercial_invoice": "Commercial Invoice",
    "packing_list": "Packing List",
    "bill_of_lading": "Bill of Lading",
    "export_contract": "Export Contract",
    "import_permit": "Import Permit",
    "phytosanitary_certificate": "Phytosanitary Certificate",
    "sbp_deposit_proof": "Proof of State Bank deposit",
    "sbp_confirmation": "State Bank confirmation",
    "irrevocable_letter_of_credit": "Irrevocable Letter of Credit",
    "goods_declaration": "Goods Declaration",
    "product_licence": "Export Licence",
    "product_permit": "Export Permit",
    "product_certificate": "Product Certificate",
    "product_approval": "Export Approval",
}

#: Compliance outcomes to language that says what the reader should do.
STATUS_PHRASES: dict[str, str] = {
    "passed": "Ready, based on the checks CACE could run",
    "failed": "Not ready yet",
    "manual_review": "Needs human confirmation",
    "not_applicable": "Does not apply to this case",
    "required": "Needed for export submission",
    "conditional": "Needed only when the stated condition applies",
}

#: Requirement flags. The raw form of each of these was appearing in answers.
REQUIREMENT_PHRASES: dict[str, str] = {
    "true": "Required.",
    "false": "Not required under the current matched rule.",
    "conditional": "Required only when the stated condition applies.",
    "none": "Not stated in the current matched rule.",
    "unknown": "Not stated in the current matched rule.",
}

#: Verification identifiers that were reaching users verbatim. Each gets an
#: intentional sentence rather than a prettified version of its own name.
VERIFICATION_PHRASES: dict[str, str] = {
    "verified_no_licence_required_under_epo_2022_general_permission": (
        "No separate export licence was found to be required under the matched "
        "general-permission rule."
    ),
    "verified_no_permit_required_under_epo_2022_general_permission": (
        "No separate export permit was found to be required under the matched "
        "general-permission rule."
    ),
    "verified_no_approval_required_under_epo_2022_general_permission": (
        "No separate export approval was found to be required under the matched "
        "general-permission rule."
    ),
    "verified_conditional_destination_based": (
        "Whether this is needed depends on the destination country and any trade "
        "scheme being used."
    ),
    "verified_from_local_official_tariff_pdf": (
        "Checked against the official tariff document held by CACE."
    ),
}

#: Evidence bookkeeping, in words that describe the situation to a reader.
EVIDENCE_PHRASES: dict[str, str] = {
    "direct_evidence": "A source names this document directly",
    "indirect_support": "Supported by related guidance rather than a direct mention",
    "configured_rule_only": "Comes from CACE's configured rules",
    "evidence_unavailable": "Not found in the current rules or sources",
    "conflicting_evidence": "Sources disagree; needs human confirmation",
    "accepted": "Accepted as relevant",
    "evidence_not_found": "Not found in the current sources",
}


def label_for_document(document_type: str | None) -> str:
    """The reader-facing name of a document type."""
    if not document_type:
        return "This document"
    key = document_type.strip().casefold()
    if key in DOCUMENT_LABELS:
        return DOCUMENT_LABELS[key]
    return key.replace("_", " ").strip().capitalize()


def phrase_for_status(status: str | None) -> str:
    if not status:
        return "Status not recorded"
    return STATUS_PHRASES.get(status.strip().casefold(), status.replace("_", " "))


def phrase_for_requirement_value(value: object) -> str:
    key = str(value).strip().casefold()
    return REQUIREMENT_PHRASES.get(key, REQUIREMENT_PHRASES["unknown"])


def phrase_for_evidence(evidence_class: str | None) -> str:
    if not evidence_class:
        return "Source not recorded"
    return EVIDENCE_PHRASES.get(
        evidence_class.strip().casefold(), evidence_class.replace("_", " ")
    )


# --------------------------------------------------------------------------- #
# The display boundary
# --------------------------------------------------------------------------- #

#: ``Licence required: value=False;`` and friends, as stored in corpus text.
_VALUE_ASSIGNMENT = re.compile(
    r"\bvalue\s*=\s*(true|false|conditional|none|null)\b", re.IGNORECASE
)
#: A bare snake_case identifier of two or more words. Deliberately requires an
#: underscore between word characters so ordinary prose is untouched.
_SNAKE_IDENTIFIER = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){1,}\b")
#: Python/JSON fragments that should never be shown at all.
_STRUCTURE_FRAGMENT = re.compile(r"\{['\"][^{}]{0,200}\}|\[\s*['\"][^\[\]]{0,200}\]")
#: Leftover key-ish prose such as ``source_kind:`` once the value is gone.
_TRAILING_KEY = re.compile(
    r"\b(source_kind|evidence_status|validation_status|verification_status"
    r"|extraction_method|rule_id|check_id|pct_codes|schema_name)\s*[:=]\s*",
    re.IGNORECASE,
)

#: Words that look like snake_case but are meaningful to a reader if we ever
#: emit them. Left alone so the sanitizer never mangles intended output.
_SAFE_IDENTIFIERS = frozenset({"form_e", "e_form"})


#: "evidence [1]", "passage 2", "according to source [3]" - the model naming
#: the numbered list it was shown. Replaced by wording that refers to the
#: sources the reader can actually see below the answer.
_EVIDENCE_MARKER_PHRASE = re.compile(
    r"\b(?:according to|per|from|in|see)?\s*"
    r"(?:the\s+)?(?:evidence|passage|source|excerpt|document)s?\s*"
    r"(?:\*\*)?\[\d+\](?:\*\*)?"
    r"|\b(?:evidence|passage|source|excerpt)\s+(?:number\s+)?\d+\b",
    re.IGNORECASE,
)

#: Any remaining bare marker, including one wrapped in markdown emphasis.
_EVIDENCE_MARKER = re.compile(r"(?:\*\*)?\[\d+\](?:\*\*)?")


def sanitize_for_display(text: str | None) -> str:
    """Remove internal serialization from anything shown to a user.

    Applied to every visible string, including text that came from the indexed
    corpus. Rewrites what has a known meaning, drops what does not, and never
    leaves a bare configuration token behind.
    """
    if not text:
        return ""
    cleaned = text

    # The [1], [2] markers number the evidence passages in the model's prompt.
    # The reader never sees that list, so a sentence like "the source is
    # evidence [1]" points at nothing. The prompt asks the model not to cite
    # them, but a prompt is a request, not a guarantee - this is the guarantee.
    cleaned = _EVIDENCE_MARKER_PHRASE.sub(" the indexed sources", cleaned)
    cleaned = _EVIDENCE_MARKER.sub("", cleaned)
    # The substitutions above can leave doubled or orphaned spaces mid-line.
    # Newlines are preserved - paragraph breaks carry meaning in these answers.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([.,;:])", r"\1", cleaned)

    # Known verification identifiers first: they are long and specific, and
    # would otherwise be shredded by the generic snake_case rule below.
    for identifier, phrase in VERIFICATION_PHRASES.items():
        cleaned = re.sub(re.escape(identifier), phrase, cleaned, flags=re.IGNORECASE)

    cleaned = _STRUCTURE_FRAGMENT.sub(" ", cleaned)

    # "Licence required: value=False;" -> "Licence required: not required ..."
    def _value(match: re.Match[str]) -> str:
        return phrase_for_requirement_value(match.group(1)).rstrip(".")

    cleaned = _VALUE_ASSIGNMENT.sub(_value, cleaned)
    cleaned = _TRAILING_KEY.sub("", cleaned)

    def _identifier(match: re.Match[str]) -> str:
        token = match.group(0)
        low = token.casefold()
        if low in _SAFE_IDENTIFIERS:
            return DOCUMENT_LABELS.get(low, token)
        if low in DOCUMENT_LABELS:
            return DOCUMENT_LABELS[low]
        if low in STATUS_PHRASES:
            return STATUS_PHRASES[low]
        if low in EVIDENCE_PHRASES:
            return EVIDENCE_PHRASES[low]
        # Unknown internal identifier: show readable words, never the key.
        return token.replace("_", " ")

    cleaned = _SNAKE_IDENTIFIER.sub(_identifier, cleaned)

    # Tidy the punctuation the substitutions leave behind.
    cleaned = re.sub(r"\s*;\s*(?=;|$)", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    return cleaned.strip()


def contains_internal_tokens(text: str | None) -> bool:
    """Whether text still carries anything that must not be displayed.

    Used by tests and by the answer validator as a last line of defence.
    """
    if not text:
        return False
    if _VALUE_ASSIGNMENT.search(text) or _TRAILING_KEY.search(text):
        return True
    if _STRUCTURE_FRAGMENT.search(text):
        return True
    for match in _SNAKE_IDENTIFIER.finditer(text):
        if match.group(0).casefold() not in _SAFE_IDENTIFIERS:
            return True
    return False


# --------------------------------------------------------------------------- #
# Deterministic concept explanations
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Concept:
    """One thing an exporter asks about, explained in advance rather than found.

    ``requirement`` states how CACE treats it, and is written to match the
    configured rules: the baseline clearance documents are normally required,
    the certificate of origin is conditional. Nothing here asserts a legal
    requirement beyond what those rules already encode.
    """

    key: str
    title: str
    what_it_is: str
    why_it_matters: str
    requirement: str
    example: str | None = None


FORM_E = Concept(
    key="form_e",
    title="Form-E / PSW Export Declaration",
    what_it_is=(
        "Form-E, now normally handled through the Pakistan Single Window (PSW) "
        "export declaration, is the form that records an export and its expected "
        "payment with your bank and with customs."
    ),
    why_it_matters=(
        "It is how the shipment and the money coming back for it are officially "
        "declared. Customs and your bank use it to tie the goods you send to the "
        "payment you receive."
    ),
    requirement="Normally required for export submission.",
    example=(
        "CACE checks that the exporter name and invoice number on the form match "
        "your Commercial Invoice. It does not authenticate the form with the bank "
        "or authority that issued it."
    ),
)

CERTIFICATE_OF_ORIGIN = Concept(
    key="certificate_of_origin",
    title="Certificate of Origin",
    what_it_is=(
        "A Certificate of Origin states where the goods were produced. In "
        "Pakistan it is usually issued by TDAP or a chamber of commerce."
    ),
    why_it_matters=(
        "The buyer and the destination country use it to confirm where the goods "
        "came from. It is also what you show when claiming a lower duty under a "
        "trade agreement."
    ),
    requirement=(
        "Conditional. It may be required by the destination country, the buyer, "
        "a bank, or the trade scheme you are using."
    ),
    example=(
        "For some destinations CACE's rules expect one; for others it only "
        "applies if you are claiming a trade-agreement benefit."
    ),
)

COMMERCIAL_INVOICE = Concept(
    key="commercial_invoice",
    title="Commercial Invoice",
    what_it_is=(
        "The Commercial Invoice is the seller's bill for the goods. It lists what "
        "is being sold, how much of it, the price, and the buyer and seller."
    ),
    why_it_matters=(
        "Customs use it to value the shipment, and it is the document every other "
        "one is checked against."
    ),
    requirement="Normally required for export submission.",
    example=None,
)

PACKING_LIST = Concept(
    key="packing_list",
    title="Packing List",
    what_it_is=(
        "A Packing List describes how the goods are packed, including the "
        "packages, quantities and weights."
    ),
    why_it_matters=(
        "Customs and the buyer use it to compare the physical shipment with the "
        "Commercial Invoice."
    ),
    requirement="Normally required for export submission.",
    example=None,
)

PCT_CODE = Concept(
    key="pct_code",
    title="PCT code",
    what_it_is=(
        "A PCT code is the number that classifies your product for customs. It is "
        "Pakistan's version of the international HS code, written as eight digits."
    ),
    why_it_matters=(
        "The code decides which rules and duties apply, so the paperwork you need "
        "follows from it."
    ),
    requirement=(
        "CACE gives document guidance for a set of validated textile codes. For "
        "other codes it can still explain the rules, but it will not issue a "
        "compliance decision."
    ),
    example="Cotton knitted T-shirts are 6109.1000.",
)

CUSTOMS_DECLARATION = Concept(
    key="customs_declaration",
    title="Customs declaration",
    what_it_is=(
        "A customs declaration is the formal statement to customs about what you "
        "are exporting, filed electronically through PSW."
    ),
    why_it_matters=(
        "It is the point at which the shipment is officially declared, and the "
        "supporting documents are attached to it."
    ),
    requirement="Required to move goods out of the country.",
    example=None,
)

EXPORTER = Concept(
    key="exporter",
    title="Exporter",
    what_it_is="The exporter is the business in Pakistan sending the goods abroad.",
    why_it_matters=(
        "The exporter's name must be the same across your documents; CACE reports "
        "a mismatch rather than deciding which one is right."
    ),
    requirement="Named on every export document.",
    example=None,
)

CONSIGNEE = Concept(
    key="consignee",
    title="Consignee",
    what_it_is="The consignee is the buyer or receiver the goods are being sent to.",
    why_it_matters=(
        "Customs and the carrier use it to confirm who is receiving the shipment."
    ),
    requirement="Named on every export document.",
    example=None,
)

DESTINATION_COUNTRY = Concept(
    key="destination_country",
    title="Destination country",
    what_it_is="The destination country is where the goods are being sent.",
    why_it_matters=(
        "Some paperwork depends on it, which is why CACE asks for it before giving "
        "a document checklist."
    ),
    requirement=(
        "CACE checks its configured destination rules for the product you select."
    ),
    example=None,
)

REQUIRED_DOCUMENT = Concept(
    key="required_document",
    title="Required document",
    what_it_is=(
        "A required document is one CACE's current rules expect for every "
        "shipment of that product."
    ),
    why_it_matters=(
        "Without it, CACE will not report the shipment as ready to submit."
    ),
    requirement="Prepare it before you submit.",
    example=None,
)

CONDITIONAL_DOCUMENT = Concept(
    key="conditional_document",
    title="Conditional document",
    what_it_is=(
        "A conditional document is one that is needed only in certain situations, "
        "such as a particular destination, buyer or trade scheme."
    ),
    why_it_matters=(
        "CACE lists it so you can check whether the condition applies to your "
        "shipment, rather than assuming it always does."
    ),
    requirement="Needed only when the stated condition applies.",
    example=None,
)

MANUAL_REVIEW = Concept(
    key="manual_review",
    title="Needs human confirmation",
    what_it_is=(
        "This means CACE could not settle something on its own and is asking a "
        "person to confirm it."
    ),
    why_it_matters=(
        "It usually happens when a value was hard to read, two documents disagree, "
        "or a rule depends on information the documents do not contain. CACE "
        "reports it instead of guessing."
    ),
    requirement=(
        "A person confirms or corrects the value, then the checks run again."
    ),
    example=None,
)

SUBMISSION_READINESS = Concept(
    key="submission_readiness",
    title="Required before submission",
    what_it_is=(
        "\"Required before submission\" means CACE expects the document to be in "
        "hand before you file the export with customs."
    ),
    why_it_matters=(
        "CACE is checking your paperwork beforehand so problems surface now rather "
        "than at the border."
    ),
    requirement=(
        "This is pre-submission guidance. CACE does not clear goods through "
        "customs."
    ),
    example=None,
)

CONCEPTS: tuple[Concept, ...] = (
    FORM_E,
    CERTIFICATE_OF_ORIGIN,
    COMMERCIAL_INVOICE,
    PACKING_LIST,
    PCT_CODE,
    CUSTOMS_DECLARATION,
    EXPORTER,
    CONSIGNEE,
    DESTINATION_COUNTRY,
    REQUIRED_DOCUMENT,
    CONDITIONAL_DOCUMENT,
    MANUAL_REVIEW,
    SUBMISSION_READINESS,
)

#: How a person refers to each concept. Ordered longest-first per concept so a
#: specific phrase wins over a generic word.
_CONCEPT_PATTERNS: tuple[tuple[re.Pattern[str], Concept], ...] = (
    (re.compile(r"\bform[\s\-_]?e\b|\be[\s\-]?form\b|psw export declaration", re.I), FORM_E),
    (
        re.compile(
            r"certificate of origin|\bcoo\b|\bc\.o\.o\b|country of origin"
            r"|where .{0,30}(were|was) (produced|made|manufactured)",
            re.I,
        ),
        CERTIFICATE_OF_ORIGIN,
    ),
    (re.compile(r"commercial invoice|\binvoice\b", re.I), COMMERCIAL_INVOICE),
    (re.compile(r"packing list|packing slip", re.I), PACKING_LIST),
    (re.compile(r"\bpct code\b|\bpct\b|\bhs code\b|tariff code", re.I), PCT_CODE),
    (
        re.compile(r"customs declaration|goods declaration|declare .{0,20}customs", re.I),
        CUSTOMS_DECLARATION,
    ),
    (re.compile(r"\bexporter\b", re.I), EXPORTER),
    (re.compile(r"\bconsignee\b", re.I), CONSIGNEE),
    (re.compile(r"destination country", re.I), DESTINATION_COUNTRY),
    (
        re.compile(r"human review|manual review|needs? (a )?human|why .{0,30}review", re.I),
        MANUAL_REVIEW,
    ),
    (
        re.compile(r"required before submission|submission ready|ready to submit", re.I),
        SUBMISSION_READINESS,
    ),
    (re.compile(r"required document|what does required mean", re.I), REQUIRED_DOCUMENT),
    (re.compile(r"conditional document|what does conditional mean", re.I), CONDITIONAL_DOCUMENT),
)

#: "is a COO always required?" needs the conditionality answered head-on.
_ALWAYS_QUESTION = re.compile(
    r"\balways\b|\bevery\b|\ball (exports?|shipments?|cases?)\b", re.I
)

NO_DESTINATION_RULE_NOTE = (
    "CACE did not find an additional destination-specific requirement in its "
    "current validated rules."
)

SCOPE_NOTE = (
    "CACE provides pre-submission guidance and does not issue customs clearance."
)


def detect_concepts(question: str) -> list[Concept]:
    """Concepts the question asks about, in the order they are mentioned."""
    text = question or ""
    found: list[tuple[int, Concept]] = []
    seen: set[str] = set()
    for pattern, concept in _CONCEPT_PATTERNS:
        match = pattern.search(text)
        if match and concept.key not in seen:
            seen.add(concept.key)
            found.append((match.start(), concept))
    return [concept for _, concept in sorted(found, key=lambda pair: pair[0])]


def _asks_why(question: str) -> bool:
    return bool(re.search(r"\bwhy\b|\bpurpose\b|what (is|are) .{0,20}for\b", question or "", re.I))


def explain_concepts(question: str, concepts: list[Concept]) -> str:
    """A short, plain answer written from templates rather than passages.

    One concept gets definition, purpose and requirement. Several get one short
    paragraph each followed by a combined requirement summary, so a question
    about two documents answers both instead of blending them.
    """
    if not concepts:
        return ""
    wants_why = _asks_why(question)
    always = bool(_ALWAYS_QUESTION.search(question or ""))
    parts: list[str] = []

    if len(concepts) == 1:
        concept = concepts[0]
        if always and concept is CERTIFICATE_OF_ORIGIN:
            parts.append(
                "No. It depends on the destination country, buyer, bank or "
                "applicable trade scheme. CACE checks its configured destination "
                "rules for the selected product."
            )
            parts.append(concept.what_it_is)
        else:
            parts.append(concept.what_it_is)
            parts.append(concept.why_it_matters)
            parts.append(f"In CACE's current rules: {concept.requirement}")
            if concept.example and not wants_why:
                parts.append(concept.example)
    else:
        for concept in concepts:
            body = concept.what_it_is
            if wants_why or len(concepts) <= 3:
                # One "why" sentence each keeps a two-document answer inside the
                # 160-word budget; the full reasoning is available by asking
                # about either document on its own.
                first_why = concept.why_it_matters.split(". ")[0].rstrip(".") + "."
                body = f"{body} {first_why}"
            parts.append(body)
        summary = ["In CACE's current rules:"]
        for concept in concepts:
            summary.append(f"- {concept.title} — {concept.requirement}")
        parts.append("\n".join(summary))

    parts.append(SCOPE_NOTE)
    return sanitize_for_display("\n\n".join(p for p in parts if p))
