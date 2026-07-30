"""The global regulatory knowledge assistant.

This is the conversational half of CACE that the supported-PCT "Prepare an Export"
form is not. It answers questions about the indexed regulatory corpus without
requiring a shipment, an upload, an audit workflow or one of the five supported
PCT codes, and it never issues a compliance verdict outside those five codes.

The four guard layers run in order:

1. domain check      - deterministic, pre-retrieval (``domain_guard``)
2. retrieval         - the existing hybrid regulatory RAG, unmodified
3. evidence gate     - the retriever's own lexical relevance floor
4. answer validation - forbidden claims and citation/passage correspondence

Answers are composed **extractively** from accepted passages. No language model
is called, which is why an answer can never be produced from model memory and
presented as regulatory fact: every sentence in a grounded answer is either
fixed framing text or a quotation whose citation is checked against the
retrieved set. Instructions embedded in a retrieved document are quoted as
content and are structurally incapable of steering the response, because
nothing in this path interprets passage text as a directive.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.assistant import AssistantConversation, AssistantMessage
from app.schemas.assistant import (
    ChecklistDocumentSchema,
    ProductCandidateSchema,
    RegulatoryChatResponse,
    RegulatoryCitationSchema,
)
from app.services.assistant.domain_guard import (
    OFF_TOPIC_MESSAGE,
    contains_injection,
    validate_answer,
)
from app.services.assistant.destinations import canonical_destination
from app.services.assistant.foundation import SUPPORTED_PCT_PRODUCTS
from app.services.assistant.plain_language import (
    Concept,
    NO_DESTINATION_RULE_NOTE,
    contains_internal_tokens,
    detect_concepts,
    explain_concepts,
    sanitize_for_display,
)
from app.services.assistant.guidance import (
    generate_pre_submission_guidance,
    get_document_explanation,
)
from app.services.assistant.regulatory_intent import (
    IntentDecision,
    classify_regulatory_intent,
)
from app.services.assistant.scopes import (
    GENERAL_LIMITATION,
    INFORMATIONAL_LIMITATION,
    NO_EVIDENCE_MESSAGE,
    UNSUPPORTED_PCT_NOTICE,
    supported_compliance_scope_labels,
)
from app.core.exceptions import StructuredExtractionProviderError
from app.services.structured_extraction_service import (
    generate_checklist_explanation,
    generate_grounded_explanation,
)
from app.services.regulatory.citation_validation import (
    DraftCitation,
    validate_rag_output,
)
from app.services.regulatory.retrieval import (
    ScoredEvidence,
    search_regulatory_evidence,
)
from app.services.regulatory.source_kinds import (
    resolve_source_kind,
    get_corpus_snapshot_date,
    is_official,
    referenced_official_source,
    source_kind_label,
)

SHIPMENT_CONTEXT_REQUIRED_MESSAGE = (
    "That question is about a specific shipment. This is the general regulatory "
    "assistant and it is not connected to one. Open the shipment you mean and "
    "ask there, so the answer is drawn from that shipment's own documents and "
    "frozen audit result."
)

VEHICLE_IMPORT_SCOPE_MESSAGE = (
    "CACE currently focuses on Pakistan textile exports and cannot provide a "
    "verified vehicle-import procedure."
)
_VEHICLE_IMPORT_REQUEST = re.compile(
    r"\b(import|bring)\b[^.?!]{0,80}\b(car|cars|vehicle|vehicles|automobile|automobiles)\b"
    r"|\b(car|cars|vehicle|vehicles|automobile|automobiles)\b[^.?!]{0,80}\b(import|bring)\b",
    re.IGNORECASE,
)
_FORM_E_DEFINITION_QUESTION = re.compile(
    r"^\s*(what\s+is|define|explain)\s+(a\s+)?form[\s-]?e\s*[?.]?\s*$",
    re.IGNORECASE,
)

SUGGESTED_QUESTIONS = [
    "What is Form-E?",
    "What is a Certificate of Origin?",
    "Which indexed sources explain PSW export declarations?",
    "What documents normally describe the goods in a shipment?",
    "Which sources mention phytosanitary requirements?",
    "What does the indexed corpus say about cotton exports?",
    "Search for references to PCT 52010090.",
    "How does CACE distinguish official sources from curated summaries?",
]

#: Removed before retrieval. The retriever's relevance gate measures the share
#: of *query* tokens present in a passage, so question scaffolding ("what is
#: the", "which indexed sources say") dilutes a short, precise query below the
#: floor and a genuinely relevant passage gets discarded. Only content-free
#: words are listed; no domain term is stripped.
_QUERY_STOPWORDS = frozenset(
    """
    a an the and or of to in on for from by with about is are was were be been
    being do does did can could should would will shall may might must i me my
    mine we us our you your it its this that these those there here what which
    who whom whose when where why how please tell show give find search list
    explain describe say says said mention mentions discuss discusses talk
    talks any some all each every into at as if then than so such not no yes
    indexed corpus cace
    source sources specifically specific regarding concerning related relating
    broader exists exist anything something mentioned
    passage passages excerpt excerpts quote quotes text texts
    mentioning containing referencing covering discussing stating saying
    listing finding searching
    """.split()
)

#: Deterministic concept normalization: a phrasing the user might use, mapped
#: to the vocabulary the corpus actually contains.
#:
#: This exists because the retriever's evidence gate scores a passage by the
#: share of *query* tokens it contains. Feeding a whole sentence in dilutes
#: that share with words no regulation uses ("paperwork", "proves", "records"),
#: so a genuinely relevant passage falls under the floor and the assistant
#: reports no evidence for a question the corpus answers. Rewriting the
#: question to its canonical domain terms is query understanding, not a second
#: retriever - the same hybrid search, evidence gate and ranking still decide
#: the result. Order matters: the more specific concept wins.
_CONCEPTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"certificate of origin|\bcoo\b|country of origin|origin of the goods"
            r"|where .{0,40}(originat|manufactur|produc)",
            re.IGNORECASE,
        ),
        "certificate of origin destination preferential tariff",
    ),
    (
        re.compile(
            r"packing list|packed inside|what is packed|carton|package contents"
            r"|contents of each|inside each",
            re.IGNORECASE,
        ),
        "packing list supporting documents export clearance",
    ),
    (
        re.compile(r"form[-\s]?e\b|e-?form\b", re.IGNORECASE),
        "form-e supporting documents required export clearance",
    ),
    (
        re.compile(
            r"electronic\w*\b.{0,40}\b(declar|export|submit|file)"
            r"|export declaration|goods declaration|web[-\s]?based"
            r"|declared? electronic",
            re.IGNORECASE,
        ),
        "customs clearance procedure export web-based declaration",
    ),
    (
        re.compile(
            r"phytosanitar|plant quarantine|agricultur|plant material|plant health",
            re.IGNORECASE,
        ),
        "phytosanitary certificate measure",
    ),
    (
        re.compile(r"letter of credit|\bl/?c\b|irrevocable", re.IGNORECASE),
        "irrevocable letter of credit",
    ),
    (
        re.compile(r"state bank|\bsbp\b|security deposit", re.IGNORECASE),
        "state bank of pakistan security deposit",
    ),
    (
        re.compile(r"commercial invoice|\binvoices?\b", re.IGNORECASE),
        "commercial invoice supporting documents export clearance",
    ),
    (
        re.compile(r"pakistan single window|\bpsw\b", re.IGNORECASE),
        "pakistan single window customs clearance procedure export",
    ),
    (
        re.compile(r"\btdap\b|trade development authority", re.IGNORECASE),
        "trade development authority of pakistan",
    ),
    (
        re.compile(r"\bcotton\b|\byarn\b|\bdenim\b|\btextiles?\b", re.IGNORECASE),
        "cotton textile export",
    ),
    (
        re.compile(
            r"export polic|\bsro\b|regulations?\b|\brules?\b|\blaws?\b|prohibit|restrict",
            re.IGNORECASE,
        ),
        "export policy order schedule conditions",
    ),
)


#: A tariff code as typed: eight digits, optionally split by a dot or space.
_PCT_SHAPED = re.compile(r"\b\d{4}[.\s-]?\d{4}\b")


def _normalize_for_retrieval(question: str, pct_code: str | None) -> str:
    """Rewrite a question into the corpus's own vocabulary before retrieval."""
    text = question or ""
    # The question's own words come first, and the canonical vocabulary is
    # added after them. Matching a concept used to *replace* the question
    # entirely, which discarded exactly the terms that made it specific:
    # "What is the 180 day rule for raw cotton?" was searched as "cotton
    # textile export policy order schedule conditions", with "180" and "day"
    # gone, so the SRO that states the rule could never rank. Expansion helps
    # recall; substitution destroys precision.
    # Tariff codes come from the ``pct_code`` argument, never from the
    # question's own words. Stage 2 broadens precisely by dropping the code,
    # and it cannot do that if the digits the user typed are still in the
    # query - a question naming an unsupported code would retrieve nothing at
    # all instead of the related-category passages it is meant to fall back to.
    stripped = _PCT_SHAPED.sub(" ", text.casefold())
    words = re.findall(r"[a-z0-9][a-z0-9\-]*", stripped)
    parts = [word for word in words if word not in _QUERY_STOPWORDS] or words
    for _pattern, terms in _CONCEPTS:
        if _pattern.search(text):
            parts.extend(terms.split())
    if pct_code:
        parts.append(pct_code)
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            ordered.append(part)
    return " ".join(ordered)


