"""Regression tests for LangGraph workflow and RAG retrieval hardening."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID, uuid4

import numpy as np
import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.customs_audit import CustomsAuditWorkflow
from app.models.documents import DocumentUploadRecord
from app.models.regulatory import RegulatoryChunk, RegulatoryChunkVector
from app.models.shipment_chunks import ShipmentDocumentChunk
from app.models.shipment_search import ShipmentSummaryVector
from app.services.assistant.regulatory_chat import (
    _checklist_explanation_is_acceptable,
    _mentions_pct_in_text,
)
from app.services.assistant.shipment_assistant import answer_shipment_question
from app.services.assistant.shipment_indexer import (
    build_semantic_chunks_for_document,
    index_shipment_documents,
)
from app.services.assistant.shipment_retriever import ShipmentDocumentRetriever
from app.services.customs_audit.agents import (
    DeterministicAuditorAgent,
    DeterministicBrokerAgent,
)
from app.services.customs_audit.checkpointer import build_memory_checkpointer
from app.services.customs_audit.dependency_map import resolve_affected_checks
from app.services.customs_audit.deps import (
    EvidenceProvider,
    PipelineFn,
    RecheckFn,
    WorkflowDeps,
    build_default_deps,
)
from app.services.customs_audit.graph import build_customs_audit_graph
from app.services.customs_audit.workflow_service import CustomsAuditService
from app.services.customs_chat.knowledge.retriever import retrieve_evidence
from app.services.regulatory import ingestion as ingestion_module
from app.services.regulatory import reranker as reranker_module
from app.services.regulatory.chunking import split_by_sequential_numbered_headings
from app.services.regulatory.embeddings import (
    FakeEmbeddingProvider,
    reset_embedding_provider,
    set_embedding_provider,
)
from app.services.regulatory.explanation import _citations_from_grounded
from app.services.regulatory.ingestion import _split_parents, ingest_all
from app.services.regulatory.reranker import (
    CrossEncoderReranker,
    FakeReranker,
    LexicalReranker,
    get_reranker,
    reset_reranker,
    set_reranker,
)
from app.services.regulatory.retrieval import (
    ScoredEvidence,
    _lexical_relevance,
    _rank_map,
    search_regulatory_evidence,
    tokenize,
)
from app.services.regulatory.sources import RegulatorySource
from app.services.regulatory.vector_cache import (
    get_vector_matrix,
    reset_vector_matrix_cache,
)
from app.services.regulatory.vector_store import build_vector_index
from app.services.shipment_search.search import search_shipments
from app.services.shipment_search.vector_store import index_shipment_summary
from tests.unit.test_regulatory_retrieval import add_evidence


@pytest.fixture(autouse=True)
def _reset_rag_caches() -> Iterator[None]:
    reset_embedding_provider()
    reset_reranker()
    reset_vector_matrix_cache()
    yield
    reset_embedding_provider()
    reset_reranker()
    reset_vector_matrix_cache()


def _make_extraction_with_executable_rule(
    *,
    packing_quantity: str = "99",
    overall_status: str = "failed",
    item_quantity_status: str = "failed",
    supporting_documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "overall_status": overall_status,
        "is_compliant": overall_status == "passed",
        "rule_data_version": "sha256:testrules",
        "invoice": {
            "exporter_name": {"value": "Lahore Cotton Mills"},
            "buyer_name": {"value": "Beijing Buyers Ltd"},
            "invoice_number": {"value": "INV-900"},
            "currency": {"value": "USD"},
            "destination_country": {"value": "China"},
            "invoice_total": {"value": "550.00"},
            "line_items": [
                {
                    "item_index": 1,
                    "product_name": {"value": "Cotton knitted T-shirts"},
                    "pct_code": {"value": "6109.1000"},
                    "quantity": {"value": "100"},
                    "unit_price": {"value": "5.50"},
                    "line_total": {"value": "550.00"},
                    "net_weight": {"value": "75"},
                    "gross_weight": {"value": "80"},
                }
            ],
        },
        "packing_list": {
            "items": [
                {
                    "item_index": 1,
                    "product_name": {"value": "Cotton knitted T-shirts"},
                    "pct_code": {"value": "6109.1000"},
                    "quantity": {"value": packing_quantity},
                    "net_weight": {"value": "75"},
                    "gross_weight": {"value": "80"},
                }
            ]
        },
        "supporting_documents": supporting_documents or [],
        "shipment_level_checks": [],
        "items": [
            {
                "item_reference": "invoice_line_1",
                "invoice_item_index": 1,
                "packing_item_index": 1,
                "match_status": "matched",
                "product_name": "Cotton knitted T-shirts",
                "pct_code": "61091000",
                "status": overall_status,
                "item_checks": [
                    {
                        "check_id": "item_quantity_match",
                        "check_name": "Invoice and packing-list quantity match",
                        "status": item_quantity_status,
                        "message": "Quantity differs between invoice and packing list",
                    }
                ],
                "compliance": {
                    "supported_product": True,
                    "checks": [],
                    "executable_rule_checks": [
                        {
                            "check_id": "xr_coo_china",
                            "check_name": "Certificate of origin for China",
                            "status": "manual_review",
                            "message": "Confirm certificate of origin under CPFTA",
                            "pct_code": "61091000",
                            "required_document": "certificate_of_origin",
                            "source_document": "PSW/TIPP CPFTA guidance",
                            "sro_number": "2486(I)/2025",
                            "issuing_authority": "TDAP",
                            "source_page": 2,
                            "effective_date": "2022-04-22",
                        }
                    ],
                },
                "fields_requiring_manual_review": [],
            }
        ],
        "fields_requiring_manual_review": [],
    }


def _make_workflow_service(
    engine: Engine,
    *,
    run_pipeline: PipelineFn,
    evidence_provider: EvidenceProvider,
    recheck_pipeline: RecheckFn | None = None,
    human_review_required: bool = False,
) -> CustomsAuditService:
    deps = WorkflowDeps(
        session_factory=lambda: Session(engine),
        run_pipeline=run_pipeline,
        evidence_provider=evidence_provider,
        broker_agent=DeterministicBrokerAgent(),
        auditor_agent=DeterministicAuditorAgent(),
        human_review_required=human_review_required,
        rule_data_version_fn=lambda: "sha256:testrules",
        vector_index_version_fn=lambda _db: "vec-index-v1",
        recheck_pipeline=recheck_pipeline,
    )
    graph = build_customs_audit_graph(deps, build_memory_checkpointer())
    return CustomsAuditService(graph)


def test_langgraph_gathers_evidence_and_audits_executable_rule_checks(
    isolated_database: Engine,
) -> None:
    queried_queries: list[str] = []

    def fake_evidence(_db: Session, _pct: str | None, query: str) -> list[dict[str, Any]]:
        queried_queries.append(query)
        return [
            {
                "source_document": "PSW/TIPP CPFTA guidance",
                "source_document_id": "sha256:cpfta",
                "sro_number": "2486(I)/2025",
                "page_number": 2,
                "section": "conditional_certificate_of_origin",
                "issuing_authority": "TDAP",
                "validation_status": "verified",
                "evidence_text": "Certificate of origin is required for China under CPFTA.",
                "retrieval_score": 0.9,
                "rerank_score": 0.95,
            }
        ]

    extraction = _make_extraction_with_executable_rule()
    service = _make_workflow_service(
        isolated_database,
        run_pipeline=lambda _db, _req: extraction,
        evidence_provider=fake_evidence,
    )
    with Session(isolated_database) as db:
        started = asyncio.run(
            service.start_workflow(
                db,
                {
                    "commercial_invoice_document_id": str(uuid4()),
                    "packing_list_document_id": str(uuid4()),
                    "shipment_date": "2026-07-15",
                },
            )
        )
    state = service._snapshot(started["thread_id"]).values

    assert len(queried_queries) == 1
    assert "xr_coo_china" in state["regulatory_evidence_by_check"]
    assert state["regulatory_evidence_status_by_check"]["xr_coo_china"] == "evidence_verified"
    assert state["auditor_report"]["evidence_support_status"] == "supported"
    broker_check_ids = {
        c["check_id"] for c in state["broker_report"]["deterministic_check_results"]
    }
    assert "xr_coo_china" in broker_check_ids


def test_langgraph_recheck_preserves_reused_evidence_for_auditor_and_sros(
    isolated_database: Engine,
) -> None:
    calls: list[str] = []

    def fake_evidence(_db: Session, _pct: str | None, query: str) -> list[dict[str, Any]]:
        calls.append(query)
        return [
            {
                "source_document": "PSW/TIPP CPFTA guidance",
                "source_document_id": "sha256:cpfta",
                "sro_number": "2486(I)/2025",
                "page_number": 2,
                "section": "conditional_certificate_of_origin",
                "issuing_authority": "TDAP",
                "validation_status": "verified",
                "evidence_text": "Certificate of origin is required for China under CPFTA.",
                "retrieval_score": 0.9,
                "rerank_score": 0.95,
            }
        ]

    initial = _make_extraction_with_executable_rule(
        packing_quantity="99",
        overall_status="manual_review",
        item_quantity_status="manual_review",
    )
    initial["packing_list"]["items"][0]["quantity"]["validation_status"] = (
        "manual_review"
    )
    initial["fields_requiring_manual_review"] = ["packing_list.items[1].quantity"]
    corrected = _make_extraction_with_executable_rule(
        packing_quantity="100",
        overall_status="manual_review",
        item_quantity_status="passed",
    )
    service = _make_workflow_service(
        isolated_database,
        run_pipeline=lambda _db, _req: initial,
        evidence_provider=fake_evidence,
        recheck_pipeline=lambda _ext, _req, _corr: corrected,
        human_review_required=True,
    )
    with Session(isolated_database) as db:
        started = asyncio.run(
            service.start_workflow(
                db,
                {
                    "commercial_invoice_document_id": str(uuid4()),
                    "packing_list_document_id": str(uuid4()),
                    "shipment_date": "2026-07-15",
                },
            )
        )
        assert started["status"] == "awaiting_human_review"
        assert len(calls) == 1

        resumed = asyncio.run(
            service.submit_review(
                db,
                UUID(started["workflow_id"]),
                {
                    "action": "correct_extracted_value",
                    "reviewer_reference": "reviewer-1",
                    "corrections": [
                        {
                            "field_path": "packing_list.items[1].quantity",
                            "original_value": "99",
                            "corrected_value": "100",
                            "reviewer_reference": "reviewer-1",
                            "timestamp": "2026-07-15T00:00:00Z",
                            "reason": "Confirmed quantity 100 from packing list scan",
                        }
                    ],
                },
            )
        )
    resumed_state = service._snapshot(resumed["thread_id"]).values

    # Pure quantity fix must NOT re-query regulatory RAG, AND Auditor must still
    # see the reused evidence as supported (never falsely degraded to not_found).
    assert len(calls) == 1
    assert resumed_state["auditor_report"]["evidence_support_status"] == "supported"
    assert resumed_state["auditor_report"]["recommended_workflow_action"] != "evidence_missing"
    assert resumed_state["retrieved_evidence"]
    assert resumed_state["retrieved_evidence"][0]["sro_number"] == "2486(I)/2025"


def test_dependency_map_expands_legacy_and_executable_regulatory_checks() -> None:
    present = [
        "item_quantity_match",
        "xr_coo_china",
        "product_export_status",
        "raw_cotton_sbp_deposit",
        "certificate_of_origin_china",
        "required_document_form_e",
        "mvp_pct_support",
    ]
    pct_dep = resolve_affected_checks("invoice.line_items[1].pct_code")
    resolved = set(pct_dep.resolve_check_ids(present))
    assert {
        "xr_coo_china",
        "product_export_status",
        "raw_cotton_sbp_deposit",
        "certificate_of_origin_china",
        "required_document_form_e",
        "mvp_pct_support",
    }.issubset(resolved)
    assert "item_quantity_match" not in resolved

    qty_dep = resolve_affected_checks("invoice.line_items[1].quantity")
    qty_resolved = set(qty_dep.resolve_check_ids(present))
    assert "product_export_status" not in qty_resolved
    assert "required_document_form_e" not in qty_resolved


def test_workflow_sync_indexes_verified_supporting_documents(
    isolated_database: Engine,
) -> None:
    inv_id = uuid4()
    pack_id = uuid4()
    coo_id = uuid4()

    with Session(isolated_database) as db:
        for doc_id, name, text in (
            (inv_id, "inv.pdf", "Commercial Invoice INV-900 for 100 cotton shirts"),
            (pack_id, "pack.pdf", "Packing list net weight 75 kg gross weight 80 kg"),
            (
                coo_id,
                "coo.pdf",
                "Certificate of Origin issued by TDAP for China CPFTA shipment",
            ),
        ):
            db.add(
                DocumentUploadRecord(
                    id=doc_id,
                    original_filename=name,
                    stored_filename=name,
                    file_extension=".pdf",
                    mime_type="application/pdf",
                    size_bytes=200,
                    extracted_pages=[{"page_number": 1, "text": text}],
                )
            )
        db.commit()

    clean_extraction = {
        "overall_status": "passed",
        "is_compliant": True,
        "rule_data_version": "sha256:testrules",
        "invoice": {
            "exporter_name": {"value": "Lahore Cotton Mills"},
            "buyer_name": {"value": "Beijing Buyers Ltd"},
            "invoice_number": {"value": "INV-900"},
            "currency": {"value": "USD"},
            "destination_country": {"value": "China"},
            "invoice_total": {"value": "550.00"},
            "line_items": [
                {
                    "item_index": 1,
                    "product_name": {"value": "Cotton knitted T-shirts"},
                    "pct_code": {"value": "6109.1000"},
                    "quantity": {"value": "100"},
                    "unit_price": {"value": "5.50"},
                    "line_total": {"value": "550.00"},
                    "net_weight": {"value": "75"},
                    "gross_weight": {"value": "80"},
                }
            ],
        },
        "packing_list": {
            "items": [
                {
                    "item_index": 1,
                    "product_name": {"value": "Cotton knitted T-shirts"},
                    "pct_code": {"value": "6109.1000"},
                    "quantity": {"value": "100"},
                    "net_weight": {"value": "75"},
                    "gross_weight": {"value": "80"},
                }
            ]
        },
        "supporting_documents": [
            {
                "document_id": str(coo_id),
                "uploaded": True,
                "state": "shipment_matched",
                "canonical_document_type": "certificate_of_origin",
                "claimed_document_type": "certificate_of_origin",
                "detected_document_type": "certificate_of_origin",
                "extraction_confidence": 0.95,
                "source_page": 1,
                "content_status": "passed",
                "extraction": {
                    "document_number": {"value": "COO-55"},
                    "exporter_or_applicant": {"value": "Lahore Cotton Mills"},
                    "destination_country": {"value": "China"},
                    "issuing_authority": {"value": "TDAP"},
                },
                "checks": [],
            }
        ],
        "shipment_level_checks": [],
        "items": [
            {
                "item_reference": "invoice_line_1",
                "invoice_item_index": 1,
                "packing_item_index": 1,
                "match_status": "matched",
                "product_name": "Cotton knitted T-shirts",
                "pct_code": "61091000",
                "status": "passed",
                "item_checks": [],
                "compliance": {"supported_product": True, "checks": []},
                "fields_requiring_manual_review": [],
            }
        ],
        "fields_requiring_manual_review": [],
    }
    service = _make_workflow_service(
        isolated_database,
        run_pipeline=lambda _db, _req: clean_extraction,
        evidence_provider=lambda _db, _pct, _q: [],
    )
    with Session(isolated_database) as db:
        started = asyncio.run(
            service.start_workflow(
                db,
                {
                    "commercial_invoice_document_id": str(inv_id),
                    "packing_list_document_id": str(pack_id),
                    "shipment_date": "2026-07-15",
                },
            )
        )
    assert started["status"] == "completed"

    with Session(isolated_database) as db:
        indexed_doc_ids = set(
            db.execute(
                select(ShipmentDocumentChunk.document_id).where(
                    ShipmentDocumentChunk.shipment_id == inv_id,
                    ShipmentDocumentChunk.active.is_(True),
                )
            ).scalars()
        )
    assert {inv_id, pack_id, coo_id}.issubset(indexed_doc_ids)


def test_build_default_deps_handles_blank_groq_api_key(
    isolated_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("   "))
    monkeypatch.setattr(settings, "langgraph_enable_live_agents", False)

    deps = build_default_deps(lambda: Session(isolated_database))
    assert deps.explanation_narrator is None

    monkeypatch.setattr(settings, "langgraph_enable_live_agents", True)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        build_default_deps(lambda: Session(isolated_database))


def test_exact_pct_matching_rejects_cross_token_digit_concatenation() -> None:
    fake_chunk = RegulatoryChunk(
        chunk_id="c1",
        role="child",
        is_parent=False,
        chunk_index=0,
        source_document="Unrelated Notification",
        source_path="test.txt",
        document_checksum="sha256:x",
        document_type="export_policy_order",
        validation_status="verified",
        rule_data_version="v1",
        ingestion_version="v1",
        text="Notification 6203 applies to 4200 cartons of citrus fruit and reference 996203420011.",
        char_count=84,
    )
    score = _lexical_relevance(
        set(tokenize("PCT 62034200 cotton trousers")),
        fake_chunk,
        pct_code="62034200",
        destination_country=None,
    )
    assert score < 0.25
    assert _mentions_pct_in_text(fake_chunk, "62034200") is False

    real_chunk = RegulatoryChunk(
        chunk_id="c2",
        role="child",
        is_parent=False,
        chunk_index=0,
        source_document="Tariff",
        source_path="test.txt",
        document_checksum="sha256:y",
        document_type="tariff_schedule",
        validation_status="verified",
        rule_data_version="v1",
        ingestion_version="v1",
        text="Heading 6203.4200 covers men's woven cotton trousers.",
        char_count=53,
    )
    assert _mentions_pct_in_text(real_chunk, "62034200") is True


def test_superseded_chunks_excluded_by_default_and_available_historically(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        add_evidence(
            db,
            key="old_rule",
            source_document="Old Export Order 2016",
            parent_text="Raw cotton deposit rule under PCT 5201.0090 required 5 percent deposit.",
            pct_codes=["52010090"],
            validation_status="verified",
            document_type="export_policy_order",
        )
        for chunk in db.scalars(
            select(RegulatoryChunk).where(RegulatoryChunk.chunk_id.like("old_rule:%"))
        ):
            chunk.currency_status = "superseded"
        db.commit()

        current_out = search_regulatory_evidence(
            db,
            query="raw cotton deposit PCT 52010090",
            pct_code="52010090",
            embedder=FakeEmbeddingProvider(),
            reranker=LexicalReranker(),
        )
        historical_out = search_regulatory_evidence(
            db,
            query="what did the superseded 2016 raw cotton deposit rule say for PCT 52010090?",
            pct_code="52010090",
            embedder=FakeEmbeddingProvider(),
            reranker=LexicalReranker(),
        )
    assert current_out.status == "evidence_not_found"
    assert historical_out.status == "ok"
    assert historical_out.results[0].chunk.currency_status == "superseded"


def test_rank_map_gives_equal_competition_ranks_to_ties() -> None:
    ranks = _rank_map([0.8, 0.0, 0.0, 0.5, 0.0])
    assert ranks[0] == 1
    assert ranks[3] == 2
    assert ranks[1] == ranks[2] == ranks[4] == 3


def test_vector_cache_isolates_collections_and_invalidates_on_rebuild(
    isolated_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "regulatory_vector_collection", "collection_alpha")

    with Session(isolated_database) as db:
        add_evidence(
            db,
            key="alpha",
            source_document="Alpha Doc",
            parent_text="Alpha raw cotton security deposit rule.",
            pct_codes=["52010090"],
        )
        embedder_8 = FakeEmbeddingProvider(dimension=8, model_name="shared-model")
        build_vector_index(db, embedder_8)

        # Insert a foreign collection row AND a NaN row in the same table.
        db.add(
            RegulatoryChunkVector(
                chunk_id="foreign:p0:c0",
                parent_chunk_id="foreign:p0",
                collection="collection_beta",
                embedding=[1.0] * 8,
                embedding_model="shared-model",
                embedding_dim=8,
                document_checksum="sha256:foreign",
                index_version="vec-index-v1",
                meta={},
            )
        )
        db.add(
            RegulatoryChunkVector(
                chunk_id="nan_row:p0:c0",
                parent_chunk_id="nan_row:p0",
                collection="collection_alpha",
                embedding=[float("nan")] * 8,
                embedding_model="shared-model",
                embedding_dim=8,
                document_checksum="sha256:nan",
                index_version="vec-index-v1",
                meta={},
            )
        )
        db.commit()

        matrix = get_vector_matrix(db, embedding_model="shared-model", dimension=8)
        assert "alpha:p0:c0" in matrix.chunk_ids
        assert "foreign:p0:c0" not in matrix.chunk_ids
        assert "nan_row:p0:c0" not in matrix.chunk_ids

        # Rebuilding with a new dimension under the same model name must update
        # existing vectors AND invalidate the vector cache automatically.
        embedder_16 = FakeEmbeddingProvider(dimension=16, model_name="shared-model")
        report = build_vector_index(db, embedder_16)
        assert report.chunks_updated >= 1
        matrix_16 = get_vector_matrix(db, embedding_model="shared-model", dimension=16)
        assert matrix_16.dimension == 16
        assert "alpha:p0:c0" in matrix_16.chunk_ids


def test_ingest_all_savepoint_preserves_earlier_sources_on_later_error(
    isolated_database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good_file = tmp_path / "good.txt"
    good_file.write_text("SRO 2486(I)/2025 raw cotton deposit rule.", encoding="utf-8")
    missing_file = tmp_path / "does_not_exist.txt"

    monkeypatch.setattr(ingestion_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        ingestion_module,
        "discover_sources",
        lambda: [
            RegulatorySource(
                key="good_source",
                kind="text_file",
                path=good_file,
                source_document="Good Source",
                issuing_authority="FBR",
                document_type="export_policy_amendment",
                validation_status="verified",
            ),
            RegulatorySource(
                key="broken_source",
                kind="text_file",
                path=missing_file,
                source_document="Missing Source",
                issuing_authority="FBR",
                document_type="export_policy_amendment",
                validation_status="verified",
            ),
        ],
    )

    with Session(isolated_database) as db:
        report = ingest_all(db, rule_data_version="sha256:test")
        persisted_chunks = list(db.scalars(select(RegulatoryChunk)))

    assert report.documents_ingested == 1
    assert report.documents_errored == 1
    assert len(persisted_chunks) >= 2
    assert all(c.source_document == "Good Source" for c in persisted_chunks)


def test_section_aware_ingestion_retains_substantive_preamble_continuation(
    tmp_path: Path,
) -> None:
    page_text = (
        "THE CUSTOMS ACT,1969\n\n"
        "39\n\n"
        "The exporter shall retain all export invoices and bills of lading for five years "
        "and produce them upon inspection by an officer of customs.\n\n"
        "26. Conducting the audit.- The appropriate officer may conduct an audit of records.\n"
    )
    split = split_by_sequential_numbered_headings(
        page_text, label_prefix="Section", start_last_number=25
    )
    assert [label for label, _ in split.sections] == ["Section 26"]
    assert "retain all export invoices" in split.preamble

    source = RegulatorySource(
        key="act",
        kind="pdf_pages",
        path=tmp_path / "act.pdf",
        source_document="Customs Act, 1969",
        issuing_authority="FBR",
        document_type="customs_act",
        validation_status="verified",
        chunk_strategy="section_aware",
    )
    parents, last_num = _split_parents(source, page_text, last_section_number=25)
    assert last_num == 26
    assert len(parents) == 2
    assert parents[0][1] == "Section 25 (continued)"
    assert "retain all export invoices" in parents[0][0]
    assert parents[1][1] == "Section 26"


def test_cross_encoder_handles_0d_scalar_and_logs_init_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _ScalarModel:
        def predict(
            self, _pairs: list[list[str]], convert_to_numpy: bool = True
        ) -> np.ndarray:
            return np.asarray(1.75, dtype=np.float32)

    reranker = object.__new__(CrossEncoderReranker)
    reranker._model = _ScalarModel()
    reranker.model_name = "scalar-test"
    assert reranker.score("cotton", ["cotton yarn"]) == [pytest.approx(1.75)]

    def fail_init(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("cross-encoder offline")

    monkeypatch.setattr(reranker_module, "CrossEncoderReranker", fail_init)
    monkeypatch.setattr(get_settings(), "regulatory_enable_real_models", True)
    reset_reranker()
    fallback = get_reranker()
    assert fallback.degraded is True
    assert any(
        record.name == "app.services.regulatory.reranker" and record.exc_info is not None
        for record in caplog.records
    )


def test_citations_from_grounded_deduplicates_when_parent_chunk_id_is_none() -> None:
    chunk = RegulatoryChunk(
        chunk_id="standalone:1",
        parent_chunk_id=None,
        role="child",
        is_parent=False,
        chunk_index=0,
        source_document="Export Policy Order",
        source_path="epo.pdf",
        document_checksum="sha256:epo",
        document_type="export_policy_order",
        validation_status="verified",
        rule_data_version="v1",
        ingestion_version="v1",
        text="Raw cotton requires deposit.",
        char_count=28,
    )
    item = ScoredEvidence(
        chunk=chunk,
        parent=chunk,
        bm25_score=1.0,
        bm25_rank=1,
        dense_score=1.0,
        dense_rank=1,
        rrf_score=0.03,
        rrf_rank=1,
        cross_encoder_score=1.0,
        cross_encoder_rank=1,
    )
    citations = _citations_from_grounded([item, item])
    assert len(citations) == 1


def test_shipment_retriever_gates_before_top_k_and_supports_canonical_form_e(
    isolated_database: Engine,
) -> None:
    shipment_id = uuid4()
    doc_inv = uuid4()
    doc_fe = uuid4()

    set_embedding_provider(FakeEmbeddingProvider(dimension=8, model_name="new-model"))
    set_reranker(
        FakeReranker(
            mapping={
                "Invoice says Form-E reference is pending": 10.0,
                "Form-E declaration FE-7788 issued by bank for export": 5.0,
            }
        )
    )

    with Session(isolated_database) as db:
        # Rank #1 chunk is wrong doc type ("commercial_invoice") for a "form-e"
        # query and must be rejected without starving rank #2 (canonical type
        # "form_e_or_psw_export_declaration") when top_k=1!
        db.add_all(
            [
                ShipmentDocumentChunk(
                    id=uuid4(),
                    shipment_id=shipment_id,
                    document_id=doc_inv,
                    document_type="commercial_invoice",
                    document_name="inv.pdf",
                    page_number=1,
                    section="general_content",
                    is_parent=False,
                    content_hash="h1",
                    active=True,
                    text="Invoice says Form-E reference is pending",
                    search_text="invoice says form-e reference is pending",
                    embedding=[1.0] * 8,
                    embedding_model="old-incompatible-model",
                    embedding_dim=8,
                ),
                ShipmentDocumentChunk(
                    id=uuid4(),
                    shipment_id=shipment_id,
                    document_id=doc_fe,
                    document_type="form_e_or_psw_export_declaration",
                    document_name="form_e.pdf",
                    page_number=1,
                    section="declaration_identity",
                    is_parent=False,
                    content_hash="h2",
                    active=True,
                    text="Form-E declaration FE-7788 issued by bank for export",
                    search_text="form-e declaration fe-7788 issued by bank for export",
                    embedding=[0.5] * 8,
                    embedding_model="old-incompatible-model",
                    embedding_dim=8,
                ),
            ]
        )
        db.commit()

        results = ShipmentDocumentRetriever(db).retrieve(
            shipment_id, "What is the Form-E declaration number?", top_k=1
        )
    assert len(results) == 1
    assert results[0].document_type == "form_e_or_psw_export_declaration"
    assert "FE-7788" in results[0].text


def test_shipment_indexer_uses_ocr_pages_and_refreshes_on_model_switch(
    isolated_database: Engine,
) -> None:
    shipment_id = uuid4()
    doc_id = uuid4()

    set_embedding_provider(FakeEmbeddingProvider(dimension=8, model_name="model-v1"))
    with Session(isolated_database) as db:
        doc = DocumentUploadRecord(
            id=doc_id,
            original_filename="scanned_form_e.pdf",
            stored_filename="scanned_form_e.pdf",
            file_extension=".pdf",
            mime_type="application/pdf",
            size_bytes=512,
            extracted_pages=[{"page_number": 1, "text": ""}],
            ocr_pages=[{"page_number": 1, "text": "Form-E Declaration No FE-9001 Export"}],
        )
        db.add(doc)
        db.commit()

        chunks_preview = build_semantic_chunks_for_document(
            doc, shipment_id, document_type="form_e_or_psw_export_declaration"
        )
        assert chunks_preview
        assert chunks_preview[0].section == "declaration_identity"

        index_shipment_documents(
            db, shipment_id, [(doc_id, "form_e_or_psw_export_declaration")]
        )
        v1_active = list(
            db.scalars(
                select(ShipmentDocumentChunk).where(
                    ShipmentDocumentChunk.shipment_id == shipment_id,
                    ShipmentDocumentChunk.active.is_(True),
                )
            )
        )
        assert v1_active and all(c.embedding_model == "model-v1" for c in v1_active)

        # Switching active embedder must deactivate stale chunks and re-index.
        set_embedding_provider(FakeEmbeddingProvider(dimension=16, model_name="model-v2"))
        index_shipment_documents(
            db, shipment_id, [(doc_id, "form_e_or_psw_export_declaration")]
        )
        v2_active = list(
            db.scalars(
                select(ShipmentDocumentChunk).where(
                    ShipmentDocumentChunk.shipment_id == shipment_id,
                    ShipmentDocumentChunk.active.is_(True),
                )
            )
        )
        assert v2_active
        assert all(
            c.embedding_model == "model-v2" and c.embedding_dim == 16 for c in v2_active
        )


def test_shipment_assistant_finds_chunks_indexed_under_invoice_doc_id(
    isolated_database: Engine,
) -> None:
    workflow_id = uuid4()
    invoice_doc_id = uuid4()

    with Session(isolated_database) as db:
        db.add(
            CustomsAuditWorkflow(
                id=workflow_id,
                thread_id="thread-alias",
                status="completed",
                deterministic_status="passed",
                invoice_document_id=invoice_doc_id,
            )
        )
        db.add(
            DocumentUploadRecord(
                id=invoice_doc_id,
                original_filename="inv.pdf",
                stored_filename="inv.pdf",
                file_extension=".pdf",
                mime_type="application/pdf",
                size_bytes=200,
                extracted_pages=[
                    {
                        "page_number": 1,
                        "text": "Packaging details: 50 export cartons strapped on wooden pallets.",
                    }
                ],
            )
        )
        db.commit()
        # Production workflow_service indexes chunks under workflow.invoice_document_id.
        index_shipment_documents(
            db, invoice_doc_id, [(invoice_doc_id, "commercial_invoice")]
        )

        # Querying by workflow_id must still retrieve those indexed chunks.
        resp = answer_shipment_question(
            db, workflow_id, "What does the document say about cartons and pallets?"
        )
    assert "50 export cartons" in resp.answer
    assert any(s.source_kind == "shipment_document" for s in resp.sources)


def test_regulatory_chat_checklist_validator_fixed_regex() -> None:
    allowed = ["Commercial Invoice", "Packing List", "Form-E / PSW export declaration"]
    good = (
        "Here are the documents you need for exporting cotton yarn from Pakistan: "
        "Commercial Invoice, Packing List, and Form-E."
    )
    exhaustive_all = (
        "These are all the documents you need for exporting cotton yarn from Pakistan "
        "before customs submission."
    )
    exhaustive_only = (
        "Commercial Invoice and Packing List are the only documents required for "
        "exporting cotton yarn from Pakistan."
    )
    assert _checklist_explanation_is_acceptable(good, allowed) is True
    assert _checklist_explanation_is_acceptable(exhaustive_all, allowed) is False
    assert _checklist_explanation_is_acceptable(exhaustive_only, allowed) is False


def test_customs_chat_retriever_filters_low_relevance_noise_and_breaks_ties(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        add_evidence(
            db,
            key="relevant",
            source_document="PSW Export Manual",
            parent_text="Form E submission requirements for export clearance through PSW.",
            pct_codes=[],
        )
        add_evidence(
            db,
            key="noise",
            source_document="Unrelated Schedule",
            parent_text="Agricultural tractor spare parts import valuation annex.",
            pct_codes=[],
        )
        set_embedding_provider(FakeEmbeddingProvider())
        set_reranker(LexicalReranker())

        evidence = retrieve_evidence(
            db,
            search_queries=("Form E submission requirements",),
            top_k=5,
            tau_high=0.45,
            tau_low=0.25,
        )
    assert evidence.band in ("STRONG", "WEAK")
    titles = [c.document for c in evidence.citations]
    assert "PSW Export Manual" in titles
    assert "Unrelated Schedule" not in titles


def test_shipment_search_normalizes_vectors_and_reembeds_on_dim_change(
    isolated_database: Engine,
) -> None:
    wf_match = uuid4()
    wf_other = uuid4()

    with Session(isolated_database) as db:
        # Insert two unnormalized vectors: wf_other has huge magnitude but worse angle,
        # wf_match is perfectly aligned with [1, 0, 0]. Cosine normalization must rank
        # wf_match ahead of wf_other.
        db.add_all(
            [
                ShipmentSummaryVector(
                    workflow_id=wf_match,
                    summary_text="Weight discrepancy shipment",
                    embedding=[1.0, 0.0, 0.0],
                    embedding_model="norm-test",
                    embedding_dim=3,
                    summary_checksum="s1",
                    index_version="shipment-vec-index-v1",
                    meta={},
                ),
                ShipmentSummaryVector(
                    workflow_id=wf_other,
                    summary_text="Clean shipment",
                    embedding=[50.0, 100.0, 0.0],
                    embedding_model="norm-test",
                    embedding_dim=3,
                    summary_checksum="s2",
                    index_version="shipment-vec-index-v1",
                    meta={},
                ),
            ]
        )
        db.commit()

        embedder = FakeEmbeddingProvider(
            dimension=3,
            mapping={"weight discrepancy": [1.0, 0.0, 0.0]},
            model_name="norm-test",
        )
        out = search_shipments(db, "weight discrepancy", top_k=2, embedder=embedder)
        assert out.results[0].workflow_id == str(wf_match)
        assert out.results[0].score == pytest.approx(1.0)
        assert out.results[1].score < 0.5

        empty_out = search_shipments(db, "weight discrepancy", top_k=0, embedder=embedder)
        assert empty_out.results == []

        # Dimension change under the same model name must trigger re-embedding.
        wf = CustomsAuditWorkflow(
            id=wf_match,
            thread_id="t-dim",
            status="completed",
            deterministic_status="passed",
            final_report={"user_report": {}},
        )
        db.add(wf)
        db.commit()
        index_shipment_summary(
            db,
            wf,
            embedder=FakeEmbeddingProvider(dimension=3, model_name="dim-switch"),
        )
        updated = index_shipment_summary(
            db,
            wf,
            embedder=FakeEmbeddingProvider(dimension=6, model_name="dim-switch"),
        )
        assert updated is not None
        assert updated.embedding_dim == 6
