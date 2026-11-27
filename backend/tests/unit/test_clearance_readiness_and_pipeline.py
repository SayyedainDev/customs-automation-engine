"""Unit & integration tests for Pre-Clearance Readiness, Step-1 Duty/Telemetry,
Customs Chat checklist output guard, and MCP direct checklist lookup.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.main import app
from app import mcp_server
from app.schemas.compliance import ComplianceCheckResult, ComplianceCheckStatus
from app.schemas.multi_line_extraction import OutstandingDocument
from app.schemas.shipment_extraction import CrossDocumentCheck
from app.services import document_service
from app.services.compliance.readiness import compute_clearance_readiness
from app.services.customs_chat.pipeline import answer_customs_question


def test_readiness_clean_shipment_scores_100_low_risk() -> None:
    summary = compute_clearance_readiness(
        overall_status=ComplianceCheckStatus.PASSED,
        document_review_status=ComplianceCheckStatus.PASSED,
        checks=[
            CrossDocumentCheck(
                check_id="item_quantity_match",
                check_name="Quantity match",
                status=ComplianceCheckStatus.PASSED,
                message="Matched.",
                invoice_source_page=1,
                packing_list_source_page=1,
            )
        ],
    )
    assert summary.readiness_score == 100
    assert summary.readiness_band == "ready_for_filing"
    assert summary.port_hold_risk == "low"
    assert summary.remediation_steps == []


def test_readiness_separates_pending_external_docs_from_in_house_defects() -> None:
    pending_docs = compute_clearance_readiness(
        overall_status=ComplianceCheckStatus.FAILED,
        document_review_status=ComplianceCheckStatus.PASSED,
        checks=[],
        outstanding_documents=[
            OutstandingDocument(
                document_type="form_e",
                display_name="Form-E / PSW export declaration",
                requirement="required",
                reasons=["Form-E required before export."],
                sources=["SRO 957(I)/2021"],
            ),
            OutstandingDocument(
                document_type="certificate_of_origin",
                display_name="Certificate of origin",
                requirement="required",
                reasons=["CPFTA COO required for China."],
                sources=["TDAP CPFTA Guide"],
            ),
        ],
    )
    assert pending_docs.readiness_score == 70
    assert pending_docs.readiness_band == "awaiting_external_documents"
    assert pending_docs.port_hold_risk == "medium"
    assert pending_docs.in_house_fixes_count == 0
    assert pending_docs.external_documents_count == 2
    assert {s.owner for s in pending_docs.remediation_steps} == {
        "bank_or_psw",
        "issuing_authority",
    }

    mismatch = compute_clearance_readiness(
        overall_status=ComplianceCheckStatus.FAILED,
        document_review_status=ComplianceCheckStatus.FAILED,
        checks=[
            CrossDocumentCheck(
                check_id="item_quantity_match",
                check_name="Line quantity match",
                status=ComplianceCheckStatus.FAILED,
                message="Invoice has 100 PCS while packing list has 99 PCS.",
                invoice_source_page=1,
                packing_list_source_page=1,
            )
        ],
        outstanding_documents=[
            OutstandingDocument(
                document_type="form_e",
                display_name="Form-E / PSW export declaration",
                requirement="required",
            )
        ],
    )
    assert mismatch.readiness_score == 50
    assert mismatch.readiness_band == "blocked_document_defect"
    assert mismatch.port_hold_risk == "high"
    assert mismatch.in_house_fixes_count == 1
    assert mismatch.remediation_steps[0].priority == 1
    assert mismatch.remediation_steps[0].owner == "exporter_in_house"
    assert mismatch.remediation_steps[0].score_impact == 35
    assert mismatch.remediation_steps[1].priority == 2


def test_readiness_manual_confirmation_band() -> None:
    summary = compute_clearance_readiness(
        overall_status=ComplianceCheckStatus.MANUAL_REVIEW,
        document_review_status=ComplianceCheckStatus.MANUAL_REVIEW,
        checks=[
            ComplianceCheckResult(
                check_id="xr_yarn_afghanistan_duty_drawback",
                check_name="Afghanistan yarn drawback boundary",
                status=ComplianceCheckStatus.MANUAL_REVIEW,
                message="Manual verification required.",
                pct_code="52052100",
                required_document=None,
                source_document="Export Policy Order",
                sro_number="SRO 544(I)/2022",
                source_url=None,
                source_page=7,
                source_locator="paragraph 7(3)",
                issuing_authority="Ministry of Commerce",
                effective_date=None,
                legal_cutoff_date=None,
                rule_data_version="textile-2026",
                validation_status="verified",
            )
        ],
    )
    assert summary.readiness_band == "needs_human_confirmation"
    assert summary.port_hold_risk == "medium"
    assert summary.readiness_score == 90
    assert summary.remediation_steps[0].owner == "compliance_reviewer"


def test_multi_line_pipeline_populates_readiness_duty_and_telemetry_and_mcp_checklist(
    isolated_database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(document_service, "UPLOAD_DIRECTORY", upload_dir)
    monkeypatch.setattr(get_settings(), "extraction_mode", "hybrid")
    monkeypatch.setattr(get_settings(), "enable_demo_samples", True)
    monkeypatch.setattr(get_settings(), "groq_api_key", None)

    client = TestClient(app)

    samples = client.get("/api/v1/demo/samples").json()["samples"]
    assert {s["key"] for s in samples} == {
        "clean_cotton_tshirts",
        "missing_documents_hitl",
        "error_quantity_mismatch",
    }

    # 1. Clean cotton t-shirts -> 100/100 readiness + Decimal duty summary + telemetry
    clean_load = client.post("/api/v1/demo/samples/clean_cotton_tshirts/load").json()
    clean_res = client.post(
        "/api/v1/compliance/check-documents/multi-line",
        json={
            "commercial_invoice_document_id": clean_load["commercial_invoice"]["document_id"],
            "packing_list_document_id": clean_load["packing_list"]["document_id"],
            "shipment_date": clean_load["shipment_date"],
            "supporting_documents": [
                {"document_type": d["document_type"], "document_id": d["document"]["document_id"]}
                for d in clean_load["supporting_documents"]
            ],
        },
    )
    assert clean_res.status_code == 200
    clean_body = clean_res.json()
    assert clean_body["overall_status"] == "passed"
    assert clean_body["clearance_readiness"]["readiness_score"] == 100
    assert clean_body["clearance_readiness"]["port_hold_risk"] == "low"
    assert clean_body["duty_estimation_summary"] is not None
    assert float(clean_body["duty_estimation_summary"]["total_duties_and_taxes"]) > 0
    assert clean_body["items"][0]["duty_estimate"]["hs_code"] == "61091000"
    assert clean_body["pipeline_telemetry"]["llm_calls"] == 0
    assert clean_body["pipeline_telemetry"]["tokens_used"] == 0
    assert clean_body["pipeline_telemetry"]["estimated_tokens_saved"] > 0

    # 2. Pending documents sample -> awaiting_external_documents (sound uploaded files)
    pending_load = client.post("/api/v1/demo/samples/missing_documents_hitl/load").json()
    pending_res = client.post(
        "/api/v1/compliance/check-documents/multi-line",
        json={
            "commercial_invoice_document_id": pending_load["commercial_invoice"]["document_id"],
            "packing_list_document_id": pending_load["packing_list"]["document_id"],
            "shipment_date": pending_load["shipment_date"],
        },
    ).json()
    assert pending_res["document_review_status"] == "passed"
    assert pending_res["overall_status"] == "failed"
    assert (
        pending_res["clearance_readiness"]["readiness_band"]
        == "awaiting_external_documents"
    )
    assert pending_res["clearance_readiness"]["port_hold_risk"] == "medium"

    # 3. Customs Chat checklist tool without Groq key -> zero false guard violations!
    with Session(isolated_database) as db:
        chat_turn = answer_customs_question(
            db,
            message="What documents are required to export cotton t-shirts to China?",
            client=None,
        )
        assert chat_turn.mode == "GROUNDED"
        assert chat_turn.checklist_tool is not None
        assert chat_turn.guard_violations == ()

    # 4. MCP checklist tool accepts direct product name or PCT code without chat phrasing
    by_name = mcp_server.get_textile_export_checklist(
        "cotton t-shirts to China", "China"
    )
    assert by_name["matched"] is True
    assert by_name["pct_code"] == "61091000"

    by_code = mcp_server.get_textile_export_checklist("6109.1000", "China")
    assert by_code["matched"] is True
    assert by_code["pct_code"] == "61091000"
