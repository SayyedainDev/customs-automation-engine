"""The textile compliance checklist as a tool the pipeline can call.

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 13.5: the deterministic
compliance engine (``services/compliance``, reached here through
``services.assistant.guidance.generate_pre_submission_guidance``) is
untouched and stays the sole authority on document requirements. This module
never decides a requirement itself - it only (a) recognises when a question
is a checklist ask about one of the five validated textile products, (b)
calls the existing engine, and (c) asks Groq's existing, already-tested
``generate_checklist_explanation`` to put the *fixed* result into plain
words. The checklist a caller renders is the engine's own
``ChecklistDocument`` list, never anything derived from the model's prose.

Deliberately independent of ``services/assistant/regulatory_intent.py`` and
``domain_guard.py``: reusing either would re-couple the new general-customs
pipeline to the old five-PCT-code-only domain check this package replaces.
``resolve_product`` has no such coupling - it is a pure product-name-to-code
resolver - so it is reused directly.
"""

from __future__ import annotations

import re

from groq import Groq
from sqlalchemy.orm import Session

from app.services.assistant.foundation import SUPPORTED_PCT_PRODUCTS
from app.services.assistant.guidance import generate_pre_submission_guidance
from app.services.assistant.product_resolver import resolve_product
from app.services.customs_chat.contracts import ChecklistDocument, ChecklistTool, Citation
from app.services.customs_chat.telemetry import log_stage
from app.services.structured_extraction_service import generate_checklist_explanation

#: A checklist ask, not a definitional one ("what is cotton yarn" should not
#: trigger this - "what documents do I need for cotton yarn" should).
_CHECKLIST_PHRASING = re.compile(
    r"\b(documents?|documentation|paperwork|checklist)\b"
    r"|\bwhat\s+(do|would|will)\s+i\s+need\b"
    r"|\bneed\s+to\s+(export|import|ship)\b"
    r"|\brequired?\s+(to|for)\s+(export|import|ship)\b"
    r"|\bprepare\s+(for|to)\s+(export|import|ship)\b",
    re.IGNORECASE,
)

_DEFAULT_DESTINATION = "USA"


def _split_documents(guidance) -> tuple[tuple[ChecklistDocument, ...], tuple[ChecklistDocument, ...]]:  # type: ignore[no-untyped-def]
    required: list[ChecklistDocument] = []
    conditional: list[ChecklistDocument] = []
    for doc in guidance.documents:
        item = ChecklistDocument(
            display_name=doc.display_name,
            requirement=doc.requirement,
            condition=(doc.summary or None) if doc.requirement == "conditional" else None,
        )
        (required if doc.requirement == "required" else conditional).append(item)
    return tuple(required), tuple(conditional)


def _extract_citations(guidance) -> tuple[Citation, ...]:  # type: ignore[no-untyped-def]
    seen: set[tuple[str, str | None]] = set()
    official: list[Citation] = []
    curated: list[Citation] = []
    for doc in guidance.documents:
        for src in getattr(doc, "citations", None) or []:
            doc_title = src.source_document or src.display_name or "Regulatory source"
            ref = (
                f"page {src.page_number}"
                if src.page_number is not None
                else getattr(src, "section", None)
            )
            key = (doc_title, ref)
            if key in seen:
                continue
            seen.add(key)
            is_off = bool(getattr(src, "is_official", False))
            citation = Citation(
                document=doc_title,
                reference=ref,
                source_kind=str(getattr(src, "source_type", None) or src.source_kind),
                kind_label=str(getattr(src, "source_kind_label", None) or "Regulatory source"),
                is_official=is_off,
                currency_status="current",
                excerpt=src.snippet or "",
                url=getattr(src, "source_url", None)
                or getattr(src, "referenced_official_source", None),
            )
            if is_off:
                official.append(citation)
            else:
                curated.append(citation)
    return tuple([*official, *curated])


def maybe_run_checklist_tool(
    db: Session,
    *,
    standalone_question: str,
    destination: str | None,
    client: Groq | None = None,
) -> tuple[ChecklistTool, str | None, tuple[Citation, ...]] | None:
    """Returns ``(tool_result, explanation_or_none, citations)`` when the question is a
    checklist ask about a single, unambiguous, supported textile product -
    ``None`` otherwise, so the caller falls through to the general pipeline
    (which still covers an ambiguous or unsupported product informationally).

    ``explanation`` is ``None`` when Groq could not be reached; the caller is
    expected to show the checklist itself (always present, always correct,
    never touched by this) with a short fixed lead-in instead of a template
    standing in for real prose - the same "never fabricate a stand-in
    explanation" rule the rest of generation follows.
    """
    if not _CHECKLIST_PHRASING.search(standalone_question or ""):
        return None

    resolution = resolve_product(standalone_question)
    if resolution is None or not resolution.is_resolved or resolution.pct_code is None:
        return None

    pct_code = resolution.pct_code
    product = SUPPORTED_PCT_PRODUCTS.get(pct_code)
    if not product:
        return None

    country = destination or _DEFAULT_DESTINATION
    guidance = generate_pre_submission_guidance(
        db, product=product, pct_code=pct_code, destination=country
    )
    required, conditional = _split_documents(guidance)
    citations = _extract_citations(guidance)
    tool_result = ChecklistTool(
        pct_code=pct_code,
        product=product,
        destination=country,
        required_documents=required,
        conditional_documents=conditional,
    )

    explanation: str | None = None
    try:
        explanation = generate_checklist_explanation(
            question=standalone_question,
            product=product,
            pct_code=pct_code,
            required_documents=[d.display_name for d in required],
            conditional_documents=[d.display_name for d in conditional],
            evidence_passages=[c.excerpt for c in citations if c.excerpt][:3],
            client=client,
        ).strip()
    except Exception as exc:  # noqa: BLE001 - a missing explanation still ships the checklist
        log_stage("checklist_tool", error=str(exc)[:200])
        explanation = None

    log_stage("checklist_tool", pct_code=pct_code, required=len(required), conditional=len(conditional))
    return tool_result, explanation, citations
