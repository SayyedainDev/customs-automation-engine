"""Scenario evidence for historical shipment summary indexing and semantic search."""

from __future__ import annotations

from uuid import UUID, uuid4

from app.models.customs_audit import CustomsAuditWorkflow
from app.services.customs_audit.state import WorkflowStatus
from app.services.regulatory.embeddings import FakeEmbeddingProvider
from app.services.shipment_search.search import search_shipments
from app.services.shipment_search.vector_store import index_shipment_summary
from cace_testing.harnesses.database import CaceRuntime


def _seed_completed_workflow(
    runtime: CaceRuntime,
    *,
    exporter: str,
    product: str,
    pct_code: str,
    mismatches: list[str],
    embedder: FakeEmbeddingProvider,
) -> UUID:
    workflow_id = uuid4()
    workflow = CustomsAuditWorkflow(
        id=workflow_id,
        thread_id=f"thread-{workflow_id.hex[:8]}",
        status=WorkflowStatus.COMPLETED.value,
        deterministic_status="failed" if mismatches else "passed",
        requires_human_review=bool(mismatches),
        final_report={
            "deterministic_compliance_status": "failed" if mismatches else "passed",
            "user_report": {
                "shipment_summary": {"exporter": exporter, "destination": "China"},
                "line_items": [{"product_name": product, "pct_code": pct_code}],
                "problems": {
                    "document_mismatches": mismatches,
                    "calculation_errors": [],
                    "regulatory_problems": [],
                },
            },
        },
    )
    with runtime.open_session() as db:
        db.add(workflow)
        db.commit()
        db.refresh(workflow)
        index_shipment_summary(db, workflow, embedder=embedder)
    return workflow_id


def test_shipment_search_handles_empty_semantic_and_recency_fallback(
    cace_runtime: CaceRuntime,
) -> None:
    with cace_runtime.open_session() as db:
        empty = search_shipments(db, "weight discrepancy")
        assert empty.status == "no_shipments_indexed"
        assert empty.results == []

    indexed_embedder = FakeEmbeddingProvider(dimension=8)
    match_workflow_id = _seed_completed_workflow(
        cace_runtime,
        exporter="Alpha Spinning Mills",
        product="Cotton yarn",
        pct_code="52051100",
        mismatches=["The invoice and packing-list net weight do not match."],
        embedder=indexed_embedder,
    )
    _seed_completed_workflow(
        cace_runtime,
        exporter="Beta Knitwear",
        product="Cotton T-shirts",
        pct_code="61091000",
        mismatches=[],
        embedder=indexed_embedder,
    )

    with cace_runtime.open_session() as db:
        semantic = search_shipments(
            db,
            "cotton yarn net weight mismatch",
            embedder=indexed_embedder,
        )
        assert semantic.status == "ok"
        assert semantic.retrieval_mode in {"semantic", "semantic_degraded"}
        assert {item.workflow_id for item in semantic.results} >= {
            str(match_workflow_id)
        }

        incompatible_embedder = FakeEmbeddingProvider(
            dimension=16,
            model_name="other-embedding-model",
        )
        fallback = search_shipments(
            db,
            "cotton",
            embedder=incompatible_embedder,
        )
        assert fallback.status == "ok"
        assert fallback.retrieval_mode == "recency_fallback"
        assert len(fallback.results) == 2
