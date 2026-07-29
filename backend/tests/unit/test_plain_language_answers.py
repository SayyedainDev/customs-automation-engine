"""Ask CACE must answer in words an exporter can act on.

Live defect: asked "why is form e and COO is required", Ask CACE replied

    Certificate required: value=conditional; TDAP certificate of origin or
    REX-related evidence when required by the destination market or
    preferential scheme. Approval required: value=False;...

and then repeated the same fragment under "Key points". Technically sourced,
unusable. The leak is not a stray ``str(obj)``: ``sources.py::_render_product``
writes ``value=False`` into the corpus text at ingestion, so quoting a passage
verbatim published an internal serialization.

These tests pin the two guarantees that follow: nothing internal reaches a
reader from any path, and common concepts get written prose rather than a
nearby quotation. No Groq call is made anywhere here.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.models.customs_audit import CustomsAuditWorkflow
from app.services.assistant.plain_language import (
    CONCEPTS,
    contains_internal_tokens,
    detect_concepts,
    explain_concepts,
    label_for_document,
    phrase_for_evidence,
    phrase_for_requirement_value,
    phrase_for_status,
    sanitize_for_display,
)
from app.services.assistant.regulatory_chat import answer_regulatory_question
from app.services.regulatory.embeddings import (
    FakeEmbeddingProvider,
    reset_embedding_provider,
    set_embedding_provider,
)
from app.services.regulatory.reranker import FakeReranker, reset_reranker, set_reranker
from tests.unit.test_regulatory_retrieval import add_evidence, build_corpus

REPORTED_QUESTION = "why is form e and COO is required"

#: The exact leakage seen in the deployed answer.
LEAKED_ANSWER = (
    "Certificate required: value=conditional; TDAP certificate of origin or "
    "REX-related evidence when required by the destination market or "
    "preferential scheme. Approval required: value=False; "
    "verified_no_permit_required_under_epo_2022_general_permission"
)

#: Tokens that must never be visible, whatever the question.
FORBIDDEN_SUBSTRINGS = (
    "value=False",
    "value=True",
    "value=conditional",
    "verified_no_permit_required_under_epo_2022_general_permission",
    "verified_no_licence_required_under_epo_2022_general_permission",
    "certificate_required",
    "source_kind",
    "evidence_status",
    "extraction_method",
    "validation_status",
    "form_e",
    "certificate_of_origin",
    "manual_review",
    "not_applicable",
    "pct_codes",
    "schema_name",
)

QUESTIONS = [
    "Why is Form-E required?",
    "What is Form-E?",
    "Why is a Certificate of Origin required?",
    REPORTED_QUESTION,
    "Is a Certificate of Origin always required?",
    "What is a commercial invoice?",
    "What is a packing list?",
    "What is a PCT code?",
    "Why does this shipment need human review?",
    'What does "required before submission" mean?',
    "Documents for cotton towels to China",
    "Documents for cotton pants to USA",
    "Which rule says Form-E is required?",
    "Find passages mentioning Certificate of Origin",
    "How to import a car from China?",
]


@pytest.fixture(autouse=True)
def _offline_models():
    set_embedding_provider(FakeEmbeddingProvider(dimension=16))
    set_reranker(FakeReranker())
    yield
    reset_embedding_provider()
    reset_reranker()


def ask(db: Session, question: str):
    return answer_regulatory_question(db, question=question)


# --------------------------------------------------------------------------- #
# 1-5, 14: nothing internal is ever displayed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("question", QUESTIONS)
def test_no_internal_token_reaches_the_user(
    isolated_database: Engine, question: str
) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, question)
    visible = "\n".join([response.answer, *response.limitations])
    for token in FORBIDDEN_SUBSTRINGS:
        assert token not in visible, f"{token!r} leaked for {question!r}"
    assert not contains_internal_tokens(response.answer)
    # No JSON or dict serialization either.
    assert not re.search(r"\{['\"]|\}\s*$|\bdict\(", response.answer)


def test_the_exact_reported_leakage_is_rewritten() -> None:
    safe = sanitize_for_display(LEAKED_ANSWER)
    for token in ("value=conditional", "value=False",
                  "verified_no_permit_required_under_epo_2022_general_permission"):
        assert token not in safe
    assert "Required only when the stated condition applies" in safe
    assert "Not required under the current matched rule" in safe
    assert "No separate export permit" in safe


def test_malformed_metadata_cannot_leak_through_fallback_formatting() -> None:
    """Unknown identifiers become words, never keys, and structures are dropped."""
    for raw in (
        "status: some_unknown_internal_flag",
        "{'rule_id': 'xr_common_form_e', 'value': False}",
        "['certificate_required', 'form_e']",
        "value=null",
        "check_id=xr_coo_china",
    ):
        safe = sanitize_for_display(raw)
        assert not contains_internal_tokens(safe), f"leak from {raw!r} -> {safe!r}"


def test_display_mappers_return_plain_language() -> None:
    assert label_for_document("form_e") == "Form-E / PSW Export Declaration"
    assert label_for_document("certificate_of_origin") == "Certificate of Origin"
    assert phrase_for_status("manual_review") == "Needs human confirmation"
    assert phrase_for_status("not_applicable") == "Does not apply to this case"
    assert phrase_for_requirement_value(False) == (
        "Not required under the current matched rule."
    )
    assert phrase_for_requirement_value(True) == "Required."
    assert phrase_for_requirement_value("conditional") == (
        "Required only when the stated condition applies."
    )
    assert "Not found" in phrase_for_evidence("evidence_unavailable")
    # No mapper output may itself contain an internal token.
    for concept in CONCEPTS:
        for field in (concept.title, concept.what_it_is, concept.why_it_matters,
                      concept.requirement, concept.example or ""):
            assert not contains_internal_tokens(field)


# --------------------------------------------------------------------------- #
# 6-8: the answers themselves
# --------------------------------------------------------------------------- #
def test_form_e_explanation_is_short_and_direct(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "Why is Form-E required?")
    words = len(response.answer.split())
    assert 40 <= words <= 160, f"{words} words"
    assert response.answer.startswith("Form-E")
    # Never opens with retrieval language.
    for opener in ("These indexed sources", "The corpus", "The retrieved evidence",
                   "According to the structured rule", "Answer\n"):
        assert not response.answer.startswith(opener)


def test_certificate_of_origin_explains_conditionality(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "Why is a Certificate of Origin required?")
    lowered = response.answer.casefold()
    assert "conditional" in lowered
    assert "destination" in lowered
    # Must not overstate.
    assert "required for every export" not in lowered
    assert "always required" not in lowered


def test_always_question_is_answered_no_first(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "Is a Certificate of Origin always required?")
    assert response.answer.lstrip().startswith("No.")
    assert "destination country" in response.answer.casefold()


def test_combined_question_explains_both_documents_separately(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, REPORTED_QUESTION)
    answer = response.answer
    assert 60 <= len(answer.split()) <= 160, f"{len(answer.split())} words"
    # Both explained, in their own words.
    assert "Form-E" in answer and "Certificate of Origin" in answer
    assert "Pakistan Single Window" in answer
    assert "where the goods were produced" in answer
    # Required vs conditional distinguished for each.
    assert "Normally required for export submission" in answer
    assert "Conditional" in answer
    # One scope note, and no clearance promise.
    assert answer.count("does not issue customs clearance") == 1
    assert "will clear customs" not in answer.casefold()


def test_answer_comes_before_sources_and_quotes_nothing(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, REPORTED_QUESTION)
    # Explanation mode shows no raw passage inside the answer.
    assert '"' not in response.answer
    for source in response.sources:
        excerpt = " ".join((source.accepted_passage or "").split())[:40]
        assert excerpt not in response.answer
    assert len(response.sources) <= 3


def test_no_duplicated_key_points_section(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, REPORTED_QUESTION)
    assert "Key points" not in response.answer
    paragraphs = [p.strip() for p in response.answer.split("\n\n") if p.strip()]
    assert len(paragraphs) == len(set(paragraphs)), "a paragraph was repeated"


# --------------------------------------------------------------------------- #
# 11: irrelevant passages
# --------------------------------------------------------------------------- #
def test_irrelevant_passages_are_not_cited_for_a_document_question(
    isolated_database: Engine,
) -> None:
    noise = (
        "Cotton Seeds for sowing and vegetable ghee exported from export "
        "processing zones require a phytosanitary certificate from the NPPO."
    )
    with Session(isolated_database) as db:
        build_corpus(db)
        add_evidence(
            db,
            key="noise",
            source_document="Export Policy Order, 2022 - SRO 544(I)/2022",
            parent_text=noise,
            pct_codes=[],
            document_type="export_policy_order",
        )
        response = ask(db, REPORTED_QUESTION)
    for source in response.sources:
        assert "Cotton Seeds for sowing" not in (source.accepted_passage or "")
        assert "vegetable ghee" not in (source.accepted_passage or "").casefold()


def test_sources_stay_linked_to_real_passages(isolated_database: Engine) -> None:
    from app.models.regulatory import RegulatoryChunk

    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, REPORTED_QUESTION)
        titles = {
            t for t in db.execute(select(RegulatoryChunk.source_document)).scalars()
        }
    # The template answer does not depend on retrieval, so zero sources is a
    # legitimate outcome for a small corpus. What must hold is that anything
    # shown is a real, linked passage.
    for source in response.sources:
        assert source.title in titles
        assert source.accepted_passage
        assert source.source_kind_label


# --------------------------------------------------------------------------- #
# 13, 17-20: other modes and cost
# --------------------------------------------------------------------------- #
def test_templates_need_no_provider_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """The concept explanations are pure functions - nothing to call."""
    import app.services.structured_extraction_service as extraction

    def _explode(*_: object, **__: object) -> None:  # pragma: no cover
        raise AssertionError("a provider call was attempted for a template answer")

    monkeypatch.setattr(extraction, "_get_groq_client", _explode)
    for question in ("What is Form-E?", "What is a packing list?",
                     REPORTED_QUESTION, "What is a PCT code?"):
        answer = explain_concepts(question, detect_concepts(question))
        assert answer and not contains_internal_tokens(answer)


def test_checklist_meaning_is_unchanged(isolated_database: Engine) -> None:
    """Legal meaning must survive the wording change.

    China is a destination whose configured rule *requires* the certificate of
    origin, so it must appear as required there and as conditional elsewhere.
    Softening that into "conditional" everywhere would be a compliance change
    dressed up as a readability fix.
    """
    with Session(isolated_database) as db:
        build_corpus(db)
        china = ask(db, "What documents do I need for cotton towels to China?")
        germany = ask(db, "What documents do I need for cotton towels to Germany?")
    assert china.answer_mode == "checklist"
    required_china = {d.display_name.casefold() for d in china.required_documents}
    assert {"commercial invoice", "packing list"} <= required_china
    assert "certificate of origin" in required_china
    conditional_germany = {
        d.display_name.casefold() for d in germany.conditional_documents
    }
    assert "certificate of origin" in conditional_germany


def test_evidence_lookup_still_returns_an_exact_source(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "Which rule says Form-E is required?")
    # Routing is the contract here; whether this small corpus happens to hold a
    # matching passage is not.
    assert response.answer_mode == "evidence_lookup"
    for source in response.sources:
        assert source.title
        assert source.page_number is not None or source.section


def test_explicit_document_search_still_shows_passages(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "Find passages mentioning Certificate of Origin")
    assert response.answer_mode == "document_search"
    assert response.sources


def test_out_of_scope_explains_scope_not_a_failed_search(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "How to import a car from China?")
    assert response.intent == "out_of_scope"
    lowered = response.answer.casefold()
    assert "textile" in lowered
    assert "no matching evidence" not in lowered
    assert "could not find sufficiently relevant evidence" not in lowered


def test_no_compliance_state_is_mutated(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        build_corpus(db)
        before = len(list(db.execute(select(CustomsAuditWorkflow)).scalars()))
        response = ask(db, REPORTED_QUESTION)
        after = len(list(db.execute(select(CustomsAuditWorkflow)).scalars()))
    assert before == after == 0
    assert response.informational_only is True


def test_technical_metadata_is_still_available_in_the_payload(
    isolated_database: Engine,
) -> None:
    """Sanitizing display must not blind the API's structured fields."""
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, REPORTED_QUESTION)
    # Machine-readable fields keep their exact values.
    assert response.intent == "general_regulatory_information"
    assert response.answer_mode == "explanation"
    assert response.evidence_status in {
        "accepted", "evidence_not_found", "not_applicable"
    }
    for source in response.sources:
        # Raw values are still present for machines, just not for readers.
        assert source.source_kind
        assert source.evidence_status == "accepted"
