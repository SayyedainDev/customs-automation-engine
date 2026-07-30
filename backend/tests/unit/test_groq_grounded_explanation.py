"""Ask CACE's regulatory explanations now call Groq, on request from the
project owner: retrieve evidence-gated chunks, then ask Groq to explain them
in plain language, rather than only using written templates.

The owner's instruction was explicit: "I want here that groq call happen it
retrieve the chunk then goes to LLM and in LLM write prompt that it explain it
simply and to the point." This module proves that call actually happens for
ordinary explanation questions, and - just as importantly - that it is bounded
and safe: exactly one call per question, grounded only in already-gated
evidence, validated the same way every other visible answer is validated, and
falls back to the deterministic template whenever the provider is unavailable,
errors, or returns something that does not pass.

No real Groq call is made anywhere in this file - every test injects a fake or
raising client. ``default_no_groq_key`` in conftest.py additionally blanks the
credential for every test by default, so a code path that forgot to inject a
client would fail closed (configuration error -> fallback) rather than quietly
reaching the real network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import app.services.structured_extraction_service as extraction_service
from app.core.exceptions import StructuredExtractionConfigurationError
from app.services.assistant.regulatory_chat import (
    _groq_answer_is_acceptable,
    _groq_explanation_or_none,
    answer_regulatory_question,
)
from app.services.assistant.plain_language import contains_internal_tokens
from app.services.regulatory.embeddings import (
    FakeEmbeddingProvider,
    reset_embedding_provider,
    set_embedding_provider,
)
from app.services.regulatory.reranker import LexicalReranker, reset_reranker, set_reranker
from app.schemas.assistant import RegulatoryCitationSchema
from tests.unit.test_regulatory_retrieval import build_corpus


@pytest.fixture(autouse=True)
def _offline_retrieval():
    set_embedding_provider(FakeEmbeddingProvider(dimension=16))
    set_reranker(LexicalReranker())
    yield
    reset_embedding_provider()
    reset_reranker()


def _fake_groq(content: str, *, calls: list[dict] | None = None):
    """A stand-in Groq client that returns ``content`` and counts invocations."""

    class _Message:
        pass

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            if calls is not None:
                calls.append(kwargs)
            _Response.choices[0].message.content = content
            return _Response()

    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))


def _raising_groq(exc: Exception):
    class _Completions:
        def create(self, **kwargs):
            raise exc

    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))


def _citation(text: str) -> RegulatoryCitationSchema:
    return RegulatoryCitationSchema(
        title="Test Source",
        source_kind="official_manual",
        source_kind_label="Official manual",
        is_official=True,
        accepted_passage=text,
        evidence_status="accepted",
    )


def ask(db: Session, question: str):
    return answer_regulatory_question(db, question=question)


# --------------------------------------------------------------------------- #
# The call actually happens, exactly once, grounded in the gated evidence
# --------------------------------------------------------------------------- #
def test_groq_is_called_for_an_explanation_question(
    isolated_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    fake = _fake_groq(
        "Form-E records your export with your bank and customs. It shows the "
        "shipment was officially declared and is normally required before you "
        "submit an export.",
        calls=calls,
    )
    monkeypatch.setattr(extraction_service, "_get_groq_client", lambda: fake)

    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "why is form e required")

    assert len(calls) == 1, "expected exactly one Groq call for one question"
    request = calls[0]
    assert request["model"] == "openai/gpt-oss-20b"
    assert request["response_format"] == {"type": "text"}
    # Grounded: the retrieved evidence is in the prompt, nothing else is.
    assert "Evidence" in request["messages"][1]["content"]
    assert response.answer_mode == "explanation"
    assert "officially declared" in response.answer


def test_only_gated_evidence_passages_reach_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    fake = _fake_groq("An answer.", calls=calls)
    monkeypatch.setattr(extraction_service, "_get_groq_client", lambda: fake)

    citations = [_citation("Passage about Form-E and customs declaration.")]
    _groq_explanation_or_none("why is form e required", citations)

    prompt = calls[0]["messages"][1]["content"]
    assert "Passage about Form-E and customs declaration." in prompt


def test_no_call_when_there_is_no_evidence_to_ground_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    fake = _fake_groq("should never be reached", calls=calls)
    monkeypatch.setattr(extraction_service, "_get_groq_client", lambda: fake)

    result = _groq_explanation_or_none("why is form e required", [])
    assert result is None
    assert calls == []


# --------------------------------------------------------------------------- #
# Fallback: unavailable, erroring, or invalid output never reaches the user
# --------------------------------------------------------------------------- #
def test_falls_back_to_template_when_no_key_is_configured(
    isolated_database: Engine,
) -> None:
    """The autouse fixture blanks the key; this pins that this is safe."""
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "what is a packing list")
    assert response.answer_mode == "explanation"
    assert response.answer.startswith("A Packing List")


@pytest.mark.parametrize(
    "exc",
    [
        StructuredExtractionConfigurationError("no key"),
        RuntimeError("connection reset"),
    ],
)
def test_falls_back_to_template_when_the_provider_is_unreachable(
    isolated_database: Engine, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    monkeypatch.setattr(
        extraction_service, "_get_groq_client", lambda: _raising_groq(exc)
    )
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "what is a packing list")
    assert response.answer.startswith("A Packing List")


def test_falls_back_when_generated_output_fails_validation(
    isolated_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A compliance-verdict claim from the model must not reach the user."""
    fake = _fake_groq("This shipment will clear customs without any issues.")
    monkeypatch.setattr(extraction_service, "_get_groq_client", lambda: fake)
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "what is a packing list")
    assert "will clear customs" not in response.answer.casefold()
    assert response.answer.startswith("A Packing List")


