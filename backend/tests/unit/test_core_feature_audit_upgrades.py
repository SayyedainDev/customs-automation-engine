"""Unit and API tests verifying the Core Feature Audit upgrades:
1. SKU / Style-Code multi-line item matching (`ItemMatchStrategy.SKU_CODE`).
2. Exact Decimal 2026-27 Pakistan Customs cascading duty & tax calculator (`duty_calculator.py` & `/api/v1/compliance/calculate-duties`).
3. Precomputed revision cache reuse between `/check-documents/multi-line` and `/customs-audit/workflows`.
4. Real-document `provide_missing_document` human-review workflow resumption (while preserving rejection of un-uploaded UUIDs).
5. Model Context Protocol (`FastMCP`) server tools (`app/mcp_server.py`).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.main import app
from app.mcp_server import (
    calculate_pakistan_customs_duties,
    get_textile_export_checklist,
    lookup_pakistan_tariff,
    mcp,
)
from app.models.documents import DocumentUploadRecord
from app.models.tariff import TariffLine
from app.schemas.multi_line_extraction import (
    InvoiceLineItem,
    ItemMatchStatus,
    ItemMatchStrategy,
    MultiLineShipmentRequest,
    PackingListItem,
)
from app.schemas.shipment_extraction import (
    ExtractedField,
    ExtractionMethod,
    FieldValidationStatus,
)
from app.services.compliance.duty_calculator import calculate_cascading_customs_taxes
from app.services.customs_audit.deps import default_run_pipeline
from app.services.multi_line.item_matching import match_line_items
from app.services.multi_line_shipment_service import (
    _REVISION_PIPELINE_CACHE,
    cache_multi_line_response,
    get_cached_multi_line_response,
)
from tests.unit.test_customs_audit import (
    make_service,
    manual_review_extraction,
    passed_extraction,
    review,
    start,
)

client = TestClient(app)


def _ef(val):
    return ExtractedField(
        value=val,
        source_document_id=uuid4(),
        source_page=1,
        extraction_method=ExtractionMethod.REGEX_LABEL,
        confidence=Decimal("0.99"),
        validation_status=FieldValidationStatus.VERIFIED,
        validation_note="ok",
    )


def test_sku_matching_disambiguates_same_pct_lines_with_different_line_numbers() -> None:
    """When two lines share the same PCT code and have re-ordered line numbers
    between Invoice and Packing List, explicit SKU/Style codes match them 1-to-1."""
    inv_1 = InvoiceLineItem(
        item_index=1,
        line_number=_ef(1),
        product_name=_ef("Men's Cotton T-Shirt SKU: ST-101-BLK"),
        pct_code=_ef("6109.1000"),
        quantity=_ef(Decimal("100")),
        unit=_ef("PCS"),
        unit_price=_ef(Decimal("5.00")),
        line_total=_ef(Decimal("500.00")),
        net_weight=_ef(Decimal("20")),
        gross_weight=_ef(Decimal("22")),
        item_confidence=Decimal("0.99"),
        item_validation_status=FieldValidationStatus.VERIFIED,
        item_note="ok",
    )
    inv_2 = InvoiceLineItem(
        item_index=2,
        line_number=_ef(2),
        product_name=_ef("Men's Cotton T-Shirt SKU: ST-102-WHT"),
        pct_code=_ef("6109.1000"),
        quantity=_ef(Decimal("200")),
        unit=_ef("PCS"),
        unit_price=_ef(Decimal("5.00")),
        line_total=_ef(Decimal("1000.00")),
        net_weight=_ef(Decimal("40")),
        gross_weight=_ef(Decimal("44")),
        item_confidence=Decimal("0.99"),
        item_validation_status=FieldValidationStatus.VERIFIED,
        item_note="ok",
    )
    # Packing list has no line numbers (or swapped order) but carries matching SKU codes:
    pkg_1 = PackingListItem(
        item_index=1,
        line_number=_ef(None),
        product_name=_ef("Cotton knitted T-shirt Style #ST-102-WHT"),
        pct_code=_ef("6109.1000"),
        quantity=_ef(Decimal("200")),
        unit=_ef("PCS"),
        net_weight=_ef(Decimal("40")),
        gross_weight=_ef(Decimal("44")),
        package_count=_ef(10),
        item_confidence=Decimal("0.99"),
        item_validation_status=FieldValidationStatus.VERIFIED,
        item_note="ok",
    )
    pkg_2 = PackingListItem(
        item_index=2,
        line_number=_ef(None),
        product_name=_ef("Cotton knitted T-shirt Style #ST-101-BLK"),
        pct_code=_ef("6109.1000"),
        quantity=_ef(Decimal("100")),
        unit=_ef("PCS"),
        net_weight=_ef(Decimal("20")),
        gross_weight=_ef(Decimal("22")),
        package_count=_ef(5),
        item_confidence=Decimal("0.99"),
        item_validation_status=FieldValidationStatus.VERIFIED,
        item_note="ok",
    )

    matches = match_line_items([inv_1, inv_2], [pkg_1, pkg_2])
    assert len(matches) == 2
    assert all(m.status == ItemMatchStatus.MATCHED for m in matches)
    assert all(m.strategy == ItemMatchStrategy.SKU_CODE for m in matches)
    assert matches[0].invoice_item == inv_1 and matches[0].packing_item == pkg_2
    assert matches[1].invoice_item == inv_2 and matches[1].packing_item == pkg_1


def test_cascading_customs_duty_calculator_uses_exact_decimal_compounding() -> None:
    """Verify the 2026-27 Pakistan Customs compounding tax sequence:
    V=1000.00, CD=20% (200), ACD=6% (60), RD=10% (100) -> DPV=1360.00
    ST=18% of 1360 (244.80), VAT=3% of 1360 (40.80) -> IT Base=1645.60
    WHT=5.5% of 1645.60 (90.51) -> Total Taxes=736.11, Landed=1736.11.
    """
    b = calculate_cascading_customs_taxes(
        assessed_value=Decimal("1000.00"),
        customs_duty_pct=Decimal("20.00"),
        regulatory_duty_pct=Decimal("10.00"),
        sales_tax_pct=Decimal("18.00"),
        value_addition_tax_pct=Decimal("3.00"),
        advance_income_tax_pct=Decimal("5.50"),
    )
    assert b.customs_duty == Decimal("200.00")
    assert b.additional_customs_duty == Decimal("60.00")  # 6% slab for CD >= 20%
    assert b.regulatory_duty == Decimal("100.00")
    assert b.sales_tax_base == Decimal("1360.00")
    assert b.sales_tax == Decimal("244.80")
    assert b.value_addition_tax == Decimal("40.80")
    assert b.income_tax_base == Decimal("1645.60")
    assert b.advance_income_tax == Decimal("90.51")
    assert b.total_duties_and_taxes == Decimal("736.11")
    assert b.total_landed_cost == Decimal("1736.11")
    assert b.effective_rate_pct == Decimal("73.61")

    # Also test the FastAPI route:
    resp = client.post(
        "/api/v1/compliance/calculate-duties",
        json={
            "hs_code": "62034200",
            "assessed_value": "1000.00",
            "customs_duty_pct": "20.00",
            "regulatory_duty_pct": "10.00",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["hs_code"] == "62034200"
    assert data["breakdown"]["total_duties_and_taxes"] == "736.11"
    assert len(data["formula_steps"]) == 12


def test_provide_missing_document_succeeds_for_real_uploaded_document_and_recomputes(
    isolated_database: Engine,
) -> None:
    """When a real DocumentUploadRecord exists in PostgreSQL, submitting
    provide_missing_document attaches the document, re-runs the pipeline, and
    creates Revision 2."""
    doc_id = uuid4()
    with Session(isolated_database) as db:
        db.add(
            DocumentUploadRecord(
                id=doc_id,
                original_filename="form-e.pdf",
                stored_filename=f"{doc_id}.pdf",
                file_extension=".pdf",
                mime_type="application/pdf",
                size_bytes=2048,
                status="extracted",
            )
        )
        db.commit()

    calls = {"count": 0}

    def dynamic_extraction():
        calls["count"] += 1
        if calls["count"] == 1:
            return manual_review_extraction()
        return passed_extraction()

    svc = make_service(isolated_database, dynamic_extraction)
    started = start(svc, isolated_database)
    assert started["status"] == "awaiting_human_review"

    resumed = review(
        svc,
        isolated_database,
        started["workflow_id"],
        {
            "action": "provide_missing_document",
            "corrections": [],
            "provided_document_ids": [str(doc_id)],
            "reviewer_reference": "supervisor-001",
            "source": "form_e_or_psw_export_declaration",
            "review_note": "Uploaded Form-E PDF.",
        },
    )
    assert resumed["status"] == "completed"
    assert resumed["deterministic_status"] == "passed"
    assert resumed["final_report"]["deterministic_result_current_version"] == 2


def test_list_workflows_endpoint_returns_persisted_workflows(
    isolated_database: Engine,
) -> None:
    svc = make_service(isolated_database, passed_extraction())
    started = start(svc, isolated_database)

    with Session(isolated_database) as db:
        listed = svc.list_workflows(db, limit=10)
        evts = svc.get_events(db, __import__("uuid").UUID(started["workflow_id"]))
    assert any(w["workflow_id"] == started["workflow_id"] for w in listed)
    assert len(evts) >= 4
    assert evts[0]["previous_state_hash"] == "sha256:genesis"
    for idx in range(1, len(evts)):
        assert evts[idx]["previous_state_hash"] == evts[idx - 1]["new_state_hash"]
        assert str(evts[idx]["new_state_hash"]).startswith("sha256:")


def test_mcp_server_exposes_and_executes_deterministic_tools(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        db.add(
            TariffLine(
                hs_code="61091000",
                description="T-shirts, singlets and other vests, knitted or crocheted - Of cotton",
                chapter="61",
                heading="6109",
                customs_duty_pct=20.0,
                tariff_year="FY2025-26",
                source_page=155,
                needs_review=False,
            )
        )
        db.commit()

    duty_res = calculate_pakistan_customs_duties(
        assessed_value="100.00", customs_duty_pct="20.00"
    )
    assert duty_res["breakdown"]["customs_duty"] == "20.00"
    assert duty_res["breakdown"]["additional_customs_duty"] == "6.00"

    tariff_res = lookup_pakistan_tariff("61091000")
    assert tariff_res["matched"] is True
    assert tariff_res["cascading_tax_breakdown"] is not None

    checklist_res = get_textile_export_checklist(
        "What documents do I need to export cotton t-shirts to Germany?",
        destination="Germany",
    )
    assert checklist_res["matched"] is True
    assert checklist_res["pct_code"] == "61091000"


def test_shipment_assistant_suggested_questions_use_grammatical_verb_not_completed(
    isolated_database: Engine,
) -> None:
    from app.models.customs_audit import CustomsAuditWorkflow
    from app.services.assistant.shipment_assistant import answer_shipment_question

    wf_id = uuid4()
    with Session(isolated_database) as db:
        db.add(
            CustomsAuditWorkflow(
                id=wf_id,
                thread_id="thread-gram-test",
                status="completed",
                deterministic_status="failed",
            )
        )
        db.commit()
        resp = answer_shipment_question(db, wf_id, "What is the audit status?")
    assert "Why did this shipment fail?" in resp.suggested_questions
    assert "Why did this shipment completed?" not in resp.suggested_questions


def test_structured_and_shipment_extraction_routes_surface_429_retry_after(
    monkeypatch,
) -> None:
    from app.api.routes import shipment_extraction as se_route
    from app.api.routes import structured_extraction as st_route
    from app.core.exceptions import StructuredExtractionRateLimitedError

    def _raise_429(*_args, **_kwargs):
        raise StructuredExtractionRateLimitedError(
            "rate limited", retry_after_seconds=14.2
        )

    monkeypatch.setattr(st_route, "structure_extracted_document", _raise_429)
    monkeypatch.setattr(
        se_route, "extract_validate_and_check_shipment", _raise_429
    )

    r1 = client.post(f"/api/v1/documents/{uuid4()}/structured-extraction")
    assert r1.status_code == 429
    assert r1.headers.get("retry-after") == "15"

    r2 = client.post(
        "/api/v1/compliance/check-documents",
        json={
            "commercial_invoice_document_id": str(uuid4()),
            "packing_list_document_id": str(uuid4()),
        },
    )
    assert r2.status_code == 429
    assert r2.headers.get("retry-after") == "15"


def test_sro_2486_ingestion_produces_clean_coherent_parent_and_ranks_first(
    isolated_database: Engine,
) -> None:
    from app.services.customs_audit.deps import default_evidence_provider
    from app.services.customs_audit.evidence import (
        document_evidence_for_check,
        is_regulatory_check,
        normalize_regulatory_evidence,
    )
    from app.services.customs_audit.query import query_for_check
    from app.services.regulatory.embeddings import (
        FakeEmbeddingProvider,
        reset_embedding_provider,
        set_embedding_provider,
    )
    from app.services.regulatory.ingestion import ingest_source
    from app.services.regulatory.reranker import (
        LexicalReranker,
        reset_reranker,
        set_reranker,
    )
    from app.services.regulatory.sources import discover_sources

    set_embedding_provider(FakeEmbeddingProvider())
    set_reranker(LexicalReranker())
    try:
        with Session(isolated_database) as db:
            sources_by_key = {s.key: s for s in discover_sources()}
            sro_res = ingest_source(db, sources_by_key["sro_2486_2025_raw_cotton"], "v1")
            ingest_source(db, sources_by_key["tipp_textile_product_requirements"], "v1")
            db.commit()

            # 1. SRO 2486(I)/2025 is kept together as 1 clean parent chunk with no
            #    "VALIDATED TEXT DERIVATIVE" or "manually_verified" internal noise.
            assert sro_res.parent_chunks == 1

            for cid, cname, req_doc in (
                ("raw_cotton_sbp_deposit_proof", "Proof of 1% SBP deposit", "sbp_deposit_proof"),
                ("raw_cotton_sbp_confirmation", "SBP confirmation", "sbp_confirmation"),
                ("raw_cotton_irrevocable_letter_of_credit", "Irrevocable letter of credit", "irrevocable_letter_of_credit"),
                ("raw_cotton_shipment_within_180_days", "Raw cotton shipment within 180 days", None),
            ):
                check = {
                    "check_id": cid,
                    "check_name": cname,
                    "pct_code": "52010090",
                    "required_document": req_doc,
                    "sro_number": "2486(I)/2025",
                    "source_document": "SRO 2486(I)/2025 — amendment to Export Policy Order, 2022",
                }
                q = query_for_check(
                    check, {"invoice": {"destination_country": {"value": "China"}}}
                )
                ev = default_evidence_provider(db, "52010090", q)
                norm = normalize_regulatory_evidence(ev)
                assert norm, cid
                assert norm[0]["sro_number"] == "2486(I)/2025", cid
                assert norm[0]["source_kind"] == "official", cid
                assert "VALIDATED TEXT DERIVATIVE" not in (norm[0]["snippet"] or "")
                assert "manually_verified" not in (norm[0]["snippet"] or "")

        # 2. Internal schema and weight checks are NOT classified as regulatory rules.
        assert (
            is_regulatory_check(
                {
                    "check_id": "required_fields",
                    "source_document": "Phase 1 shipment input schema",
                }
            )
            is False
        )
        assert (
            is_regulatory_check(
                {
                    "check_id": "weight_consistency",
                    "source_document": "Shipment weight validation",
                }
            )
            is False
        )
        weight_doc_ev = document_evidence_for_check(
            {"check_id": "weight_consistency", "status": "passed"},
            {
                "invoice": {
                    "line_items": [
                        {
                            "item_index": 1,
                            "net_weight": {"value": "75.00", "source_page": 1},
                            "gross_weight": {"value": "80.00", "source_page": 1},
                        }
                    ]
                },
                "packing_list": {"items": []},
                "items": [
                    {
                        "item_reference": "invoice_line_1",
                        "invoice_item_index": 1,
                        "packing_item_index": None,
                    }
                ],
            },
            item_reference="invoice_line_1",
        )
        assert {e["field_name"]: e["extracted_value"] for e in weight_doc_ev} == {
            "net_weight": "75.00",
            "gross_weight": "80.00",
        }
    finally:
        reset_embedding_provider()
        reset_reranker()


def test_customs_chat_retriever_and_checklist_tool_surface_complete_citations(
    isolated_database: Engine,
    monkeypatch,
) -> None:
    from app.core.config import get_settings
    from app.services.customs_chat.knowledge.retriever import retrieve_evidence
    from app.services.customs_chat.pipeline import answer_customs_question
    from app.services.regulatory.embeddings import (
        FakeEmbeddingProvider,
        reset_embedding_provider,
        set_embedding_provider,
    )
    from app.services.regulatory.reranker import (
        LexicalReranker,
        reset_reranker,
        set_reranker,
    )
    from tests.unit.test_regulatory_retrieval import add_evidence

    set_embedding_provider(FakeEmbeddingProvider())
    set_reranker(LexicalReranker())
    monkeypatch.setattr(get_settings(), "customs_chat_enable_query_rewriter", False)
    try:
        with Session(isolated_database) as db:
            # 1. General procedural manual with pct_codes=[] is still retrieved even when pct_code is passed.
            add_evidence(
                db,
                key="psw_sd_manual",
                source_document="PSW User Manual - Single Declaration (Exports)",
                parent_text=(
                    "Full parent section: To submit a Single Declaration for export in "
                    "Pakistan Single Window, log into the trader portal and complete "
                    "the goods declaration form."
                ),
                children=["Single Declaration for export in Pakistan Single Window"],
                pct_codes=[],
                document_type="psw_user_manual",
                issuing_authority="Pakistan Single Window",
            )
            add_evidence(
                db,
                key="tshirt_form_e",
                source_document="Export Policy Order, 2022 — SRO 544(I)/2022",
                parent_text=(
                    "Form-E export declaration is required for cotton knitted "
                    "t-shirts under PCT 6109.1000 shipped to China."
                ),
                pct_codes=["61091000"],
                sro_number="544(I)/2022",
                page_number=14,
                document_type="export_policy_order",
                issuing_authority="Government of Pakistan, Ministry of Commerce",
            )

            ev = retrieve_evidence(
                db,
                search_queries=("Single Declaration for export in Pakistan Single Window",),
                pct_code="61091000",
            )
            assert ev.band in ("STRONG", "WEAK")
            assert any("PSW User Manual" in c.document for c in ev.citations)
            # Parent text (not truncated child window) is used as the citation excerpt.
            psw_cit = next(c for c in ev.citations if "PSW User Manual" in c.document)
            assert "Full parent section:" in psw_cit.excerpt

            # 2. Checklist tool attaches regulatory citations from pre-submission guidance.
            turn = answer_customs_question(
                db,
                message="What documents do I need to export cotton t-shirts to China?",
                client=None,
            )
            assert turn.checklist_tool is not None
            assert len(turn.citations) >= 1
            assert turn.citations[0].is_official is True
    finally:
        reset_embedding_provider()
        reset_reranker()


def test_provide_missing_document_route_preserves_document_type_and_surfaces_task(
    isolated_database: Engine,
) -> None:
    """Missing supporting documents surface in GET /review and preserve their
    submitted document type (`source`) through POST /review into Revision 2."""
    from app.api.routes.customs_audit import get_customs_audit_service
    from app.schemas.supporting_documents import SupportingDocumentRef
    from app.services.customs_audit.agents import (
        DeterministicAuditorAgent,
        DeterministicBrokerAgent,
    )
    from app.services.customs_audit.checkpointer import build_memory_checkpointer
    from app.services.customs_audit.deps import WorkflowDeps
    from app.services.customs_audit.factory import build_service
    from tests.unit.test_customs_audit import (
        PASSED_LEGAL_CHECK,
        line,
        make_extraction,
    )

    doc_id = uuid4()
    with Session(isolated_database) as db:
        db.add(
            DocumentUploadRecord(
                id=doc_id,
                original_filename="coo.pdf",
                stored_filename=f"{doc_id}.pdf",
                file_extension=".pdf",
                mime_type="application/pdf",
                size_bytes=1024,
                status="extracted",
            )
        )
        db.commit()

    recorded_requests: list[MultiLineShipmentRequest] = []
    missing_coo_check = {
        "check_id": "destination_certificate_of_origin",
        "check_name": "Certificate of Origin for China",
        "status": "failed",
        "required_document": "certificate_of_origin",
        "message": "Certificate of Origin is required for China shipments.",
        "source_document": "TIPP CPFTA",
        "issuing_authority": "Ministry of Commerce",
        "pct_code": "61091000",
    }

    def dynamic_pipeline(_db: Session, req: MultiLineShipmentRequest) -> dict:
        recorded_requests.append(req)
        if len(recorded_requests) == 1:
            return make_extraction(
                [
                    line(
                        status="failed",
                        compliance_checks=[PASSED_LEGAL_CHECK, missing_coo_check],
                        item_checks=[{"check_id": "item_quantity_match", "status": "passed"}],
                    )
                ],
                "failed",
            )
        return passed_extraction()

    deps = WorkflowDeps(
        session_factory=lambda: Session(isolated_database),
        run_pipeline=dynamic_pipeline,
        evidence_provider=lambda _db, _pct, _q: [],
        broker_agent=DeterministicBrokerAgent(),
        auditor_agent=DeterministicAuditorAgent(),
        human_review_required=True,
        rule_data_version_fn=lambda: "sha256:testrules",
        vector_index_version_fn=lambda _db: "vec-index-v1",
    )
    svc = build_service(
        lambda: Session(isolated_database),
        deps=deps,
        checkpointer=build_memory_checkpointer(),
    )

    async def override_service():
        return svc

    app.dependency_overrides[get_customs_audit_service] = override_service
    try:
        start_resp = client.post(
            "/api/v1/customs-audit/workflows",
            json={
                "commercial_invoice_document_id": str(uuid4()),
                "packing_list_document_id": str(uuid4()),
                "shipment_date": "2026-07-20",
                "additional_uploaded_document_types": ["form_e"],
            },
        )
        assert start_resp.status_code == 200
        started = start_resp.json()
        assert started["status"] == "awaiting_human_review"
        wf_id = started["workflow_id"]

        review_resp = client.get(f"/api/v1/customs-audit/workflows/{wf_id}/review")
        assert review_resp.status_code == 200
        task = review_resp.json()
        assert "provide_missing_document" in task["requested_actions"]
        assert task["reason_code"] == "missing_required_document"
        assert any(
            doc["document_type"] == "certificate_of_origin"
            for doc in task["missing_documents"]
        )

        submit_resp = client.post(
            f"/api/v1/customs-audit/workflows/{wf_id}/review",
            json={
                "action": "provide_missing_document",
                "provided_document_ids": [str(doc_id)],
                "source": "certificate_of_origin",
                "reviewer_reference": "reviewer-ui-1",
                "reason": "Uploaded Chamber of Commerce COO PDF.",
            },
        )
        assert submit_resp.status_code == 200
        resumed = submit_resp.json()
        assert resumed["status"] == "completed"
        assert resumed["deterministic_status"] == "passed"
        assert len(recorded_requests) == 2
        recomputed_req = recorded_requests[1]
        assert recomputed_req.supporting_documents == [
            SupportingDocumentRef(
                document_type="certificate_of_origin",
                document_id=doc_id,
            )
        ]
        assert recomputed_req.additional_uploaded_document_types == ["form_e"]
        revisions = resumed["final_report"]["audit_revisions"]
        assert len(revisions) == 2
        assert revisions[1]["triggered_by"] == "missing_document_provided"
    finally:
        app.dependency_overrides.pop(get_customs_audit_service, None)


def test_request_reprocessing_clears_caches_and_creates_new_revision(
    isolated_database: Engine,
) -> None:
    """`request_reprocessing` clears document structured/OCR caches and the
    revision pipeline cache, re-runs extraction/checks, and appends Revision 2."""
    from app.api.routes.customs_audit import get_customs_audit_service
    from app.services.customs_audit.agents import (
        DeterministicAuditorAgent,
        DeterministicBrokerAgent,
    )
    from app.services.customs_audit.checkpointer import build_memory_checkpointer
    from app.services.customs_audit.deps import WorkflowDeps
    from app.services.customs_audit.factory import build_service
    from tests.unit.test_customs_audit import events, ocr_extraction

    inv_id = uuid4()
    pkg_id = uuid4()
    rev_id = uuid4()
    with Session(isolated_database) as db:
        for doc_id, fname in ((inv_id, "inv.pdf"), (pkg_id, "pkg.pdf")):
            db.add(
                DocumentUploadRecord(
                    id=doc_id,
                    original_filename=fname,
                    stored_filename=f"{doc_id}.pdf",
                    file_extension=".pdf",
                    mime_type="application/pdf",
                    size_bytes=2048,
                    status="extracted",
                    extracted_text="Cached text",
                    structured_data={"phase_2c_commercial_invoice": {"status": "extracted"}},
                    structured_extraction_status="extracted",
                    ocr_pages=[{"page_number": 1, "ocr_confidence": "0.55"}],
                )
            )
        db.commit()

    cached_req = MultiLineShipmentRequest(
        review_revision_id=rev_id,
        commercial_invoice_document_id=inv_id,
        packing_list_document_id=pkg_id,
        shipment_date="2026-07-20",
        additional_uploaded_document_types=[],
        supporting_documents=[],
    )
    cache_multi_line_response(
        cached_req,
        type("DummyResp", (), {"model_dump": lambda self, mode="json": ocr_extraction()})(),  # type: ignore[arg-type]
    )
    assert get_cached_multi_line_response(cached_req) is not None

    observed_cache_during_run: list[dict | None] = []
    calls = {"count": 0}

    def reprocessing_pipeline(db: Session, req: MultiLineShipmentRequest) -> dict:
        calls["count"] += 1
        if calls["count"] == 1:
            return ocr_extraction()
        inv_row = db.get(DocumentUploadRecord, inv_id)
        pkg_row = db.get(DocumentUploadRecord, pkg_id)
        observed_cache_during_run.append(
            {
                "inv_structured": inv_row.structured_data if inv_row else "missing",
                "inv_ocr": inv_row.ocr_pages if inv_row else "missing",
                "pkg_structured": pkg_row.structured_data if pkg_row else "missing",
                "pipeline_cache": get_cached_multi_line_response(req),
            }
        )
        return passed_extraction()

    deps = WorkflowDeps(
        session_factory=lambda: Session(isolated_database),
        run_pipeline=reprocessing_pipeline,
        evidence_provider=lambda _db, _pct, _q: [],
        broker_agent=DeterministicBrokerAgent(),
        auditor_agent=DeterministicAuditorAgent(),
        human_review_required=True,
        rule_data_version_fn=lambda: "sha256:testrules",
        vector_index_version_fn=lambda _db: "vec-index-v1",
    )
    svc = build_service(
        lambda: Session(isolated_database),
        deps=deps,
        checkpointer=build_memory_checkpointer(),
    )

    async def override_service():
        return svc

    app.dependency_overrides[get_customs_audit_service] = override_service
    try:
        start_resp = client.post(
            "/api/v1/customs-audit/workflows",
            json={
                "review_revision_id": str(rev_id),
                "commercial_invoice_document_id": str(inv_id),
                "packing_list_document_id": str(pkg_id),
                "shipment_date": "2026-07-20",
            },
        )
        assert start_resp.status_code == 200
        wf_id = start_resp.json()["workflow_id"]

        review_resp = client.get(f"/api/v1/customs-audit/workflows/{wf_id}/review")
        assert review_resp.status_code == 200
        assert "request_reprocessing" in review_resp.json()["requested_actions"]

        reprocess_resp = client.post(
            f"/api/v1/customs-audit/workflows/{wf_id}/review",
            json={
                "action": "request_reprocessing",
                "reviewer_reference": "reviewer-ui-1",
                "reason": "OCR misread page 1; force clean re-extraction.",
            },
        )
        assert reprocess_resp.status_code == 200
        resumed = reprocess_resp.json()
        assert resumed["status"] == "completed"
        assert resumed["deterministic_status"] == "passed"
        assert calls["count"] == 2
        assert observed_cache_during_run == [
            {
                "inv_structured": None,
                "inv_ocr": None,
                "pkg_structured": None,
                "pipeline_cache": None,
            }
        ]
        event_types = [e["event_type"] for e in events(svc, isolated_database, wf_id)]
        assert "reprocessing_requested" in event_types
        assert "checks_recomputed" in event_types
        revisions = resumed["final_report"]["audit_revisions"]
        assert len(revisions) == 2
        assert revisions[1]["triggered_by"] == "reprocessing_requested"
    finally:
        _REVISION_PIPELINE_CACHE.clear()
        app.dependency_overrides.pop(get_customs_audit_service, None)


def test_disputed_field_details_include_source_document_ids(
    isolated_database: Engine,
) -> None:
    """Paused human-review tasks populate document_id on invoice and packing-list
    disputed_field_details so the side-by-side PDF viewer can inspect both PDFs."""
    from app.api.routes.customs_audit import get_customs_audit_service
    from app.services.customs_audit.agents import (
        DeterministicAuditorAgent,
        DeterministicBrokerAgent,
    )
    from app.services.customs_audit.checkpointer import build_memory_checkpointer
    from app.services.customs_audit.deps import WorkflowDeps
    from app.services.customs_audit.factory import build_service
    from tests.unit.test_customs_audit import fld, mismatch_extraction

    invoice_id = str(uuid4())
    packing_id = str(uuid4())
    extraction_payload = mismatch_extraction()
    extraction_payload["packing_list"]["items"] = [
        {
            "item_index": 1,
            "line_number": fld(1),
            "product_name": fld("Cotton knitted T"),
            "pct_code": fld("6109.1000"),
            "quantity": fld("99"),
            "unit": fld("PCS"),
            "net_weight": fld("75"),
            "gross_weight": fld("80"),
        }
    ]

    deps = WorkflowDeps(
        session_factory=lambda: Session(isolated_database),
        run_pipeline=lambda _db, _req: extraction_payload,
        evidence_provider=lambda _db, _pct, _q: [],
        broker_agent=DeterministicBrokerAgent(),
        auditor_agent=DeterministicAuditorAgent(),
        human_review_required=True,
        rule_data_version_fn=lambda: "sha256:testrules",
        vector_index_version_fn=lambda _db: "vec-index-v1",
    )
    svc = build_service(
        lambda: Session(isolated_database),
        deps=deps,
        checkpointer=build_memory_checkpointer(),
    )

    async def override_service():
        return svc

    app.dependency_overrides[get_customs_audit_service] = override_service
    try:
        start_resp = client.post(
            "/api/v1/customs-audit/workflows",
            json={
                "commercial_invoice_document_id": invoice_id,
                "packing_list_document_id": packing_id,
                "shipment_date": "2026-07-20",
            },
        )
        assert start_resp.status_code == 200
        wf_id = start_resp.json()["workflow_id"]

        review_resp = client.get(f"/api/v1/customs-audit/workflows/{wf_id}/review")
        assert review_resp.status_code == 200
        details = review_resp.json()["disputed_field_details"]
        by_type = {d["document_type"]: d for d in details}
        assert by_type["commercial_invoice"]["document_id"] == invoice_id
        assert by_type["packing_list"]["document_id"] == packing_id
    finally:
        app.dependency_overrides.pop(get_customs_audit_service, None)


