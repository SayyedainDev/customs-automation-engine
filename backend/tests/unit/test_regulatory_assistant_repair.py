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


def test_every_checklist_document_has_its_own_explanation() -> None:
    """A real checklist repeated one empty sentence against four documents.

    "Phytosanitary certificate: It provides information needed for this export
    and destination" says nothing, and the raw-cotton checklist showed it for
    the phytosanitary certificate, both SBP documents and the letter of credit.
    """
    from app.services.assistant.guidance import get_document_explanation
    from app.services.assistant.regulatory_chat import _document_type_for_name

    names = [
        "Commercial Invoice",
        "Packing List",
        "Form-E / PSW export declaration",
        "Certificate of origin",
        "Phytosanitary certificate",
        "Proof of SBP deposit",
        "SBP confirmation",
        "Irrevocable letter of credit",
        "Import permit",
    ]
    explanations = [
        get_document_explanation(_document_type_for_name(name)) for name in names
    ]

    assert not any("provides information needed" in text for text in explanations)
    # Each document is described distinctly, not with one shared sentence.
    assert len(set(explanations)) == len(explanations)


def test_a_two_part_question_keeps_the_half_that_needs_no_product() -> None:
    """"What is form E and is it required for Yarn export" is two questions.

    Yarn is ambiguous between two supported codes, so the clarification
    branch fired and returned only "which yarn do you mean?" - discarding the
    Form-E explanation CACE had already detected and holds written out. The
    reader learned neither what Form-E is nor whether they need one, even
    though both candidates require it.
    """
    from app.services.assistant.regulatory_chat import _render_clarification

    answer = _render_clarification(
        [("52051100", "Cotton yarn"), ("52052100", "Combed cotton yarn (heavy count)")],
        guidance_pairs=(
            ["Commercial Invoice", "Packing List", "Form-E / PSW export declaration"],
            [],
        ),
        destination=None,
        corrections={},
        matched_term="yarn",
        question="What is form E and is it required for Yarn export",
    )

    # The definition half is answered.
    assert "records an export and its expected payment" in answer
    # The requirement half is answered without waiting for the product.
    assert "required for both of the products below" in answer
    # The clarification itself is still asked.
    assert "could mean" in answer
    assert "PCT 52051100" in answer


def test_clarification_without_a_named_concept_is_unchanged() -> None:
    """A plain "cotton yarn" still just asks which product."""
    from app.services.assistant.regulatory_chat import _render_clarification

    answer = _render_clarification(
        [("52051100", "Cotton yarn"), ("52052100", "Combed cotton yarn (heavy count)")],
        guidance_pairs=(["Commercial Invoice"], []),
        destination=None,
        corrections={},
        matched_term="yarn",
        question="cotton yarn",
    )

    assert "required for both of the products below" not in answer
    assert answer.startswith("Cotton yarn could mean")


# --------------------------------------------------------------------------- #
# Plain product names, as people actually type them
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Tshirts", "61091000"),
        ("tshirts", "61091000"),
        ("T-shirts", "61091000"),
        ("t shirts", "61091000"),
        ("towels", "63026010"),
        ("blankets", "63013000"),
        ("jerseys", "61102000"),
    ],
)
def test_plural_product_names_resolve(question: str, expected: str) -> None:
    """Nobody types the singular. These all resolved to nothing.

    Two causes. The resolver demanded a *separate* textile signal word
    ("cotton", "fabric"), which a bare "Tshirts" does not have. And plurals
    were handed to the spelling repairer, where "tshirts" sat at edit distance
    1 from both "tshirt" and "shirts" and was rejected as an ambiguous repair.
    """
    assert resolve_product(question).pct_code == expected


def test_a_product_word_is_its_own_textile_context() -> None:
    """"What is Form E and is it required for Tshirts" - no "cotton" anywhere."""
    decision = classify_regulatory_intent(
        "What is Form E and is it required for Tshirts"
    )
    assert decision.intent == "supported_pct_guidance"
    assert decision.pct_code == "61091000"


def test_a_repaired_word_still_needs_textile_context() -> None:
    """The guard that stops "paint" becoming "pants" must survive all this."""
    assert resolve_product("paint for my house").pct_code is None
    assert resolve_product("I need paint").pct_code is None
    assert classify_regulatory_intent("paint for my house").intent == "out_of_scope"


def test_prompt_evidence_markers_never_reach_the_reader() -> None:
    """The [1], [2] markers number a list only the model sees.

    A live evidence-lookup answer read "The source that says Form-E is
    required is evidence **[1]**" - a pointer to nothing, since the reader is
    shown named sources, not a numbered list. Both prompts now forbid citing
    them, and this is the guarantee behind that request.
    """
    from app.services.assistant.plain_language import strip_evidence_references

    cleaned = strip_evidence_references(
        "The source that says Form-E is required is evidence **[1]**. "
        "According to passage 2, a certificate of origin is conditional."
    )

    assert "[1]" not in cleaned
    assert "passage 2" not in cleaned
    assert "evidence **" not in cleaned
    assert "the indexed sources" in cleaned
    # No doubled spaces or space-before-punctuation left behind.
    assert "  " not in cleaned
    assert " ." not in cleaned


def test_paragraph_breaks_survive_sanitizing() -> None:
    """Answers are read as paragraphs; collapsing them would run them together."""
    from app.services.assistant.plain_language import strip_evidence_references

    assert strip_evidence_references("One.\n\nTwo.") == "One.\n\nTwo."


def test_cace_own_source_numbering_is_never_stripped() -> None:
    """The document-search answer numbers the sources it lists, visibly.

    Marker removal was first applied at the global display boundary, which
    also ran over CACE's own rendering and turned "[1] Export Policy Order..."
    into " Export Policy Order..." - unnumbering a list the reader does see.
    It applies to generated prose only.
    """
    from app.services.assistant.plain_language import sanitize_for_display

    rendered = '[1] Export Policy Order, 2022 — official SRO, page 4'
    assert sanitize_for_display(rendered) == rendered


def test_the_marker_defect_does_not_survive_as_an_ordinal() -> None:
    """Stripping "[1]" only moved the model on to "the first source".

    Both are pointers into a numbered list the reader was never shown, and
    neither is the fact they asked for. Both prompts now forbid position as
    well as number, and the display boundary enforces it.
    """
    from app.services.assistant.plain_language import strip_evidence_references

    assert "first source" not in strip_evidence_references(
        "The requirement for Form-E comes from the first source."
    )
    assert "second passage" not in strip_evidence_references(
        "According to the second passage, a certificate of origin is conditional."
    )


def test_ordinal_stripping_does_not_touch_ordinary_prose() -> None:
    """"The first shipment" is not a reference to the evidence list."""
    from app.services.assistant.plain_language import strip_evidence_references

    text = "The first shipment left on Monday and the source document is an invoice."
    assert strip_evidence_references(text) == text


def test_a_family_word_is_narrowed_by_kind_not_by_tariff_code_dump() -> None:
    """"Cotton" was answered with all seventeen codes.

    More precise than "be more specific", but a wall of tariff numbers is not
    a step an exporter can take. One narrowing question is.
    """
    from app.services.assistant.regulatory_chat import _product_family_prompt

    prompt = _product_family_prompt()

    assert "Raw cotton" in prompt
    assert "Made-ups" in prompt
    # Six families, one line each - not seventeen codes.
    assert len(prompt.splitlines()) == 6
    assert "PCT" not in prompt
    assert "52010090" not in prompt
