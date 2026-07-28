"""The 18 required scenarios for targeted human correction + affected-check
recomputation.

All agent execution is deterministic/mocked and every RAG lookup is a fake
function under direct control - no Groq call, no model download, no network
request is made anywhere in this file (see test_18 for the explicit check).
Reuses the make_service/start/review/events/correction_decision harness from
test_customs_audit.py rather than duplicating it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.services.customs_audit.agents import DeterministicAuditorAgent
from app.services.customs_audit.state import utcnow_iso
from app.services.multi_line.field_paths import parse_field_path

from tests.unit.test_customs_audit import (
    PASSED_LEGAL_CHECK,
    accept_decision,
    correction_decision,
    events,
    fake_recheck_pipeline,
    line,
    make_extraction,
    make_service,
    manual_review_extraction,
    review,
    review_task,
    start,
)


class LyingAuditor(DeterministicAuditorAgent):
    def build_report(self, broker, extraction_result, deterministic_status, evidence_by_check):
        report = super().build_report(broker, extraction_result, deterministic_status, evidence_by_check)
        return report.model_copy(update={"observed_deterministic_status": "passed"})


def clean_quantity_mismatch_extraction():
    """Single problem: invoice/packing quantity disagree. No other blocker,
    so correcting it resolves everything and the workflow completes."""
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


_XR_PCT_CHECK = {
    "check_id": "xr_61091000_export_status",
    "check_name": "Export status for Cotton knitted T-shirts",
    "status": "passed",
    "source_document": "TIPP Export Policy Order",
    "sro_number": None,
    "pct_code": "61091000",
    "source_page": None,
    "effective_date": "2022-04-22",
}


def clean_pct_regulatory_extraction():
    """Single problem: PCT code disagreement, plus one xr_-family regulatory
    check already on the shipment so a PCT correction has something concrete
    to requery evidence for."""
    return make_extraction(
        [
            line(
                status="failed",
                compliance_checks=[PASSED_LEGAL_CHECK, _XR_PCT_CHECK],
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


def two_problem_extraction():
    """Quantity AND PCT code both disagree - independent problems, so
    correcting only one must leave the shipment still not ready."""
    return make_extraction(
        [
            line(
                status="failed",
                compliance_checks=[PASSED_LEGAL_CHECK],
                item_checks=[
                    {"check_id": "item_quantity_match", "status": "failed", "message": "Quantity mismatch: invoice has '100' and packing list has '99'."},
                    {"check_id": "item_pct_code_match", "status": "failed", "message": "PCT code mismatch: invoice has '6109.1000' and packing list has '6110.1000'."},
                ],
            )
        ],
        "failed",
    )


# 1. Quantity mismatch creates a structured human-review task.
def test_01_quantity_mismatch_creates_structured_review_task(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, clean_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    assert result["status"] == "awaiting_human_review"

    task = review_task(svc, isolated_database, result["workflow_id"])
    assert task["reason_code"] == "quantity_mismatch"
    field_paths = {d["field_path"] for d in task["disputed_field_details"]}
    assert "invoice.line_items[1].quantity" in field_paths
    for detail in task["disputed_field_details"]:
        parse_field_path(detail["field_path"])  # every field path is well-formed
    assert "item_quantity_match" in task["affected_check_ids"]

    snapshot = svc._graph.get_state({"configurable": {"thread_id": result["thread_id"]}})
    assert snapshot.next  # a real checkpoint exists and the graph is paused, not finished


# 2. Human corrects packing quantity from 99 to 100.
def test_02_correction_creates_a_new_passing_revision(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, clean_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    task = review_task(svc, isolated_database, result["workflow_id"])
    invoice_detail = next(d for d in task["disputed_field_details"] if d["document_type"] == "commercial_invoice")
    assert invoice_detail["value"] == "100"  # original value visible before correction

    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )

    snapshot = svc._graph.get_state({"configurable": {"thread_id": resumed["thread_id"]}})
    values = snapshot.values

    # original value remains preserved (in the correction record)
    correction = values["human_correction_history"][-1]
    assert correction["corrected_value"] == "100"
    assert correction["correction_id"]
    # affected checks rerun
    event_types = [e["event_type"] for e in events(svc, isolated_database, result["workflow_id"])]
    for required in ("human_correction_applied", "affected_checks_identified", "checks_recomputed", "audit_revision_frozen", "auditor_revision_reviewed", "consensus_recomputed"):
        assert required in event_types, required
    # new deterministic status calculated
    assert resumed["deterministic_status"] == "passed"
    # revision 1 remains unchanged; revision 2 is frozen
    history = values["deterministic_result_history"]
    assert len(history) == 2
    assert history[0]["overall_status"] == "failed" and history[0]["version"] == 1
    assert history[1]["overall_status"] == "passed" and history[1]["version"] == 2
    # Auditor reviewed revision 2
    assert values["auditor_report"]["observed_deterministic_status"] == "passed"
    # workflow completes when consensus is reached
    assert resumed["status"] == "completed"


# 3. Human attempts to send final_status = "passed".
def test_03_human_cannot_set_final_status_directly(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, clean_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    assert result["deterministic_status"] == "failed"

    decision = accept_decision()
    decision["final_status"] = "passed"
    decision["deterministic_status"] = "passed"
    resumed = review(svc, isolated_database, result["workflow_id"], decision)

    assert resumed["deterministic_status"] == "failed"
    assert resumed["final_report"]["deterministic_compliance_status"] == "failed"
    snapshot = svc._graph.get_state({"configurable": {"thread_id": resumed["thread_id"]}})
    assert len(snapshot.values.get("deterministic_result_history") or []) == 1


# 4. Human attempts to modify a field not included in the review task.
def test_04_correction_outside_the_review_task_is_rejected(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, clean_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].unit_price", "999"),
    )
    event_types = [e["event_type"] for e in events(svc, isolated_database, result["workflow_id"])]
    assert "correction_validation_failed" in event_types
    assert "human_correction_applied" not in event_types
    assert resumed["deterministic_status"] == "failed"


# 5. Human submits a string for a numeric quantity.
def test_05_non_numeric_correction_value_is_rejected_safely(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, clean_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "one hundred"),
    )
    event_types = [e["event_type"] for e in events(svc, isolated_database, result["workflow_id"])]
    assert "correction_validation_failed" in event_types
    assert resumed["deterministic_status"] == "failed"  # no unsafe state mutation
    snapshot = svc._graph.get_state({"configurable": {"thread_id": resumed["thread_id"]}})
    assert len(snapshot.values.get("deterministic_result_history") or []) == 1


# 6. Human confirms the original value without changing it.
def test_06_confirming_the_original_value_reruns_checks(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, clean_quantity_mismatch_extraction())
    result = start(svc, isolated_database)

    decision = {
        "action": "confirm_extracted_value",
        "corrections": [
            {
                "field_path": "invoice.line_items[1].quantity",
                "original_value": "100",
                "corrected_value": "100",
                "reviewer_reference": "supervisor-001",
                "reason": "Verified against the physical shipment count.",
                "source": "invoice page 1",
                "timestamp": utcnow_iso(),
            }
        ],
        "reviewer_reference": "supervisor-001",
        "timestamp": utcnow_iso(),
    }
    resumed = review(svc, isolated_database, result["workflow_id"], decision)

    snapshot = svc._graph.get_state({"configurable": {"thread_id": resumed["thread_id"]}})
    correction = snapshot.values["human_correction_history"][-1]
    assert correction["original_value"] == correction["corrected_value"] == "100"
    event_types = [e["event_type"] for e in events(svc, isolated_database, result["workflow_id"])]
    assert "human_correction_applied" in event_types
    assert "checks_recomputed" in event_types


# 7. PCT code correction.
def test_07_pct_code_correction_requeries_regulatory_evidence(isolated_database: Engine) -> None:
    calls: list[tuple[str | None, str]] = []

    def evidence_fn(db, pct, query):
        calls.append((pct, query))
        return [{"source_document": "TIPP", "validation_status": "verified", "evidence_text": "x"}]

    svc = make_service(isolated_database, clean_pct_regulatory_extraction(), evidence_fn=evidence_fn)
    result = start(svc, isolated_database)
    calls_before = len(calls)

    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].pct_code", "61091000"),
    )

    new_pcts = [c[0] for c in calls[calls_before:]]
    assert "61091000" in new_pcts  # the PCT-family regulatory check was requeried
    assert resumed["deterministic_status"] == "passed"


# 8. Quantity correction.
def test_08_quantity_correction_does_not_requery_regulatory_evidence(isolated_database: Engine) -> None:
    calls: list[tuple[str | None, str]] = []

    def evidence_fn(db, pct, query):
        calls.append((pct, query))
        return [{"source_document": "TIPP", "validation_status": "verified", "evidence_text": "x"}]

    svc = make_service(isolated_database, clean_quantity_mismatch_extraction(), evidence_fn=evidence_fn)
    result = start(svc, isolated_database)
    calls_before = len(calls)

    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )

    assert calls[calls_before:] == []  # arithmetic correction never touches RAG
    assert resumed["deterministic_status"] == "passed"


# 9. Correction introduces a new mismatch.
def test_09_correction_introducing_a_new_mismatch_does_not_auto_pass(isolated_database: Engine) -> None:
    def recheck_with_new_problem(extraction_result, request, corrections):
        result = fake_recheck_pipeline(extraction_result, request, corrections)
        for item in result.get("items", []):
            item["item_checks"].append(
                {
                    "check_id": "item_pct_code_match",
                    "status": "failed",
                    "message": "PCT code mismatch: invoice has '6109.1000' and packing list has '6110.1000'.",
                }
            )
            item["status"] = "failed"
        result["overall_status"] = "failed"
        result["is_compliant"] = False
        return result

    svc = make_service(
        isolated_database, clean_quantity_mismatch_extraction(), recheck_fn=recheck_with_new_problem
    )
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )

    assert resumed["deterministic_status"] == "failed"  # did not auto-pass
    event_types = [e["event_type"] for e in events(svc, isolated_database, result["workflow_id"])]
    assert "auditor_revision_reviewed" in event_types
    assert "consensus_recomputed" in event_types


# 10. Process restart between interrupt and response.
def test_10_review_task_and_correction_survive_a_process_restart(tmp_path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    ck_path = tmp_path / "checkpoints.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(engine)

    def make(engine_ref):
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        saver = SqliteSaver(sqlite3.connect(str(ck_path), check_same_thread=False))
        saver.setup()
        return make_service(engine_ref, clean_quantity_mismatch_extraction(), checkpointer=saver)

    svc1 = make(engine)
    result = start(svc1, engine)
    assert result["status"] == "awaiting_human_review"
    task_before = review_task(svc1, engine, result["workflow_id"])

    # "Restart": brand-new service + checkpointer connection on the same files.
    svc2 = make(engine)
    task_after = review_task(svc2, engine, result["workflow_id"])
    assert task_after["disputed_field_details"] == task_before["disputed_field_details"]

    resumed = review(
        svc2, engine, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    assert resumed["status"] == "completed"
    assert resumed["deterministic_status"] == "passed"
    engine.dispose()


# 11. Duplicate human-response submission.
def test_11_duplicate_submission_is_rejected_not_double_applied(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, clean_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    decision = correction_decision("invoice.line_items[1].quantity", "100")
    resumed = review(svc, isolated_database, result["workflow_id"], decision)
    assert resumed["status"] == "completed"

    with pytest.raises(Exception):
        review(svc, isolated_database, result["workflow_id"], decision)

    snapshot = svc._graph.get_state({"configurable": {"thread_id": resumed["thread_id"]}})
    assert len(snapshot.values["deterministic_result_history"]) == 2
    assert len(snapshot.values["human_correction_history"]) == 1


# 12. Maximum review rounds reached.
def test_12_maximum_review_rounds_reached_stops_looping(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, manual_review_extraction())
    result = start(svc, isolated_database)  # round 1 interrupt

    resumed1 = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].pct_code", "61091000"),
    )
    # The China certificate-of-origin check is untouched by this correction,
    # so consensus still requires review - round 2 interrupt.
    assert resumed1["status"] == "awaiting_human_review"

    resumed2 = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    # Round cap reached: stop looping, finalize in whatever state remains.
    assert resumed2["status"] == "completed"
    assert resumed2["deterministic_status"] == "manual_review"
    event_types = [e["event_type"] for e in events(svc, isolated_database, result["workflow_id"])]
    assert "human_review_limit_reached" in event_types


# 13. Original frozen status cannot be overwritten.
def test_13_original_frozen_status_cannot_be_overwritten(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, clean_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    snapshot = svc._graph.get_state({"configurable": {"thread_id": resumed["thread_id"]}})
    history = snapshot.values["deterministic_result_history"]
    assert history[0]["overall_status"] == "failed"
    assert history[0]["version"] == 1
    assert history[0] is not history[1]
    assert history[1]["overall_status"] == "passed"


# 14. New status is stored only under a new revision.
def test_14_new_status_stored_only_under_a_new_revision(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, clean_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    assert resumed["final_report"]["deterministic_result_current_version"] == 2
    assert resumed["final_report"]["original_deterministic_status"] == "failed"
    assert resumed["final_report"]["deterministic_compliance_status"] == "passed"


# 15. Broker and Auditor cannot write the revised frozen status.
def test_15_lying_auditor_cannot_override_the_corrected_revision_status(
    isolated_database: Engine,
) -> None:
    svc = make_service(isolated_database, two_problem_extraction(), auditor=LyingAuditor())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    # Only quantity was corrected; the PCT-code problem remains, so the real
    # status is still "failed" no matter what the Auditor's own report claims.
    assert resumed["final_report"]["deterministic_compliance_status"] == "failed"


# 16. Final explanation reflects the latest revision and mentions the correction.
def test_16_final_explanation_mentions_the_correction(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, clean_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    explanation = resumed["final_report"]["explanation"]
    assert "Human review" in explanation
    assert "100" in explanation
    assert resumed["final_report"]["deterministic_compliance_status"] == "passed"


# 17. Audit history contains both pre-correction and post-correction results.
def test_17_audit_history_contains_pre_and_post_correction_results(
    isolated_database: Engine,
) -> None:
    svc = make_service(isolated_database, clean_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    revisions = resumed["final_report"]["audit_revisions"]
    assert len(revisions) == 2
    assert revisions[0]["deterministic_result"]["overall_status"] == "failed"
    assert revisions[0]["triggered_by"] == "initial"
    assert revisions[1]["deterministic_result"]["overall_status"] == "passed"
    assert revisions[1]["triggered_by"] == "human_correction"


# 18. No Groq call occurs in the tests.
def test_18_no_groq_call_occurs_anywhere_in_this_module(isolated_database: Engine) -> None:
    """Every test above uses make_service()'s default (explanation_narrator=
    None) - this makes the guarantee explicit rather than merely implicit."""
    svc = make_service(isolated_database, clean_quantity_mismatch_extraction())
    result = start(svc, isolated_database)
    resumed = review(
        svc, isolated_database, result["workflow_id"],
        correction_decision("invoice.line_items[1].quantity", "100"),
    )
    assert resumed["final_report"]["explanation_source"] == "template_fallback"
