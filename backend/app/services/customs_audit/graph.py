"""LangGraph StateGraph wiring for the customs-audit workflow.

    START
      -> load_shipment_documents
      -> broker_agent
      -> deterministic_compliance      (freezes the authoritative status)
      -> auditor_agent
      -> compare_agent_reports
           |-- (consensus, no critical anomaly) --> build_final_report
           |-- (disagreement/uncertainty/anomaly) --> interrupt_for_human_review
                  -> human_decision_received
                  -> resume_workflow
                  -> build_final_report
      -> generate_explanation           (one Groq call at most; template on failure)
      -> persist_audit_record
      -> END
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.services.customs_audit.deps import WorkflowDeps
from app.services.customs_audit.nodes import make_nodes, route_after_compare
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
            "interrupt_for_human_review": "interrupt_for_human_review",
            "build_final_report": "build_final_report",
        },
    )
    graph.add_edge("interrupt_for_human_review", "human_decision_received")
    graph.add_edge("human_decision_received", "resume_workflow")
    graph.add_edge("resume_workflow", "build_final_report")
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
    "interrupt_for_human_review",
    "human_decision_received",
    "resume_workflow",
    "build_final_report",
    "generate_explanation",
    "persist_audit_record",
]
