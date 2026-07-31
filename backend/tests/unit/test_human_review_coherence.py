"""The human-review interrupt must be recorded, actionable, and clearable.

Driving a real four-document workflow to its interrupt exposed four defects.
The pause itself worked - LangGraph stopped, the task was served, resume
continued the graph - but everything around it was wrong in ways that made the
review pointless or the audit trail untrue:

* the two events explaining *why* a person was needed were written only after
  someone resumed, so the trail was silent for exactly as long as the workflow
  was waiting;
* the task offered "confirm the extracted value" when consensus had flagged
  evidence-provenance gaps and no value was in dispute at all - taking the
  offered action produced ``correction_validation_failed`` and completed the
  workflow, resolving nothing;
* ``requires_human_review`` was never cleared, so a completed workflow kept
  asking for a review that had already happened;
* deterministically-parsed invoice fields were reported as ``llm_structured``.
"""

from __future__ import annotations

from typing import Any

from decimal import Decimal

from app.schemas.shipment_extraction import (
    CandidateField,
    ExtractionMethod,
    FieldValidationStatus,
)
from app.services.customs_audit.graph import GRAPH_NODES
from app.services.customs_audit.nodes import (
    _allowed_actions,
    _build_review_task,
    _plain_language_question,
)
from app.services.customs_audit.state import HumanAction


# --------------------------------------------------------------------------- #
# The task must only offer what can actually be done
# --------------------------------------------------------------------------- #
def test_evidence_gaps_do_not_offer_value_correction() -> None:
    """Nothing was misread, so there is no value to confirm or correct.

    A live workflow reached human review with consensus_reached=true,
    disagreed_fields=[] and both agents recommending "continue" - the only
    unresolved issues were that CACE's own curated rule text carries no page
    number and no confirmed effective date. It still offered
    confirm_extracted_value, and a reviewer who took it got
    correction_validation_failed.
    """
    actions = _allowed_actions(has_disputed_values=False)

    assert HumanAction.CONFIRM_EXTRACTED_VALUE not in actions
    assert HumanAction.CORRECT_EXTRACTED_VALUE not in actions
    # A person can still make the decision that is actually theirs to make.
    assert HumanAction.ACCEPT_MANUAL_REVIEW in actions
    assert HumanAction.REJECT_SUBMISSION in actions
    assert HumanAction.ADD_REVIEW_NOTE in actions


def test_a_real_value_dispute_still_offers_correction() -> None:
    """The narrowing must not remove the case the feature exists for."""
    actions = _allowed_actions(has_disputed_values=True)

    assert HumanAction.CONFIRM_EXTRACTED_VALUE in actions
    assert HumanAction.CORRECT_EXTRACTED_VALUE in actions


def test_the_question_names_the_real_blocker() -> None:
    """"A value could not be safely confirmed" was false and unanswerable.

    No value was involved. The reviewer is being asked whether to rely on a
    rule CACE could not fully cite.
    """
    question = _plain_language_question(
        "manual_review_required",
        [],
        [
            "evidence_partial",
            "required_fields: no source page/locator",
            "required_fields: no verified effective date",
        ],
    )

    assert "could not be traced to an exact page or section" in question
    # It states the decision being asked for, rather than "review the item".
    assert "accept" in question.casefold() and "reject" in question.casefold()


def test_a_two_document_disagreement_still_asks_which_value_is_right() -> None:
    details = [
        {
            "document_type": "commercial_invoice",
            "value": "1000.00",
            "field_path": "invoice.net_weight",
        },
        {
            "document_type": "packing_list",
            "value": "1050.00",
            "field_path": "packing_list.net_weight",
        },
    ]
    question = _plain_language_question("net_weight_mismatch", details, [])

    assert "commercial invoice says 1000.00" in question
    assert "packing list says 1050.00" in question


def test_a_single_uncertain_value_is_named_in_the_question() -> None:
    """One disputed value should not fall back to generic wording."""
    question = _plain_language_question(
        "manual_review_required",
        [{"document_type": "commercial_invoice", "value": "2000.00",
          "field_path": "invoice.invoice_total"}],
        [],
    )

    assert "2000.00" in question
    assert "invoice.invoice total" in question


