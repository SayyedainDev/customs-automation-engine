"""Historical shipment semantic search (Phase 9).

The vector store mirrors app/services/regulatory/vector_store.py's pattern
(local embeddings, JSON-list-of-floats storage, idempotent re-embed), applied
to a different domain: one row per finalized CustomsAuditWorkflow rather than
per regulatory legal-text chunk. All embeddings use the existing
FakeEmbeddingProvider test double - no model download, no Groq.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.models.customs_audit import CustomsAuditWorkflow
from app.models.shipment_search import ShipmentSummaryVector
from app.services.regulatory.embeddings import FakeEmbeddingProvider
from app.services.shipment_search.search import search_shipments
from app.services.shipment_search.summary import build_shipment_summary_text
from app.services.shipment_search.vector_store import index_shipment_summary
from tests.unit.test_customs_audit import make_service, passed_extraction, mismatch_extraction


def _workflow(
    engine: Engine,
    *,
    deterministic_status: str = "passed",
    requires_human_review: bool = False,
    exporter: str = "Acme Textiles",
    destination: str = "China",
    products: list[str] | None = None,
    pct_codes: list[str] | None = None,
    document_mismatches: list[str] | None = None,
) -> CustomsAuditWorkflow:
    workflow_id = uuid4()
    line_items = [
        {"product_name": name, "pct_code": pct}
        for name, pct in zip(
            products or ["Cotton knitted T-shirts"],
            pct_codes or ["61091000"],
        )
    ]
    final_report = {
        "deterministic_compliance_status": deterministic_status,
        "user_report": {
            "shipment_summary": {"exporter": exporter, "destination": destination},
            "line_items": line_items,
            "problems": {
                "document_mismatches": document_mismatches or [],
                "calculation_errors": [],
                "regulatory_problems": [],
            },
        },
    }
    workflow = CustomsAuditWorkflow(
        id=workflow_id,
        thread_id=f"thread-{workflow_id.hex[:8]}",
        status="completed",
        deterministic_status=deterministic_status,
        requires_human_review=requires_human_review,
        final_report=final_report,
    )
    with Session(engine) as db:
        db.add(workflow)
        db.commit()
        db.refresh(workflow)
        db.expunge(workflow)
    return workflow


def test_summary_text_includes_weight_and_quantity_discrepancies() -> None:
    workflow = CustomsAuditWorkflow(
        id=uuid4(),
        thread_id="t1",
        status="completed",
        deterministic_status="failed",
        requires_human_review=True,
        final_report={
            "deterministic_compliance_status": "failed",
            "user_report": {
                "shipment_summary": {"exporter": "Acme Textiles", "destination": "China"},
                "line_items": [{"product_name": "Cotton yarn", "pct_code": "52051100"}],
                "problems": {
                    "document_mismatches": [
                        "The invoice and packing-list net weight do not match.",
                        "The invoice and packing-list quantity do not match.",
                    ],
                    "calculation_errors": [],
                    "regulatory_problems": [],
                },
            },
        },
    )
    text = build_shipment_summary_text(workflow)
    assert "Exporter: Acme Textiles" in text
    assert "Cotton yarn" in text
    assert "52051100" in text
    assert "Weight discrepancies: The invoice and packing-list net weight" in text
    assert "Quantity discrepancies: The invoice and packing-list quantity" in text
    assert "Human review: required" in text


def test_indexing_is_idempotent_on_unchanged_content(isolated_database: Engine) -> None:
    workflow = _workflow(isolated_database)
    embedder = FakeEmbeddingProvider(dimension=8)

    with Session(isolated_database) as db:
        first = index_shipment_summary(db, workflow, embedder)
        assert first is not None
        count_after_first = len(list(db.execute(select(ShipmentSummaryVector)).scalars()))

        second = index_shipment_summary(db, workflow, embedder)
        assert second is None  # unchanged content: no-op
        count_after_second = len(list(db.execute(select(ShipmentSummaryVector)).scalars()))

    assert count_after_first == 1
    assert count_after_second == 1


def test_indexing_updates_when_content_changes(isolated_database: Engine) -> None:
    workflow = _workflow(isolated_database, deterministic_status="passed")
    embedder = FakeEmbeddingProvider(dimension=8)

    with Session(isolated_database) as db:
        index_shipment_summary(db, workflow, embedder)

    with Session(isolated_database) as db:
        refreshed = db.get(CustomsAuditWorkflow, workflow.id)
        assert refreshed is not None
        refreshed.final_report = {
            **(refreshed.final_report or {}),
            "deterministic_compliance_status": "failed",
        }
        db.add(refreshed)
        db.commit()

        updated = index_shipment_summary(db, refreshed, embedder)
        assert updated is not None
        assert "failed" in updated.summary_text


def test_search_ranks_the_matching_shipment_first(isolated_database: Engine) -> None:
    clean = _workflow(
        isolated_database,
        deterministic_status="passed",
        exporter="Clean Exports Ltd",
        products=["Denim fabric"],
        pct_codes=["52094200"],
    )
    weight_mismatch = _workflow(
        isolated_database,
        deterministic_status="failed",
        exporter="Problem Exports Ltd",
        products=["Cotton yarn"],
        pct_codes=["52051100"],
        document_mismatches=["The invoice and packing-list gross weight do not match."],
    )

    clean_text = build_shipment_summary_text(clean)
    mismatch_text = build_shipment_summary_text(weight_mismatch)
    embedder = FakeEmbeddingProvider(
        dimension=16,
        mapping={
            clean_text: [1.0, 0.0] + [0.0] * 14,
            mismatch_text: [0.0, 1.0] + [0.0] * 14,
            "shipments with a weight discrepancy": [0.0, 1.0] + [0.0] * 14,
        },
    )

    with Session(isolated_database) as db:
        index_shipment_summary(db, clean, embedder)
        index_shipment_summary(db, weight_mismatch, embedder)
        output = search_shipments(
            db, "shipments with a weight discrepancy", top_k=5, embedder=embedder
        )

    assert output.status == "ok"
    assert output.retrieval_mode == "semantic"
    assert output.results[0].workflow_id == str(weight_mismatch.id)


def test_no_shipments_indexed_reports_that_status(isolated_database: Engine) -> None:
    with Session(isolated_database) as db:
        output = search_shipments(db, "any query", embedder=FakeEmbeddingProvider())
    assert output.status == "no_shipments_indexed"
    assert output.results == []


def test_embedder_failure_falls_back_to_a_deterministic_list(isolated_database: Engine) -> None:
    workflow = _workflow(isolated_database)
    with Session(isolated_database) as db:
        index_shipment_summary(db, workflow, FakeEmbeddingProvider(dimension=8))

    class RaisingEmbedder(FakeEmbeddingProvider):
        def embed_query(self, text: str):
            raise RuntimeError("embedding backend unavailable")

    with Session(isolated_database) as db:
        output = search_shipments(db, "any query", embedder=RaisingEmbedder())

    assert output.status == "ok"
    assert output.retrieval_mode == "recency_fallback"
    assert len(output.results) == 1


def test_incompatible_query_and_index_vectors_fall_back_to_recency(
    isolated_database: Engine,
) -> None:
    workflow = _workflow(isolated_database)
    with Session(isolated_database) as db:
        index_shipment_summary(
            db,
            workflow,
            FakeEmbeddingProvider(dimension=384, model_name="real-model"),
        )
        output = search_shipments(
            db,
            "any query",
            embedder=FakeEmbeddingProvider(
                dimension=256,
                model_name="hashing-fallback",
            ),
        )

    assert output.status == "ok"
    assert output.retrieval_mode == "recency_fallback"
    assert len(output.results) == 1


def test_completing_a_workflow_produces_exactly_one_indexed_row(isolated_database: Engine) -> None:
    svc = make_service(isolated_database, passed_extraction())
    request = {
        "commercial_invoice_document_id": uuid4(),
        "packing_list_document_id": uuid4(),
        "additional_document_ids": [],
        "shipment_date": "2026-07-20",
        "letter_of_credit_date": None,
        "additional_uploaded_document_types": ["form_e", "certificate_of_origin"],
    }
    with Session(isolated_database) as db:
        result = asyncio.run(svc.start_workflow(db, request))
    assert result["status"] == "completed"

    with Session(isolated_database) as db:
        rows = list(db.execute(select(ShipmentSummaryVector)).scalars())
    assert len(rows) == 1
    assert rows[0].workflow_id == UUID(result["workflow_id"])
