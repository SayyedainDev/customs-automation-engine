"""Safety-review fixes: distinguishing an extraction error from a document
error, and freezing audit revision 1 at the point the original deterministic
result is recorded rather than retroactively when a correction happens to be
submitted.

No Groq call anywhere in this file - every narrator is the deterministic
default (make_service()'s explanation_narrator=None).
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.core.database import Base

from tests.unit.test_customs_audit import (
    PASSED_LEGAL_CHECK,
    correction_decision,
    events,
    line,
    make_extraction,
    make_service,
    mark_field_uncertain,
    review,
    review_task,
    start,
)


def _confident_quantity_mismatch_extraction():
    """Both sides read the quantity confidently and cleanly (fld()'s default
    confidence=0.99, validation_status=verified) - a genuine disagreement
    between two documents, not a software reading error."""
    return make_extraction(
        [
            line(
                status="failed",
                compliance_checks=[PASSED_LEGAL_CHECK],
                item_checks=[
                    {
                        "check_id": "item_quantity_match",
                        "status": "failed",
                        "message": "Quantity mismatch: invoice has '100' and packing list has '99'.",
                    }
                ],
            )
        ],
        "failed",
    )


def _low_confidence_quantity_mismatch_extraction():
    extraction = _confident_quantity_mismatch_extraction()
    return mark_field_uncertain(extraction, doc="invoice", item_index=1, field="quantity")


def _ocr_ambiguous_quantity_mismatch_extraction():
    extraction = _confident_quantity_mismatch_extraction()
    invoice_field = extraction["invoice"]["line_items"][0]["quantity"]
    invoice_field["extraction_method"] = "tesseract_ocr_llm_structured_output"
    invoice_field["confidence"] = "0.65"
    invoice_field["validation_status"] = "manual_review"
    invoice_field["ocr_confidence"] = "0.65"
    return extraction


# --------------------------------------------------------------------------- #
# Issue 1: distinguish extraction error from document error
# --------------------------------------------------------------------------- #
# 1. Low-confidence extracted quantity can be confirmed.
def test_1_low_confidence_extracted_quantity_can_be_confirmed(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, _low_confidence_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    task = review_task(svc, isolated_database, result["workflow_id"])
    invoice_detail = next(d for d in task["disputed_field_details"] if d["document_type"] == "commercial_invoice")
    assert invoice_detail["correction_basis"] == "low_confidence_extraction"

    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    assert resumed["deterministic_status"] == "passed"
    assert "correction_rejected_document_conflict" not in [
        e["event_type"] for e in events(svc, isolated_database, result["workflow_id"])
    ]


# 2. OCR ambiguity can be corrected.
def test_2_ocr_ambiguity_can_be_corrected(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, _ocr_ambiguous_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    task = review_task(svc, isolated_database, result["workflow_id"])
    invoice_detail = next(d for d in task["disputed_field_details"] if d["document_type"] == "commercial_invoice")
    assert invoice_detail["correction_basis"] == "ambiguous_ocr"

    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    assert resumed["deterministic_status"] == "passed"


# 3. A clearly extracted document mismatch cannot be overwritten.
def test_3_confirmed_document_mismatch_cannot_be_overwritten(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, _confident_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    task = review_task(svc, isolated_database, result["workflow_id"])
    invoice_detail = next(d for d in task["disputed_field_details"] if d["document_type"] == "commercial_invoice")
    assert invoice_detail["correction_basis"] == "confirmed_document_mismatch"

    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "99"),
    )
    event_types = [e["event_type"] for e in events(svc, isolated_database, result["workflow_id"])]
    assert "correction_rejected_document_conflict" in event_types
    assert "human_correction_applied" not in event_types
    assert resumed["deterministic_status"] == "failed"


# 4. Attempting to overwrite a confirmed source value is rejected.
def test_4_overwrite_attempt_on_a_confirmed_value_is_rejected(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, _confident_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "99"),
    )
    assert resumed["status"] == "completed"
    assert resumed["final_report"]["correction_validation_errors"]


# 5. Rejection does not create a new audit revision.
def test_5_rejection_does_not_create_a_new_audit_revision(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, _confident_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "99"),
    )
    snapshot = svc._graph.get_state({"configurable": {"thread_id": resumed["thread_id"]}})
    assert len(snapshot.values["audit_revisions"]) == 1
    assert len(snapshot.values["deterministic_result_history"]) == 1


# 6. The original frozen result remains unchanged.
def test_6_original_frozen_result_remains_unchanged_after_rejection(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, _confident_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    before_snapshot = svc._graph.get_state({"configurable": {"thread_id": result["thread_id"]}})
    revision_1_before = before_snapshot.values["audit_revisions"][0]

    review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "99"),
    )

    after_snapshot = svc._graph.get_state({"configurable": {"thread_id": result["thread_id"]}})
    revision_1_after = after_snapshot.values["audit_revisions"][0]
    assert revision_1_after == revision_1_before
    assert revision_1_after["deterministic_result"]["overall_status"] == "failed"


# 7. The response requests a corrected document.
def test_7_response_requests_a_corrected_document(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, _confident_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "99"),
    )
    errors = resumed["final_report"]["correction_validation_errors"]
    assert any(
        "The uploaded document itself contains the conflicting value. "
        "Upload a corrected document and run the audit again." in message
        for message in errors
    )


# 3 checks that never used to exist: confirm/correct actions cannot use a
# document-conflict basis, and the three explicitly-forbidden bases are
# never wired to correctable status.
def test_disallowed_bases_are_never_in_the_correctable_set() -> None:
    from app.services.customs_audit.state import CORRECTABLE_BASES

    for forbidden in ("confirmed_document_mismatch", "missing_required_document", "regulatory_violation"):
        assert forbidden not in CORRECTABLE_BASES
    for allowed in (
        "low_confidence_extraction",
        "ambiguous_ocr",
        "parser_error",
        "human_confirmation_required",
    ):
        assert allowed in CORRECTABLE_BASES


# --------------------------------------------------------------------------- #
# Issue 2: capture revision 1 when status is frozen, not retroactively
# --------------------------------------------------------------------------- #
# 1. Revision 1 exists before interrupt_for_human_review.
def test_r1_revision_1_exists_before_the_workflow_pauses(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, _low_confidence_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    assert result["status"] == "awaiting_human_review"
    snapshot = svc._graph.get_state({"configurable": {"thread_id": result["thread_id"]}})
    revisions = snapshot.values["audit_revisions"]
    assert len(revisions) == 1
    assert revisions[0]["revision_number"] == 1
    assert revisions[0]["frozen"] is True
    assert revisions[0]["auditor_report"] is not None
    assert revisions[0]["consensus_result"] is not None


# 2. Revision 1 exists even when no correction is submitted.
def test_r2_revision_1_exists_even_when_no_correction_is_ever_submitted(
    isolated_database: Engine,
) -> None:
    extraction = make_extraction(
        [
            line(
                status="passed",
                compliance_checks=[PASSED_LEGAL_CHECK],
                item_checks=[{"check_id": "item_quantity_match", "status": "passed"}],
            )
        ],
        "passed",
    )
    svc = make_service(isolated_database, extraction)
    result = start(svc, isolated_database)
    assert result["status"] == "completed"  # no human review was ever needed
    revisions = result["final_report"]["audit_revisions"]
    assert len(revisions) == 1
    assert revisions[0]["revision_number"] == 1
    assert revisions[0]["deterministic_result"]["overall_status"] == "passed"


# 3. Human correction never creates revision 1 retroactively.
def test_r3_correction_never_creates_revision_1_it_only_appends_revision_2(
    isolated_database: Engine,
) -> None:
    svc = make_service(isolated_database, _low_confidence_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    revision_events = [
        e for e in events(svc, isolated_database, result["workflow_id"])
        if e["event_type"] == "audit_revision_frozen"
    ]
    # Revision 1 is opened (provisional) and completed by the deterministic/
    # consensus nodes only - apply_human_correction never emits this event.
    assert {e["node_name"] for e in revision_events} == {
        "deterministic_compliance",
        "compare_agent_reports",
        "freeze_corrected_revision",
    }
    assert resumed["final_report"]["audit_revisions"][0]["triggered_by"] == "initial"


# 4. Revision 1 cannot be mutated after the review task is issued.
def test_r4_revision_1_is_immutable_after_the_review_task_is_issued(
    isolated_database: Engine,
) -> None:
    svc = make_service(isolated_database, _low_confidence_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    before = svc._graph.get_state({"configurable": {"thread_id": result["thread_id"]}}).values[
        "audit_revisions"
    ][0]

    review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )

    after = svc._graph.get_state({"configurable": {"thread_id": result["thread_id"]}}).values[
        "audit_revisions"
    ][0]
    assert after == before


# 5. Revision 2 contains the revised value and recalculated result.
def test_r5_revision_2_contains_the_revised_value_and_recalculated_result(
    isolated_database: Engine,
) -> None:
    svc = make_service(isolated_database, _low_confidence_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    revisions = resumed["final_report"]["audit_revisions"]
    assert len(revisions) == 2
    assert revisions[1]["revision_number"] == 2
    assert revisions[1]["triggered_by"] == "human_correction"
    assert revisions[1]["deterministic_result"]["overall_status"] == "passed"
    assert revisions[1]["auditor_report"]["observed_deterministic_status"] == "passed"


# 6. Revision 1 retains the original value and status.
def test_r6_revision_1_retains_the_original_value_and_status(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, _low_confidence_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    revisions = resumed["final_report"]["audit_revisions"]
    assert revisions[0]["deterministic_result"]["overall_status"] == "failed"
    assert revisions[0]["revision_number"] == 1


# 7. Process restart preserves both revisions.
def test_r7_process_restart_preserves_both_revisions(tmp_path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    ck_path = tmp_path / "checkpoints.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(engine)

    def make(engine_ref):
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        saver = SqliteSaver(sqlite3.connect(str(ck_path), check_same_thread=False))
        saver.setup()
        return make_service(engine_ref, _low_confidence_quantity_mismatch_extraction(), checkpointer=saver)

    svc1 = make(engine)
    result = start(svc1, engine)
    assert result["status"] == "awaiting_human_review"

    # "Restart": brand-new service + checkpointer connection on the same files.
    svc2 = make(engine)
    resumed = review(
        svc2, engine, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    assert resumed["status"] == "completed"
    revisions = resumed["final_report"]["audit_revisions"]
    assert len(revisions) == 2
    assert revisions[0]["deterministic_result"]["overall_status"] == "failed"
    assert revisions[1]["deterministic_result"]["overall_status"] == "passed"
    engine.dispose()


# --------------------------------------------------------------------------- #
# Additional checks
# --------------------------------------------------------------------------- #
def test_regulatory_correction_invalidates_the_old_citation_before_retrieval(
    isolated_database: Engine,
) -> None:
    """The stale citation for an affected regulatory check must not survive
    into the new evidence map even before the fresh query result is known -
    it is never copied forward, only reused when NOT affected."""
    xr_check = {
        "check_id": "xr_61091000_export_status",
        "check_name": "Export status for Cotton knitted T-shirts",
        "status": "passed",
        "source_document": "TIPP Export Policy Order",
        "sro_number": None,
        "pct_code": "61091000",
        "source_page": None,
        "effective_date": "2022-04-22",
    }
    extraction = make_extraction(
        [
            line(
                status="failed",
                compliance_checks=[PASSED_LEGAL_CHECK, xr_check],
                item_checks=[
                    {
                        "check_id": "item_pct_code_match",
                        "status": "failed",
                        "message": "PCT code mismatch: invoice has '6109.1000' and packing list has '6110.1000'.",
                    }
                ],
            )
        ],
        "failed",
    )
    mark_field_uncertain(extraction, doc="invoice", item_index=1, field="pct_code")

    call_count = {"n": 0}

    def evidence_fn(db, pct, query):
        call_count["n"] += 1
        return [
            {
                "source_document": f"TIPP call {call_count['n']}",
                "validation_status": "verified",
                "evidence_text": f"Evidence version {call_count['n']}",
            }
        ]

    svc = make_service(isolated_database, extraction, evidence_fn=evidence_fn)
    result = start(svc, isolated_database)
    snapshot_before = svc._graph.get_state({"configurable": {"thread_id": result["thread_id"]}})
    old_citation = snapshot_before.values["regulatory_evidence_by_check"]["xr_61091000_export_status"][0]

    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].pct_code", "61091000"),
    )
    new_citation = resumed["final_report"]["regulatory_evidence_by_check"]["xr_61091000_export_status"][0]
    assert new_citation["source_title"] != old_citation["source_title"]
    old_call_number = int(old_citation["source_title"].rsplit(" ", 1)[-1])
    new_call_number = int(new_citation["source_title"].rsplit(" ", 1)[-1])
    assert new_call_number > old_call_number


def test_unsupported_actions_are_explicitly_rejected_not_silently_completed(
    isolated_database: Engine,
) -> None:
    from app.services.customs_audit.workflow_service import WorkflowStateError

    svc = make_service(isolated_database, _low_confidence_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    task = review_task(svc, isolated_database, result["workflow_id"])
    assert "provide_missing_document" not in task["requested_actions"]
    assert "request_reprocessing" not in task["requested_actions"]

    decision = {
        "action": "provide_missing_document",
        "corrections": [],
        "provided_document_ids": ["11111111-1111-1111-1111-111111111111"],
        "reviewer_reference": "supervisor-001",
        "timestamp": "2026-07-28T00:00:00",
    }
    try:
        review(svc, isolated_database, result["workflow_id"], decision)
        raised = False
    except WorkflowStateError:
        raised = True
    assert raised
    # The workflow is still open, not silently marked completed.
    still_open = review_task(svc, isolated_database, result["workflow_id"])
    assert still_open is not None


def test_human_confirmed_field_is_labelled_correctly_not_as_pdf_extracted() -> None:
    from app.services.customs_audit.agents import _provenance
    from app.services.customs_audit.state import ProvenanceLabel

    assert _provenance("human_review") == ProvenanceLabel.HUMAN_CORRECTION
    assert _provenance("pdf_text_llm_structured_output") != ProvenanceLabel.HUMAN_CORRECTION