def test_the_built_task_agrees_with_its_own_details() -> None:
    """Whatever the state, the offered actions match the attached details."""
    state: dict[str, Any] = {
        "workflow_id": "wf-1",
        "human_review_round": 0,
        "consensus_result": {
            "reason": "unresolved issues: evidence_partial",
            "disagreed_fields": [],
            "unresolved_issues": ["evidence_partial"],
            "deterministic_status": "failed",
        },
        "extraction_result": {"items": [], "page_reviews": []},
        "deterministic_result_history": [{}],
    }

    request = _build_review_task(state)

    assert request.disputed_field_details == []
    assert HumanAction.CONFIRM_EXTRACTED_VALUE not in request.allowed_actions
    assert request.plain_language_question is not None
    assert "not yet validated line by line" in request.plain_language_question


# --------------------------------------------------------------------------- #
# The pause must be recorded before it happens
# --------------------------------------------------------------------------- #
def test_the_task_is_built_and_recorded_before_the_graph_pauses() -> None:
    """``interrupt()`` raises, so a node calling it never reaches its return.

    Building the task inside that node meant its two events were written only
    once someone resumed - and timestamped then - leaving the audit trail
    silent for exactly as long as a person was being waited on.
    """
    assert "prepare_human_review" in GRAPH_NODES
    assert GRAPH_NODES.index("prepare_human_review") < GRAPH_NODES.index(
        "interrupt_for_human_review"
    )


def test_the_pausing_node_computes_nothing() -> None:
    """It re-runs from the top on resume, so it must be free of decisions."""
    import inspect

    from app.services.customs_audit import nodes as nodes_module

    source = inspect.getsource(nodes_module)
    body = source[source.index("def interrupt_for_human_review(") :]
    body = body[: body.index("\n    def ")]

    # It reads the already-built request and pauses. It must not build one.
    assert "_build_review_task" not in body
    assert "interrupt(" in body


# --------------------------------------------------------------------------- #
# Provenance must name what actually read the value
# --------------------------------------------------------------------------- #
def _candidate(note: str) -> CandidateField[str]:
    return CandidateField(
        value="Multan Raw Cotton Traders (Pvt.) Ltd",
        source_page=1,
        confidence=Decimal("0.95"),
        validation_status=FieldValidationStatus.VERIFIED,
        validation_note=note,
    )


def test_deterministic_invoice_fields_are_not_labelled_as_llm_output() -> None:
    """The auditor was told a language model read fields it never saw.

    ``materialize_field`` recognised only the supporting-document hybrid's
    notes; the invoice/packing-list hybrid writes "hybrid extractor: regex_*",
    which matched nothing and fell through to the LLM default.
    """
    from app.services.extraction.document_bundle import materialize_field
    from tests.unit.test_cold_start_review_tokens import _bundle, STANDARD_INVOICE

    bundle = _bundle(STANDARD_INVOICE, "commercial_invoice")

    for method_name in (
        "regex_labeled",
        "regex_bare",
        "regex_table",
        "regex_stacked_table",
    ):
        field = materialize_field(
            _candidate(f"hybrid extractor: {method_name}"), bundle
        )
        assert field.extraction_method == ExtractionMethod.REGEX_LABEL, method_name


def test_a_genuine_gap_fill_is_still_labelled_as_one() -> None:
    """Narrowing the LLM label must not hide a real provider call."""
    from app.services.extraction.document_bundle import materialize_field
    from tests.unit.test_cold_start_review_tokens import _bundle, STANDARD_INVOICE

    field = materialize_field(
        _candidate("hybrid extractor: llm_gapfill"),
        _bundle(STANDARD_INVOICE, "commercial_invoice"),
    )

    assert field.extraction_method == ExtractionMethod.LLM_GAPFILL


# --------------------------------------------------------------------------- #
# The review screen
# --------------------------------------------------------------------------- #
def test_the_review_screen_shows_the_question_not_the_internal_reason() -> None:
    """The plain-language question rendered only inside CorrectionPanel.

    That panel does not render when no value is in dispute - exactly the case
    where the reviewer most needs the question - so they were shown the raw
    consensus string instead: "unresolved issues: evidence_partial;
    required_fields: no source page/locator".
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    source = (root / "frontend/src/components/AgentAuditResult.tsx").read_text()

    assert "{reviewTask.plain_language_question || reviewTask.reason}" in source
    # The raw internal strings are kept, but behind a technical disclosure.
    assert '<summary>Why this needs a person</summary>' in source
