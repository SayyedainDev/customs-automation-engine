"""Regression tests for the Ask CACE repair.

Each test pins one defect found by asking the deployed assistant real
questions. The questions are kept verbatim, including the ones a user typed
and got refused, because the wording is the point.
"""

import pytest

from app.services.assistant.domain_guard import validate_answer
from app.services.assistant.product_resolver import resolve_product
from app.services.assistant.regulatory_chat import _normalize_for_retrieval
from app.services.assistant.regulatory_intent import classify_regulatory_intent


# --------------------------------------------------------------------------- #
# Retrieval kept the question's own words
# --------------------------------------------------------------------------- #
def test_concept_expansion_does_not_discard_the_question() -> None:
    """Matching a concept used to replace the whole query.

    "What is the 180 day rule for raw cotton?" was searched as "cotton textile
    export policy order schedule conditions" - every term that made the
    question specific was gone, so the SRO stating the rule could not rank and
    the assistant answered that its sources did not cover it. They do.
    """
    query = _normalize_for_retrieval("What is the 180 day rule for raw cotton?", None)

    assert "180" in query
    assert "day" in query
    assert "rule" in query
    # The canonical vocabulary is still added, just no longer instead.
    assert "textile" in query


def test_tariff_codes_come_from_the_argument_not_the_question_text() -> None:
    """Stage 2 broadens by dropping the code, so the digits must not survive.

    Otherwise a question naming an unsupported code retrieves nothing at all
    rather than the related-category passages it is meant to fall back to.
    """
    question = "What broader product evidence exists for cotton PCT 62113200?"

    assert "62113200" not in _normalize_for_retrieval(question, None)
    assert "62113200" in _normalize_for_retrieval(question, "62113200")


def test_retrieval_meta_words_are_not_searched_for() -> None:
    """"Find passages mentioning X" is a request, not corpus vocabulary."""
    query = _normalize_for_retrieval("Find passages mentioning Certificate of Origin", None)

    assert "passages" not in query
    assert "mentioning" not in query
    assert "certificate" in query


# --------------------------------------------------------------------------- #
# Questions that name a supported product are never off-topic
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "question", ["Cotton", "cotton", "cotton yarn", "denim pants", "raw cotton"]
)
def test_product_questions_are_not_refused_as_off_topic(question: str) -> None:
    """These were answered with "I can only answer questions about customs".

    The domain guard wants a trade-context word next to the product, which a
    bare product name does not have - so naming a product CACE deterministically
    supports was treated as being outside customs entirely.
    """
    assert classify_regulatory_intent(question).intent != "out_of_scope"


@pytest.mark.parametrize(
    "question", ["What is the capital of France?", "how do I bake bread", "tell me a joke"]
)
def test_genuinely_unrelated_questions_are_still_refused(question: str) -> None:
    """Widening the guard must not open it."""
    assert classify_regulatory_intent(question).intent == "out_of_scope"


def test_bare_product_name_asks_about_that_product() -> None:
    assert classify_regulatory_intent("raw cotton").pct_code == "52010090"
    assert classify_regulatory_intent("raw cotton").intent == "supported_pct_guidance"


def test_a_question_that_merely_names_a_product_is_not_a_checklist() -> None:
    """"What is the 180 day rule for raw cotton?" asks about a rule.

    It names a supported product, but answering it with a document checklist
    would ignore what was actually asked.
    """
    decision = classify_regulatory_intent("What is the 180 day rule for raw cotton?")
    assert decision.intent == "general_regulatory_information"


# --------------------------------------------------------------------------- #
# Product vocabulary
# --------------------------------------------------------------------------- #
def test_raw_cotton_can_be_named_in_words() -> None:
    """52010090 is the product CACE models most fully and had no alias at all."""
    assert resolve_product("raw cotton").pct_code == "52010090"


def test_a_garment_named_with_its_material_is_the_garment() -> None:
    """"denim pants" are trousers, not denim fabric.

    Matching took the first alias token in the sentence, so "denim" won and
    the answer was about fabric.
    """
    resolution = resolve_product("denim pants")
    assert resolution.matched_term == "pants"
    assert set(resolution.candidates) == {"62034200", "62046290"}

    assert resolve_product("mens denim trousers").pct_code == "62034200"
    # A material named on its own still resolves to the material.
    assert set(resolve_product("denim fabric").candidates) == {"52094200", "52114200"}


# --------------------------------------------------------------------------- #
# Completeness claims
# --------------------------------------------------------------------------- #
def test_completeness_claims_are_rejected() -> None:
    """Retrieved passages can never support a claim about what is *not* required.

    A live answer ended "These are the only documents required for denim pants
    export according to the indexed sources"; an exporter who trusted it would
    arrive at customs short of paperwork.
    """
    verdict = validate_answer(
        "These are the only documents required for denim pants export "
        "according to the indexed sources.",
        has_accepted_evidence=True,
    )
    assert not verdict.ok
    assert "completeness claim" in verdict.violations


def test_softer_completeness_wording_is_also_rejected() -> None:
    """The first wording of this rule let a live answer through.

    "No extra documents are needed for most destinations, so just prepare
    these four items" is the same claim in gentler words.
    """
    verdict = validate_answer(
        "No extra documents are needed for most destinations, so just prepare "
        "these four items before you send the goods.",
        has_accepted_evidence=True,
    )
    assert not verdict.ok
    assert "completeness claim" in verdict.violations


def test_deadline_questions_reach_the_letter_of_credit_vocabulary() -> None:
    """The 180-day rule is stated in terms of the letter of credit.

    A question about "the 180 day rule" and the passage that answers it share
    almost no words, and "rule" used to expand to the name of a *different*
    instrument (the Export Policy Order), which then outranked the SRO that
    actually states it.
    """
    query = _normalize_for_retrieval("What is the 180 day rule for raw cotton?", None)

    assert "letter" in query and "credit" in query
    assert "policy" not in query


def test_ordinary_document_wording_is_still_allowed() -> None:
    verdict = validate_answer(
        "A commercial invoice and a packing list are normally prepared for a "
        "textile export. The destination may ask for a certificate of origin.",
        has_accepted_evidence=True,
    )
    assert verdict.ok