def _first_sentences(text: str | None, max_chars: int = 420) -> str:
    """A readable prefix of an accepted passage, cut on a clean boundary."""
    normalized = " ".join((text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    window = normalized[:max_chars]
    cut = max(window.rfind(". "), window.rfind("; "))
    if cut < max_chars // 3:
        cut = window.rfind(" ")
    return window[: cut + 1].rstrip() + "…" if cut > 0 else window + "…"


_DESTINATION = re.compile(
    r"\b(?:to|for|into|destined for)\s+(?P<country>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"
)


_NOT_A_COUNTRY = {"pct", "form", "cace", "customs", "psw", "sbp", "tdap", "hs"}


def _extract_destination(question: str) -> str | None:
    """Pull a capitalised destination out of "... to China" style questions.

    Every "to"/"for" match is considered, because the first one is usually
    prepositional noise ("required for PCT 61091000 to China").
    """
    for match in _DESTINATION.finditer(question or ""):
        country = match.group("country").strip()
        if country.casefold().split()[0] not in _NOT_A_COUNTRY:
            return country
    return None


def _render_guidance_answer(guidance) -> str:  # type: ignore[no-untyped-def]
    """Restate the deterministic checklist as chat text, verdicts intact."""
    if not guidance.supported_scope:
        return guidance.answer or UNSUPPORTED_PCT_NOTICE
    lines = [
        f"For {guidance.product} under PCT {guidance.pct_code} to "
        f"{guidance.destination}, the configured CACE rules expect these documents:",
        "",
    ]
    for doc in guidance.documents:
        lines.append(
            f"• {doc.display_name} — {doc.requirement} ({doc.evidence_class.replace('_', ' ')})"
        )
    lines.append("")
    lines.append(
        "This is the deterministic supported-PCT checklist. Open Prepare an Export "
        "for the per-document reasons, cited passages and evidence classification."
    )
    return "\n".join(lines)


EXACT_PCT = "exact_pct"
BROADER_CATEGORY = "broader_category"
NO_PCT_SCOPE = "not_pct_specific"
NO_EVIDENCE = "none"


def broader_category_notice(pct_code: str) -> str:
    return (
        f"I did not find evidence specifically mentioning PCT {pct_code}. The "
        "following passages concern the broader product category and should not "
        "be treated as a compliance determination for that code."
    )


def _mentions_pct_in_text(chunk, pct_code: str) -> bool:  # type: ignore[no-untyped-def]
    """Whether the passage text itself names this PCT code.

    Metadata tags are deliberately not accepted as proof. A whole source
    document is tagged at ingestion with every code it covers anywhere, so a
    page about unrelated Schedule-III goods carries all five supported codes -
    exactly the false-positive the retrieval module documents. "Evidence
    specifically mentioning PCT X" has to mean the text says so.
    """
    digits = re.sub(r"\D", "", pct_code or "")
    if len(digits) != 8:
        return False
    body = re.sub(r"[.\s-]", "", chunk.text or "")
    return digits in body


def _staged_retrieve(
    db: Session,
    *,
    question: str,
    pct_code: str | None,
    supported: bool,
    destination: str | None,
    source_document: str | None,
    top_k: int,
) -> tuple[list[ScoredEvidence], str]:
    """Two-stage retrieval that keeps "about this code" separate from "related".

    Stage 1 searches with the exact code present, then keeps only passages whose
    text actually names it. Stage 2 runs only when stage 1 found nothing and
    broadens to the product terms in the question.

    The distinction is the point. Dropping an unsupported code from the query
    and presenting whatever generic cotton passage came back is how an answer
    ends up implying the corpus says something about a code it never mentions.
    """
    if not pct_code:
        query = _normalize_for_retrieval(question, None)
        results = _retrieve(
            db,
            query=query,
            pct_code=None,
            destination=destination,
            source_document=source_document,
            top_k=top_k,
        )
        return results, (NO_PCT_SCOPE if results else NO_EVIDENCE)

    # Stage 1 - exact code. The metadata filter is applied only for codes the
    # corpus is actually tagged with; for any other code it would return an
    # empty set and mask the broader stage.
    stage_one = _retrieve(
        db,
        query=_normalize_for_retrieval(question, pct_code),
        pct_code=pct_code if supported else None,
        destination=destination,
        source_document=source_document,
        top_k=top_k,
    )
    exact = [item for item in stage_one if _mentions_pct_in_text(item.chunk, pct_code)]
    if exact:
        return exact, EXACT_PCT

    # Stage 2 - broader product category, code removed from the query.
    broader = _retrieve(
        db,
        query=_normalize_for_retrieval(question, None),
        pct_code=None,
        destination=destination,
        source_document=source_document,
        top_k=top_k,
    )
    if broader:
        return broader, BROADER_CATEGORY
    return [], NO_EVIDENCE


def _is_quoted_from(snippet: str, passage: str) -> bool:
    """Whether a displayed snippet really came from an accepted passage.

    Whitespace is normalized because the snippet re-joins sentences that the
    source split across lines, and a trailing ellipsis marks truncation.
    """
    normalized_passage = " ".join((passage or "").split())
    candidate = " ".join((snippet or "").split()).rstrip("…").strip()
    return bool(candidate) and candidate in normalized_passage


def _to_citation(
    evidence: ScoredEvidence, snapshot_date, /
) -> RegulatoryCitationSchema:
    chunk = evidence.chunk
    kind = resolve_source_kind(chunk)
    return RegulatoryCitationSchema(
        title=chunk.source_document,
        source_kind=kind,
        source_kind_label=source_kind_label(kind),
        is_official=is_official(kind),
        issuing_authority=chunk.issuing_authority,
        page_number=chunk.page_number,
        section=chunk.section,
        publication_date=chunk.issue_date,
        effective_date=chunk.effective_date,
        corpus_snapshot_date=snapshot_date,
        accepted_passage=chunk.text,
        evidence_status="accepted",
        source_url=chunk.source_url,
        sro_number=chunk.sro_number,
        referenced_official_source=referenced_official_source(chunk, kind),
    )


def _compose_grounded_answer(
    citations: list[RegulatoryCitationSchema], intent: str
) -> tuple[str, list[str]]:
    """Return the answer text and the quoted snippets it contains.

    The snippets are returned separately because the two Layer-4 checks apply
    to different halves of the answer: forbidden *claims* are checked against
    CACE's own framing only (a regulation may legitimately say "legally
    required"; CACE may not say it on its own account), while the snippets are
    checked for verbatim correspondence with an accepted passage.
    """
    lead = {
        "regulatory_document_search": (
            "These indexed sources contain passages matching your search:"
        ),
        "unsupported_pct_information": (
            "The indexed regulatory corpus contains the following related passages:"
        ),
    }.get(intent, "Based on the indexed CACE regulatory corpus:")

    lines = [lead, ""]
    snippets: list[str] = []
    for index, citation in enumerate(citations, start=1):
        where = [citation.source_kind_label]
        if citation.issuing_authority:
            where.append(citation.issuing_authority)
        if citation.page_number is not None:
            where.append(f"page {citation.page_number}")
        elif citation.section:
            where.append(citation.section)
        snippet = _first_sentences(citation.accepted_passage)
        snippets.append(snippet)
        lines.append(f"[{index}] {citation.title} — {', '.join(where)}")
        lines.append(f'    "{snippet}"')
        lines.append("")
    lines.append(
        "Passages are quoted from the corpus snapshot; see the source cards for "
        "the full accepted text and provenance."
    )
    return "\n".join(lines).strip(), snippets



NO_USA_SPECIFIC_RULE = (
    "CACE did not find an additional USA-specific requirement in its current "
    "validated rules."
)

#: Topics that can never be primary evidence for a garment or made-up checklist,
#: however often they say "cotton". The failing query returned passages about
#: cotton seeds, cotton waste, agricultural phytosanitary rules and vegetable
#: ghee, all of which share only the word "cotton" with a question about
#: trousers.
_NON_PRODUCT_TOPICS = re.compile(
    r"cotton\s+seed|seeds?\s+for\s+sowing|cotton\s+waste|linters"
    r"|vegetable\s+ghee|cooking\s+oil|edible\s+oil"
    r"|plant\s+protection|phytosanitary|plant\s+quarantine|nppo"
    r"|wheat|pulses|tobacco|scrap",
    re.IGNORECASE,
)

#: Products whose rules genuinely do concern plant material, so the filter above
#: must not be applied to them.
_AGRICULTURAL_CATEGORIES = frozenset({"raw_material"})


def _is_product_relevant(passage: str, category: str | None) -> bool:
    """Whether a passage may back a product checklist for this category."""
    if category in _AGRICULTURAL_CATEGORIES:
        return True
    return not _NON_PRODUCT_TOPICS.search(passage or "")


def _checklist_documents(
    guidance,  # type: ignore[no-untyped-def]
) -> tuple[list[ChecklistDocumentSchema], list[ChecklistDocumentSchema]]:
    """Split the deterministic checklist into required and conditional lines."""
    required: list[ChecklistDocumentSchema] = []
    conditional: list[ChecklistDocumentSchema] = []
    for doc in guidance.documents:
        line = ChecklistDocumentSchema(
            display_name=doc.display_name,
            requirement=doc.requirement,
            condition=(doc.summary or None) if doc.requirement == "conditional" else None,
        )
        (required if doc.requirement == "required" else conditional).append(line)
    return required, conditional


def _render_checklist(
    guidance,  # type: ignore[no-untyped-def]
    *,
    destination: str | None,
    corrections: dict[str, str],
) -> str:
    """A short exporter-facing checklist, not a pile of quotations."""
    required, conditional = _checklist_documents(guidance)
    lines: list[str] = []
    if corrections:
        readable = "; ".join(f"'{typed}' as '{used}'" for typed, used in corrections.items())
        lines.append(f"Interpreting {readable}.")
    where = f" to {destination}" if destination else ""
    lines.append(
        f"For {guidance.product} (PCT {guidance.pct_code}){where}, prepare these "
        "documents:"
    )
    if required:
        lines += ["", "Required documents"]
        lines += [
            f"- {d.display_name}: {get_document_explanation(_document_type_for_name(d.display_name))}"
            for d in required
        ]
    if conditional:
        lines += ["", "May be needed"]
        for d in conditional:
            note = f" - {_first_sentences(d.condition, 160)}" if d.condition else ""
            reason = get_document_explanation(_document_type_for_name(d.display_name))
            lines.append(f"- {d.display_name}: {reason}{note}")
    lines += ["", "In simple terms"]
    if destination == "USA":
        lines.append(f"- {NO_USA_SPECIFIC_RULE}")
    lines.append(
        "- This is a preparation checklist, not customs clearance or legal advice."
    )
    return "\n".join(lines)


def _document_type_for_name(display_name: str) -> str:
    """Map the stable display labels used by the checklist to their purpose."""
    normalized = " ".join((display_name or "").casefold().replace("/", " ").split())
    if "commercial invoice" in normalized:
        return "commercial_invoice"
    if "packing list" in normalized:
        return "packing_list"
    if "form e" in normalized or "export declaration" in normalized:
        return "form_e"
    if "certificate of origin" in normalized or normalized == "coo":
        return "certificate_of_origin"
    return normalized.replace(" ", "_")


def _render_clarification(
    candidates: list[tuple[str, str]],
    *,
    guidance_pairs,  # type: ignore[no-untyped-def]
    destination: str | None,
    corrections: dict[str, str],
    matched_term: str | None,
) -> str:
    """Ask which product was meant, and show what they already share."""
    lines: list[str] = []
    if corrections:
        readable = "; ".join(f"'{typed}' as '{used}'" for typed, used in corrections.items())
        lines.append(f"Interpreting {readable}.")
    names = " or ".join(name for _, name in candidates)
    subject = f"Cotton {matched_term}" if matched_term else "That product"
    lines.append(f"{subject} could mean {names}.")

    shared_required, shared_conditional = guidance_pairs
    if shared_required:
        lines += ["", "Common documents", "", "Required documents"]
        lines += [
            f"- {name}: {get_document_explanation(_document_type_for_name(name))}"
            for name in shared_required
        ]
    if shared_conditional:
        lines += ["", "May be needed"]
        for name, condition in shared_conditional:
            note = f" - {_first_sentences(condition, 160)}" if condition else ""
            reason = get_document_explanation(_document_type_for_name(name))
            lines.append(f"- {name}: {reason}{note}")
    lines += ["", "Please choose:"]
    lines += [f"- {name} - PCT {code}" for code, name in candidates]
    lines += ["", "In simple terms"]
    if destination == "USA":
        lines.append(f"- {NO_USA_SPECIFIC_RULE}")
    lines.append(
        "- Choose the product first; CACE cannot give a pass/fail result yet. "
        "This is a preparation checklist, not customs clearance or legal advice."
    )
    return "\n".join(lines)


def _render_explanation(citations: list[RegulatoryCitationSchema]) -> str:
    """Two to four sentences plus key points, ahead of any source card."""
    lead = _first_sentences(citations[0].accepted_passage, 320) if citations else ""
    lines = ["Answer", lead, "", "Key points"]
    seen: set[str] = set()
    for citation in citations[:4]:
        point = _first_sentences(citation.accepted_passage, 150)
        if point in seen:
            continue
        seen.add(point)
        where = f" ({citation.title}"
        where += f", page {citation.page_number})" if citation.page_number else ")"
        lines.append(f"- {point}{where}")
    lines += ["", "Sources are listed below; expand one to read the accepted passage."]
    return "\n".join(lines)


def _render_form_e_explanation(
    citation: RegulatoryCitationSchema,
) -> tuple[str, str]:
    """Concise product explanation; citations still come from retrieval."""
    explanation = (
        "Form-E / PSW Export Declaration is the export form used to record a "
        "shipment with PSW and customs. CACE compares its exporter and invoice "
        "reference with the Commercial Invoice. A match lets CACE count the form "
        "as present; an unreadable or mismatched form is reported separately. "
        "This does not authenticate the document or grant customs clearance."
    )
    snippet = _first_sentences(citation.accepted_passage, 120)
    return f"{explanation}\n\nIndexed source\n{snippet}", snippet



#: Passages that share a word with a document question but say nothing about it.
#: A Form-E/COO explanation was pulling in cotton-seed and phytosanitary text
#: because both mention "certificate" or "export".
_OFF_TOPIC_FOR_CONCEPTS = re.compile(
    r"cotton\s+seed|seeds?\s+for\s+sowing|cotton\s+waste|linters"
    r"|vegetable\s+ghee|cooking\s+oil|edible\s+oil"
    r"|plant\s+protection|phytosanitar|plant\s+quarantine|nppo"
    r"|wheat|pulses|tobacco|scrap|ball\s+bearing",
    re.IGNORECASE,
)


def _concept_relevant_citations(
    citations: list[RegulatoryCitationSchema], concepts: Sequence[Concept]
) -> list[RegulatoryCitationSchema]:
    """Keep only citations that could plausibly support these concepts.

    Word overlap alone put agricultural-cotton passages under a question about
    Form-E. Anything matching an off-topic subject is dropped unless the reader
    actually asked about that subject.
    """
    asked = " ".join(c.key for c in concepts)
    if re.search(r"phytosanitary|raw_material", asked):
        return citations
    kept = [
        c for c in citations if not _OFF_TOPIC_FOR_CONCEPTS.search(c.accepted_passage or "")
    ]
    return kept or citations


#: How many words a generated answer may run to before it is treated as a
#: validation failure rather than trimmed - trimming an LLM answer risks
#: cutting a sentence half-way through a claim.
_MAX_GROQ_ANSWER_WORDS = 220
_MIN_GROQ_ANSWER_WORDS = 8


def _groq_answer_is_acceptable(answer: str) -> bool:
    """The same checks every other visible answer in this module passes.

    Independent of whatever the system prompt asked for: the prompt is
    instructions to a model that can be wrong, this is the actual gate.
    """
    if not answer or not answer.strip():
        return False
    word_count = len(answer.split())
    if not (_MIN_GROQ_ANSWER_WORDS <= word_count <= _MAX_GROQ_ANSWER_WORDS):
        return False
    if contains_internal_tokens(answer):
        return False
    if not validate_answer(answer, has_accepted_evidence=True).ok:
        return False
    return True


def _groq_explanation_or_none(
    question: str, citations: list[RegulatoryCitationSchema]
) -> str | None:
    """Try a grounded, Groq-generated explanation; None on any failure.

    Every failure mode falls back to the deterministic composer rather than
    surfacing to the caller: no Groq credential configured, the provider
    rejecting/rate-limiting/erroring, and a generated answer that fails the
    same validation every other answer in this module has to pass. Nothing
    here retries - one user question makes at most one Groq call.
    """
    passages = [c.accepted_passage for c in citations if c.accepted_passage]
    if not passages:
        return None
    try:
        raw = generate_grounded_explanation(
            question=question, evidence_passages=passages[:5]
        )
    except StructuredExtractionProviderError:
        return None
    except Exception:  # noqa: BLE001 - any provider/transport failure falls back
        return None
    safe = sanitize_for_display(raw)
    if not _groq_answer_is_acceptable(safe):
        return None
    return safe


def _render_plain_explanation(
    question: str,
    citations: list[RegulatoryCitationSchema],
    *,
    destination: str | None = None,
) -> tuple[str, list[str], list[RegulatoryCitationSchema]]:
    """Answer first, in plain words, with citations kept below as support.

    Returns the answer, the quoted snippets it contains (none - the answer is
    either a generated paraphrase or a template, never a raw quotation), and
    the citations to display.

    Order of preference:

    1. A Groq-generated explanation grounded in the retrieved, evidence-gated
       passages - only those passages, per the system prompt, and only kept if
       it passes the same forbidden-claim and internal-token checks every
       other answer here passes.
    2. A written template, when the question names one of the concepts CACE
       has one for. Needs no network call, so this is what runs when Groq is
       not configured, is unavailable, or produced something that didn't pass.
    3. A sanitized one-line lead into the sources themselves, when neither of
       the above has anything to work with.
    """
    concepts = detect_concepts(question)
    relevant_citations = (
        _concept_relevant_citations(citations, concepts)[:3] if concepts else citations[:3]
    )

    groq_answer = _groq_explanation_or_none(question, relevant_citations or citations)
    if groq_answer:
        if destination:
            groq_answer = f"{groq_answer}\n\n{NO_DESTINATION_RULE_NOTE}"
        return groq_answer, [], relevant_citations or citations[:3]

    if concepts:
        answer = explain_concepts(question, concepts)
        if destination:
            answer = f"{answer}\n\n{NO_DESTINATION_RULE_NOTE}"
        return answer, [], relevant_citations

    # No known concept and no usable Groq answer: summarise, but never publish
    # an internal token and never lead with retrieval language.
    if not citations:
        return "", [], []
    lead = sanitize_for_display(_first_sentences(citations[0].accepted_passage, 320))
    lines = [lead] if lead else []
    lines.append(
        "The sources below are the closest material CACE has indexed for this "
        "question. Expand one to read the passage it came from."
    )
    return "\n\n".join(lines), [], citations[:3]


#: Documents an explanation may name. Anything else means the model invented
#: a requirement, which is the one failure the checklist path cannot tolerate.
_DOCUMENT_MENTION = re.compile(
    r"\b(bill of entry|bill of lading|letter of credit|import permit|"
    r"export licence|export license|insurance certificate|inspection "
    r"certificate|phytosanitary|fumigation|health certificate|gsp form|"
    r"form a\b|atr\b|eur1|movement certificate)\b",
    re.IGNORECASE,
)


def _checklist_explanation_is_acceptable(
    answer: str, allowed_documents: list[str]
) -> bool:
    """The checklist's own gate, on top of the shared answer checks.

    The document list is deterministic and drives what an exporter goes and
    obtains. An explanation that names a document the rules did not require -
    or that declares the list complete - would contradict the very thing it is
    explaining, so it is rejected and the written template is used instead.
    """
    if not _groq_answer_is_acceptable(answer):
        return False
    allowed = " ".join(allowed_documents).casefold()
    for match in _DOCUMENT_MENTION.finditer(answer):
        if match.group(0).casefold() not in allowed:
            return False
    if re.search(
        r"\b(only|all（?)?\s+(the\s+)?documents?\s+(you\s+)?(need|required)\b"
        r"|\bthese are the only\b|\bnothing else is (needed|required)\b"
        r"|\bcomplete list\b|\bexhaustive\b",
        answer,
        re.IGNORECASE,
    ):
        return False
    return True


def _groq_checklist_explanation_or_none(
    *,
    question: str,
    guidance,  # type: ignore[no-untyped-def]
    required: list[ChecklistDocumentSchema],
    conditional: list[ChecklistDocumentSchema],
    citations: list[RegulatoryCitationSchema],
) -> str | None:
    """Plain-language prose for a deterministic checklist; None on any failure."""
    try:
        raw = generate_checklist_explanation(
            question=question,
            product=guidance.product,
            pct_code=guidance.pct_code,
            required_documents=[d.display_name for d in required],
            conditional_documents=[d.display_name for d in conditional],
            evidence_passages=[
                c.accepted_passage for c in citations[:4] if c.accepted_passage
            ],
        )
    except StructuredExtractionProviderError:
        return None
    except Exception:  # noqa: BLE001 - any provider/transport failure falls back
        return None
    safe = sanitize_for_display(raw)
    allowed = [d.display_name for d in (*required, *conditional)]
    if not safe or not _checklist_explanation_is_acceptable(safe, allowed):
        return None
    return safe


def _render_evidence_lookup(citations: list[RegulatoryCitationSchema]) -> str:
    """One short answer with the exact source and page."""
    if not citations:
        return NO_EVIDENCE_MESSAGE
    top = citations[0]
    where = f"{top.title}"
    if top.page_number is not None:
        where += f", page {top.page_number}"
    elif top.section:
        where += f", {top.section}"
    lines = [
        f"{where} ({top.source_kind_label}) is the closest supporting source in "
        "the indexed corpus.",
        "",
        f'"{_first_sentences(top.accepted_passage, 300)}"',
    ]
    if len(citations) > 1:
        others = ", ".join(
            f"{c.title}{f' p.{c.page_number}' if c.page_number else ''}"
            for c in citations[1:3]
        )
        lines += ["", f"Also relevant: {others}."]
    return "\n".join(lines)


def _persist(
    db: Session,
    *,
    conversation_id: UUID,
    existing: AssistantConversation | None,
    question: str,
    answer: str,
    intent: str,
    citations: list[RegulatoryCitationSchema],
) -> None:
    if existing is None:
        db.add(
            AssistantConversation(
                id=conversation_id,
                shipment_id=None,
                mode="regulatory_assistant",
            )
        )
    db.add(
        AssistantMessage(conversation_id=conversation_id, role="user", text=question)
    )
    db.add(
        AssistantMessage(
            conversation_id=conversation_id,
            role="assistant",
            text=answer,
            answer_type=intent,
            sources=[c.model_dump(mode="json") for c in citations],
        )
    )
    db.commit()


def _retrieve(
    db: Session,
    *,
    query: str,
    pct_code: str | None,
    destination: str | None,
    source_document: str | None,
    top_k: int,
) -> list[ScoredEvidence]:
    """Search the full active corpus through the existing hybrid retriever.

    ``pct_code`` is forwarded only when the caller supplied one or the question
    named one. A general question is never silently narrowed to the five
    supported codes - that conflation is what made the corpus unreachable.
    """
    # A source filter has no retriever-side equivalent, so widen the candidate
    # set and filter afterwards rather than adding a parallel search path.
    fetch_k = top_k * 4 if source_document else top_k
    output = search_regulatory_evidence(
        db,
        query=query,
        pct_code=pct_code,
        destination_country=destination,
        top_k=min(fetch_k, 20),
        verified_only=True,
    )
    results = output.results
    if source_document:
        needle = source_document.casefold()
        results = [
            item for item in results if needle in item.chunk.source_document.casefold()
        ]
    return results[:top_k]


def answer_regulatory_question(
    db: Session,
    *,
    question: str,
    conversation_id: UUID | None = None,
    pct_code: str | None = None,
    destination: str | None = None,
    source_document: str | None = None,
    top_k: int = 5,
    shipment_selected: bool = False,
) -> RegulatoryChatResponse:
    conv_id = conversation_id or uuid4()
    existing = db.get(AssistantConversation, conv_id) if conversation_id else None
    scope_labels = supported_compliance_scope_labels()
    # An explicit filter wins; otherwise the destination is read from the
    # question against a country vocabulary. Both go through the same
    # canonicaliser so 'usa', 'U.S.A.' and 'United States' agree.
    destination_used = canonical_destination(destination) or destination

    def respond(
        *,
        answer: str,
        intent: str,
        evidence_status: str,
        citations: list[RegulatoryCitationSchema],
        limitations: list[str],
        informational_only: bool = True,
        evidence_scope: str = NO_PCT_SCOPE,
        answer_mode: str = "explanation",
        required_documents: list[ChecklistDocumentSchema] | None = None,
        conditional_documents: list[ChecklistDocumentSchema] | None = None,
        product_candidates: list[ProductCandidateSchema] | None = None,
        interpreted_as: dict[str, str] | None = None,
        resolved_product: str | None = None,
        resolved_pct_code: str | None = None,
    ) -> RegulatoryChatResponse:
        # The single display boundary. Every visible string leaves through here,
        # so a configuration token cannot reach a reader from any branch - this
        # path, a future one, or a corpus passage that happens to contain one.
        answer = sanitize_for_display(answer) or answer
        limitations = [sanitize_for_display(item) or item for item in limitations]
        _persist(
            db,
            conversation_id=conv_id,
            existing=existing,
            question=question,
            answer=answer,
            intent=intent,
            citations=citations,
        )
        return RegulatoryChatResponse(
            conversation_id=conv_id,
            message_id=uuid4(),
            answer=answer,
            intent=intent,
            evidence_status=evidence_status,
            evidence_scope=evidence_scope,
            answer_mode=answer_mode,
            required_documents=required_documents or [],
            conditional_documents=conditional_documents or [],
            product_candidates=product_candidates or [],
            interpreted_as=interpreted_as or {},
            resolved_product=resolved_product,
            resolved_pct_code=resolved_pct_code,
            destination=destination_used,
            sources=citations,
            limitations=limitations,
            supported_compliance_scope=scope_labels,
            informational_only=informational_only,
            suggested_questions=list(SUGGESTED_QUESTIONS),
        )

    # --- Layer 1: domain guard -------------------------------------------
    decision: IntentDecision = classify_regulatory_intent(
        question, pct_filter=pct_code, shipment_selected=shipment_selected
    )
    if decision.intent == "out_of_scope":
        return respond(
            answer=OFF_TOPIC_MESSAGE,
            intent="out_of_scope",
            evidence_status="not_applicable",
            citations=[],
            limitations=[GENERAL_LIMITATION],
            answer_mode="refusal",
        )

    # Import questions are normally within the broader customs domain, but
    # this prototype has no validated vehicle-import procedure. Refuse before
    # retrieval so unrelated textile/export passages cannot leak into the UI.
    if _VEHICLE_IMPORT_REQUEST.search(question):
        return respond(
            answer=VEHICLE_IMPORT_SCOPE_MESSAGE,
            intent="out_of_scope",
            evidence_status="not_applicable",
            citations=[],
            limitations=[GENERAL_LIMITATION],
            answer_mode="refusal",
        )

    if decision.requires_shipment_context:
        # A regulatory conversation must not quietly become a shipment
        # conversation; the caller has to select the shipment explicitly. But
        # "why does this shipment need human review?" is two questions, and the
        # general half can be answered here rather than refused wholesale.
        general = explain_concepts(question, detect_concepts(question))
        return respond(
            answer=(
                f"{general}\n\n{SHIPMENT_CONTEXT_REQUIRED_MESSAGE}"
                if general
                else SHIPMENT_CONTEXT_REQUIRED_MESSAGE
            ),
            intent=decision.intent,
            evidence_status="not_applicable",
            citations=[],
            limitations=[GENERAL_LIMITATION],
            answer_mode="refusal",
        )

    effective_pct = decision.pct_code
    supported = bool(effective_pct and effective_pct in SUPPORTED_PCT_PRODUCTS)

    # A checklist question about a supported code is a deterministic-compliance
    # question, so it is answered by the compliance guidance service rather
    # than by summarising passages. This is the one path where the answer is
    # not merely informational.
    if decision.destination and not destination_used:
        destination_used = decision.destination

    corrections = dict(decision.product.corrections) if decision.product else {}

    # A product the user named ambiguously: ask, and meanwhile show what the
    # candidates already agree on, rather than dumping passages.
    if decision.intent == "product_clarification_required":
        product = decision.product
        candidates = [
            (code, SUPPORTED_PCT_PRODUCTS[code])
            for code in (product.candidates if product else ())
            if code in SUPPORTED_PCT_PRODUCTS
        ]
        if not candidates:
            return respond(
                answer=(
                    "I could not tell which supported product you mean. Name the "
                    "product more precisely, or give its eight-digit PCT code."
                ),
                intent=decision.intent,
                evidence_status="not_applicable",
                citations=[],
                limitations=[GENERAL_LIMITATION],
                answer_mode="clarification",
                interpreted_as=corrections,
            )
        # With no destination, do not activate China's configured COO rule as
        # if the user had named China. Use a neutral supported destination to
        # preserve the COO as conditional until the destination is known.
        country = destination_used or "USA"
        shared_required: list[str] | None = None
        shared_conditional: list[tuple[str, str | None]] | None = None
        for code, name in candidates:
            guidance = generate_pre_submission_guidance(
                db, product=name, pct_code=code, destination=country
            )
            required, conditional = _checklist_documents(guidance)
            req_names = [d.display_name for d in required]
            cond_pairs = [(d.display_name, d.condition) for d in conditional]
            if shared_required is None:
                shared_required, shared_conditional = req_names, cond_pairs
            else:
                shared_required = [n for n in shared_required if n in req_names]
                shared_conditional = [
                    pair for pair in (shared_conditional or []) if pair[0] in
                    {n for n, _ in cond_pairs}
                ]
        return respond(
            answer=_render_clarification(
                candidates,
                guidance_pairs=(shared_required or [], shared_conditional or []),
                destination=destination_used,
                corrections=corrections,
                matched_term=decision.product.matched_term if decision.product else None,
            ),
            intent=decision.intent,
            evidence_status="not_applicable",
            citations=[],
            limitations=[GENERAL_LIMITATION],
            answer_mode="clarification",
            required_documents=[
                ChecklistDocumentSchema(display_name=n, requirement="required")
                for n in (shared_required or [])
            ],
            conditional_documents=[
                ChecklistDocumentSchema(
                    display_name=n, requirement="conditional", condition=c
                )
                for n, c in (shared_conditional or [])
            ],
            product_candidates=[
                ProductCandidateSchema(pct_code=code, product_name=name)
                for code, name in candidates
            ],
            interpreted_as=corrections,
        )

    # A checklist question about a supported code is a deterministic-compliance
    # question, so it is answered by the compliance guidance service rather
    # than by summarising passages. This is the one path where the answer is
    # not merely informational.
    if decision.intent == "supported_pct_guidance" and effective_pct:
        country = destination_used or "China"
        guidance = generate_pre_submission_guidance(
            db,
            product=SUPPORTED_PCT_PRODUCTS[effective_pct],
            pct_code=effective_pct,
            destination=country,
        )
        required, conditional = _checklist_documents(guidance)
        # Retrieve, then explain. This path used to answer entirely from a
        # written template with no retrieval and no model call at all, so the
        # most common question in the product - "what do I need to export X" -
        # never reached the corpus. The checklist stays deterministic; the
        # passages and the model only supply the words around it.
        checklist_evidence, _scope = _staged_retrieve(
            db,
            question=question,
            pct_code=effective_pct,
            supported=True,
            destination=destination,
            source_document=source_document,
            top_k=top_k,
        )
        checklist_citations = [
            _to_citation(item, get_corpus_snapshot_date(db))
            for item in checklist_evidence
        ][:3]
        prose = _groq_checklist_explanation_or_none(
            question=question,
            guidance=guidance,
            required=required,
            conditional=conditional,
            citations=checklist_citations,
        )
        checklist_body = _render_checklist(
            guidance, destination=destination_used, corrections=corrections
        )
        return respond(
            answer=(
                f"{prose}\n\n{checklist_body}" if prose else checklist_body
            ),
            intent="supported_pct_guidance",
            evidence_status="accepted",
            citations=checklist_citations,
            limitations=[GENERAL_LIMITATION],
            informational_only=False,
            evidence_scope=EXACT_PCT,
            answer_mode="checklist",
            required_documents=required,
            conditional_documents=conditional,
            interpreted_as=corrections,
            resolved_product=guidance.product,
            resolved_pct_code=effective_pct,
        )

    evidence, evidence_scope = _staged_retrieve(
        db,
        question=question,
        pct_code=effective_pct,
        supported=supported,
        destination=destination,
        source_document=source_document,
        top_k=top_k,
    )

    # Generic word overlap on "cotton" is not enough for product guidance: the
    # failing query accepted cotton-seed, cotton-waste and vegetable-ghee
    # passages. When the question resolved to a product, passages about
    # agricultural cotton are dropped unless the product itself is agricultural.
    if decision.product is not None or effective_pct:
        category = None
        if effective_pct:
            from app.services.compliance.pct_catalog import load_pct_catalog

            category = next(
                (p.textile_category for p in load_pct_catalog() if p.pct_code == effective_pct),
                None,
            )
        filtered = [
            item for item in evidence if _is_product_relevant(item.chunk.text, category)
        ]
        if filtered:
            evidence = filtered

    limitations = [GENERAL_LIMITATION]
    if not supported:
        limitations.append(INFORMATIONAL_LIMITATION)
    if effective_pct and not supported:
        limitations.append(UNSUPPORTED_PCT_NOTICE)

    # --- Layer 3: evidence gate -------------------------------------------
    # A question naming a specific code is an unsupported-PCT information
    # request and keeps its own honest reporting; it must not be answered with
    # the generic "what is a PCT code" template just because "PCT" appears.
    if not evidence and decision.answer_mode == "explanation" and not effective_pct:
        # A concept explanation is written prose, not a summary of passages, so
        # retrieval finding nothing is no reason to withhold it. "What is a
        # packing list?" has a real answer whether or not the corpus happens to
        # contain a matching chunk; sources are supporting material, not the
        # answer itself.
        concepts = detect_concepts(question)
        if concepts:
            return respond(
                answer=explain_concepts(question, concepts),
                intent=decision.intent,
                evidence_status="not_applicable",
                citations=[],
                limitations=limitations,
                evidence_scope=NO_PCT_SCOPE,
                answer_mode="explanation",
            )

    if not evidence:
        # The unsupported-PCT notice opens with "I found relevant information",
        # so it is only truthful when something was actually retrieved. With no
        # evidence the scope limit is still reported, but in the limitations
        # rather than as a claim inside the answer.
        answer = NO_EVIDENCE_MESSAGE
        if effective_pct and not supported:
            answer = (
                f"{NO_EVIDENCE_MESSAGE}\n\nSeparately, PCT {effective_pct} is outside "
                "CACE's deterministic compliance scope, so no compliance decision "
                "can be issued for it either way."
            )
        return respond(
            answer=answer,
            intent=decision.intent,
            evidence_status="evidence_not_found",
            citations=[],
            limitations=limitations,
            evidence_scope=NO_EVIDENCE,
            answer_mode=decision.answer_mode,
        )

    snapshot_date = get_corpus_snapshot_date(db)
    citations = [_to_citation(item, snapshot_date) for item in evidence]

    # Mode decides presentation. Every informational intent used to share the
    # document-search rendering, which is why a checklist question came back as
    # five raw quotations led by "These indexed sources contain passages
    # matching your search".
    mode = decision.answer_mode
    if mode == "evidence_lookup":
        body = _render_evidence_lookup(citations)
        snippets = [_first_sentences(c.accepted_passage, 300) for c in citations[:1]]
        citations = citations[:3]
    elif mode == "document_search":
        body, snippets = _compose_grounded_answer(citations, decision.intent)
    else:
        # Plain language first. The answer is written prose from a template when
        # the question names a concept CACE knows, so an exporter reads an
        # answer rather than a quotation that happened to rank highly.
        body, snippets, citations = _render_plain_explanation(
            question, citations, destination=None
        )

    framing = body
    # Wording is driven by what was actually retrieved, not merely by whether
    # the code is supported. "I found relevant information" is only true when
    # stage 1 matched the code itself; otherwise the broader-category notice
    # says plainly that the passages are not about this code.
    if evidence_scope == BROADER_CATEGORY and effective_pct:
        framing = f"{broader_category_notice(effective_pct)}\n\n{framing}"
        if not supported:
            framing = f"{framing}\n\n{UNSUPPORTED_PCT_NOTICE}"
    elif effective_pct and not supported:
        framing = f"{UNSUPPORTED_PCT_NOTICE}\n\n{framing}"
    elif decision.compliance_decision_requested and supported:
        framing = (
            f"PCT {effective_pct} is inside CACE's deterministic compliance scope. "
            "Use Prepare an Export for the document checklist and rule-by-rule "
            "outcome; the passages below are the supporting regulatory "
            f"evidence.\n\n{framing}"
        )
    body = framing

    # --- Layer 4: answer validation ---------------------------------------
    draft = [
        DraftCitation(
            source_document=c.title,
            sro_number=c.sro_number,
            page_number=c.page_number,
            source_url=c.source_url,
            validation_status=c.evidence_status,
            evidence_text=c.accepted_passage,
        )
        for c in citations
    ]
    # ``answer=None``: the SRO-mention rule in validate_rag_output is written
    # for generated prose. Here the only prose is CACE's own framing, and the
    # rest is quotation - a regulation citing another SRO inside a quoted
    # passage is evidence, not an unsupported claim. Correspondence of the
    # quotes is checked directly below instead.
    citation_check = validate_rag_output(
        retrieved=evidence, answer=None, draft_citations=draft
    )
    accepted_texts = [item.chunk.text for item in evidence]
    snippets_grounded = all(
        any(_is_quoted_from(snippet, text) for text in accepted_texts)
        for snippet in snippets
    )
    # Forbidden-claim checking applies to CACE's own words only.
    claim_text = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith('"')
    )
    claim_check = validate_answer(claim_text, has_accepted_evidence=True)
    if not citation_check.ok or not claim_check.ok or not snippets_grounded:
        reasons = []
        if not citation_check.ok:
            reasons.append(citation_check.reason)
        if not snippets_grounded:
            reasons.append("a quoted passage did not match the accepted evidence")
        reasons.extend(claim_check.violations)
        return respond(
            answer=(
                "I retrieved passages for this question but could not produce an "
                "answer that stayed within what the evidence supports, so I am "
                f"not answering it. Reason: {'; '.join(reasons)}."
            ),
            intent=decision.intent,
            evidence_status="evidence_not_found",
            citations=[],
            limitations=limitations,
            evidence_scope=NO_EVIDENCE,
            answer_mode=decision.answer_mode,
        )

    if any(contains_injection(c.accepted_passage) for c in citations):
        limitations.append(
            "One or more retrieved passages contain text that looks like an "
            "instruction. It is displayed as document content only and was not "
            "acted on."
        )

    return respond(
        answer=body,
        intent=decision.intent,
        evidence_status="accepted",
        citations=citations,
        limitations=limitations,
        informational_only=True,
        evidence_scope=evidence_scope,
        answer_mode=decision.answer_mode,
    )