def test_no_retry_on_a_single_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One user question makes at most one Groq call, even on failure."""
    call_count = {"n": 0}

    class _Completions:
        def create(self, **kwargs):
            call_count["n"] += 1
            raise RuntimeError("boom")

    fake = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    monkeypatch.setattr(extraction_service, "_get_groq_client", lambda: fake)

    _groq_explanation_or_none(
        "why is form e required", [_citation("Form-E is used to declare exports.")]
    )
    assert call_count["n"] == 1


# --------------------------------------------------------------------------- #
# The validation gate itself
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "answer",
    [
        "",
        "Yes.",  # too short
        "word " * 300,  # too long
        "Certificate required: value=conditional; source_kind: official",
        "This shipment is fully compliant and will clear customs without issue.",
        "The certificate is authentic and verified with the issuing authority.",
    ],
)
def test_unacceptable_groq_answers_are_rejected(answer: str) -> None:
    assert _groq_answer_is_acceptable(answer) is False


def test_a_normal_grounded_answer_is_accepted() -> None:
    answer = (
        "Form-E records your export and its expected payment with your bank "
        "and customs. It shows the shipment was officially declared and is "
        "normally required before you submit an export."
    )
    assert _groq_answer_is_acceptable(answer) is True


def test_leaked_tokens_are_sanitized_before_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that parrots the corpus's own 'value=False' style text back is
    still made safe: sanitize_for_display runs before the answer is used."""
    fake = _fake_groq(
        "Licence required: value=False; verified_no_licence_required_under_"
        "epo_2022_general_permission. This is a long enough sentence to pass "
        "the length floor for a generated answer about licence requirements."
    )
    monkeypatch.setattr(extraction_service, "_get_groq_client", lambda: fake)
    result = _groq_explanation_or_none(
        "why is a licence required",
        [_citation("Licence required: value=False; some detail about licences.")],
    )
    assert result is not None
    assert not contains_internal_tokens(result)
    assert "value=False" not in result


# --------------------------------------------------------------------------- #
# Groq must never run for the modes it was explicitly excluded from
# --------------------------------------------------------------------------- #
def test_checklist_questions_are_explained_by_groq_over_a_fixed_list(
    isolated_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checklist answers are now retrieved and explained, not just templated.

    This asserted Groq was never called here, which made the most common
    question in the product - "what documents do I need to export X" - the one
    path that never reached the corpus or the model at all. It now retrieves
    and explains.

    What must not change is where the document list comes from: the checklist
    is decided by the deterministic compliance rules and is still rendered
    verbatim underneath the prose, and the structured document fields are
    untouched by the model.
    """
    calls: list[dict] = []
    fake = _fake_groq(
        "Cotton towels are a mill-made textile export. The invoice and packing "
        "list describe what you are sending, and the export declaration files "
        "it with customs.",
        calls=calls,
    )
    monkeypatch.setattr(extraction_service, "_get_groq_client", lambda: fake)
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "what documents do I need for cotton towels to China?")

    assert response.answer_mode == "checklist"
    assert len(calls) == 1
    assert "Cotton towels are a mill-made textile export" in response.answer
    # The deterministic checklist survives underneath the generated prose.
    assert "Required documents" in response.answer
    assert "Commercial Invoice" in response.answer
    assert [d.display_name for d in response.required_documents]


def test_checklist_prose_is_dropped_if_it_invents_a_document(
    isolated_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model may explain the checklist; it may not extend it."""
    fake = _fake_groq(
        "For cotton towels you must also obtain a bill of entry and an "
        "inspection certificate before the goods can be shipped anywhere.",
    )
    monkeypatch.setattr(extraction_service, "_get_groq_client", lambda: fake)
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "what documents do I need for cotton towels to China?")

    assert "bill of entry" not in response.answer.casefold()
    assert "Required documents" in response.answer


def test_groq_is_not_called_for_document_search(
    isolated_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    fake = _fake_groq("should never be reached", calls=calls)
    monkeypatch.setattr(extraction_service, "_get_groq_client", lambda: fake)
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "find passages mentioning Certificate of Origin")
    assert response.answer_mode == "document_search"
    assert calls == []


def test_groq_is_not_called_for_out_of_scope_questions(
    isolated_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    fake = _fake_groq("should never be reached", calls=calls)
    monkeypatch.setattr(extraction_service, "_get_groq_client", lambda: fake)
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "write a python function")
    assert response.answer_mode == "refusal"
    assert calls == []


def test_groq_is_not_called_for_evidence_lookup(
    isolated_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evidence lookup returns the exact source; it does not need paraphrasing."""
    calls: list[dict] = []
    fake = _fake_groq("should never be reached", calls=calls)
    monkeypatch.setattr(extraction_service, "_get_groq_client", lambda: fake)
    with Session(isolated_database) as db:
        build_corpus(db)
        response = ask(db, "which rule says Form-E is required?")
    assert response.answer_mode == "evidence_lookup"
    assert calls == []
