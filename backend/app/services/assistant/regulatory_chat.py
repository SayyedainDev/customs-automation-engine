"""The global regulatory knowledge assistant.

This is the conversational half of CACE that the five-PCT "Prepare an Export"
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
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.assistant import AssistantConversation, AssistantMessage
from app.schemas.assistant import (
    RegulatoryChatResponse,
    RegulatoryCitationSchema,
)
from app.services.assistant.domain_guard import (
    OFF_TOPIC_MESSAGE,
    contains_injection,
    validate_answer,
)
from app.services.assistant.foundation import SUPPORTED_PCT_PRODUCTS
from app.services.assistant.guidance import generate_pre_submission_guidance
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


def _normalize_for_retrieval(question: str, pct_code: str | None) -> str:
    """Rewrite a question into the corpus's own vocabulary before retrieval."""
    text = question or ""
    canonical = [terms for pattern, terms in _CONCEPTS if pattern.search(text)]
    if canonical:
        parts = " ".join(canonical).split()
    else:
        words = re.findall(r"[a-z0-9][a-z0-9\-]*", text.casefold())
        parts = [word for word in words if word not in _QUERY_STOPWORDS] or words
    if pct_code:
        parts.append(pct_code)
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            ordered.append(part)
    return " ".join(ordered)


def _first_sentences(text: str, max_chars: int = 420) -> str:
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
        "This is the deterministic five-PCT checklist. Open Prepare an Export "
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

    def respond(
        *,
        answer: str,
        intent: str,
        evidence_status: str,
        citations: list[RegulatoryCitationSchema],
        limitations: list[str],
        informational_only: bool = True,
        evidence_scope: str = NO_PCT_SCOPE,
    ) -> RegulatoryChatResponse:
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
        )

    if decision.requires_shipment_context:
        # A regulatory conversation must not quietly become a shipment
        # conversation; the caller has to select the shipment explicitly.
        return respond(
            answer=SHIPMENT_CONTEXT_REQUIRED_MESSAGE,
            intent=decision.intent,
            evidence_status="not_applicable",
            citations=[],
            limitations=[GENERAL_LIMITATION],
        )

    effective_pct = decision.pct_code
    supported = bool(effective_pct and effective_pct in SUPPORTED_PCT_PRODUCTS)

    # A checklist question about a supported code is a deterministic-compliance
    # question, so it is answered by the compliance guidance service rather
    # than by summarising passages. This is the one path where the answer is
    # not merely informational.
    if decision.intent == "supported_pct_guidance" and effective_pct:
        country = destination or _extract_destination(question)
        if country:
            return respond(
                answer=_render_guidance_answer(
                    generate_pre_submission_guidance(
                        db,
                        product=SUPPORTED_PCT_PRODUCTS[effective_pct],
                        pct_code=effective_pct,
                        destination=country,
                    )
                ),
                intent="supported_pct_guidance",
                evidence_status="accepted",
                citations=[],
                limitations=[GENERAL_LIMITATION],
                informational_only=False,
                evidence_scope=EXACT_PCT,
            )

    # --- Layer 2: retrieval over the full active corpus -------------------
    evidence, evidence_scope = _staged_retrieve(
        db,
        question=question,
        pct_code=effective_pct,
        supported=supported,
        destination=destination,
        source_document=source_document,
        top_k=top_k,
    )

    limitations = [GENERAL_LIMITATION]
    if not supported:
        limitations.append(INFORMATIONAL_LIMITATION)
    if effective_pct and not supported:
        limitations.append(UNSUPPORTED_PCT_NOTICE)

    # --- Layer 3: evidence gate -------------------------------------------
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
        )

    snapshot_date = get_corpus_snapshot_date(db)
    citations = [_to_citation(item, snapshot_date) for item in evidence]
    body, snippets = _compose_grounded_answer(citations, decision.intent)

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
    )
