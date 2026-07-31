"""LangGraph StateGraph wiring for the customs-audit workflow.

    START
      -> load_shipment_documents
      -> broker_agent
      -> deterministic_compliance      (freezes the authoritative status)
      -> auditor_agent
      -> compare_agent_reports
           |-- (consensus, no critical anomaly) --> build_final_report
           |-- (disagreement/uncertainty/anomaly) --> prepare_human_review
                  -> interrupt_for_human_review   (pauses; builds nothing)
                  -> human_decision_received
                  |-- reject / accept / note / provide-document / reprocess:
                  |     -> resume_workflow -> build_final_report
                  |-- confirm_extracted_value / correct_extracted_value:
                        -> apply_human_correction
                             |-- validation failed --> build_final_report
                             |-- applied --> freeze_corrected_revision
                                          -> auditor_recheck_revision
                                          -> recompute_consensus_after_correction
                                               |-- still uncertain, rounds left --> prepare_human_review (loop)
                                               |-- resolved / rounds exhausted --> build_final_report
      -> generate_explanation           (one Groq call at most; template on failure)
      -> persist_audit_record
      -> END

Human review is not a bare accept/reject gate: a confirm/correct decision
changes *input data*, and Python recalculates the deterministic status from
that data - never the human, never an agent (see nodes.apply_human_correction
and dependency_map.py). Every revision this produces is additive: revision 1
is frozen once by deterministic_compliance and never edited again; a
correction only ever appends revision 2, 3, ... (see AuditRevision in
state.py). The Broker/Auditor rerun in the correction path is the exact same
independent-observation contract as the first pass - it can route to another
review, it can never set the status itself.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.services.customs_audit.deps import WorkflowDeps
from app.services.customs_audit.nodes import (
    make_nodes,
    route_after_apply_correction,
    route_after_compare,
    route_after_correction_consensus,
    route_human_action,
)
from app.services.customs_audit.state import CustomsAuditState


def build_customs_audit_graph(deps: WorkflowDeps, checkpointer: Any) -> Any:
    nodes = make_nodes(deps)
    graph = StateGraph(CustomsAuditState)
    for name, node in nodes.items():
        graph.add_node(name, node)  # type: ignore[call-overload]

    graph.add_edge(START, "load_shipment_documents")
    graph.add_edge("load_shipment_documents", "broker_agent")
    graph.add_edge("broker_agent", "deterministic_compliance")
    graph.add_edge("deterministic_compliance", "auditor_agent")
    graph.add_edge("auditor_agent", "compare_agent_reports")
    graph.add_conditional_edges(
        "compare_agent_reports",
        route_after_compare,
        {
            "prepare_human_review": "prepare_human_review",
            "build_final_report": "build_final_report",
        },
    )
    graph.add_edge("prepare_human_review", "interrupt_for_human_review")
    graph.add_edge("interrupt_for_human_review", "human_decision_received")
    graph.add_conditional_edges(
        "human_decision_received",
        route_human_action,
        {
            "apply_human_correction": "apply_human_correction",
            "resume_workflow": "resume_workflow",
        },
    )
    graph.add_edge("resume_workflow", "build_final_report")
    graph.add_conditional_edges(
        "apply_human_correction",
        route_after_apply_correction,
        {
            "freeze_corrected_revision": "freeze_corrected_revision",
            "build_final_report": "build_final_report",
        },
    )
    graph.add_edge("freeze_corrected_revision", "auditor_recheck_revision")
    graph.add_edge("auditor_recheck_revision", "recompute_consensus_after_correction")
    graph.add_conditional_edges(
        "recompute_consensus_after_correction",
        route_after_correction_consensus,
        {
            "prepare_human_review": "prepare_human_review",
            "build_final_report": "build_final_report",
        },
    )
    graph.add_edge("build_final_report", "generate_explanation")
    graph.add_edge("generate_explanation", "persist_audit_record")
    graph.add_edge("persist_audit_record", END)

    return graph.compile(checkpointer=checkpointer)


GRAPH_NODES = [
    "load_shipment_documents",
    "broker_agent",
    "deterministic_compliance",
    "auditor_agent",
    "compare_agent_reports",
    "prepare_human_review",
    "interrupt_for_human_review",
    "human_decision_received",
    "resume_workflow",
    "apply_human_correction",
    "freeze_corrected_revision",
    "auditor_recheck_revision",
    "recompute_consensus_after_correction",
    "build_final_report",
    "generate_explanation",
    "persist_audit_record",
]
