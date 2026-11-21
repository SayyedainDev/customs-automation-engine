"""Unit and API tests for Flow 5: statutory tariff population, HS auto-resolution,
Decimal line/shipment duty estimates, and exportable Audit Dossier (PDF/JSON)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pymupdf
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.main import app
from app.models.tariff import TariffLine
from app.services.compliance.duty_calculator import (
    calculate_cascading_customs_taxes,
)
from app.services.customs_audit.report import build_audit_report
from app.services.customs_chat.knowledge.tariff_parser import parse_tariff_page
from scripts.load_tariff_lines import load_rows

client = TestClient(app)


def test_parser_recovers_long_multiline_ad_valorem_and_flags_specific_duty() -> None:
    lines = ["0304.5100"] + [f"- Description continuation line {i}" for i in range(1, 13)] + ["20"]
    specific_duty = [
        "1404.9020",
        "- - Betel leaves",
        "Rs.600/Kg",
        "1404.9090",
        "- - Other",
        "11",
    ]
    rows = parse_tariff_page("\n".join([*lines, *specific_duty]), page_number=12)
    by_code = {row.hs_code: row for row in rows}

    assert by_code["03045100"].needs_review is False
    assert by_code["03045100"].customs_duty_pct == 20.0
    assert by_code["14049020"].needs_review is True
    assert by_code["14049020"].customs_duty_pct is None
    assert by_code["14049090"].needs_review is False
    assert by_code["14049090"].customs_duty_pct == 11.0


def test_load_tariff_lines_populates_statutory_tax_columns(
    isolated_database: Engine,
) -> None:
    sample_rows = [
        {
            "hs_code": "61091000",
            "description": "Of cotton",
            "chapter": "61",
            "heading": "6109",
            "customs_duty_pct": 20.0,
            "tariff_year": "FY2025-26",
            "source_page": 156,
            "needs_review": False,
        },
        {
            "hs_code": "52051100",
            "description": "Measuring 714.29 decitex or more",
            "chapter": "52",
            "heading": "5205",
            "customs_duty_pct": 5.0,
            "tariff_year": "FY2025-26",
            "source_page": 133,
            "needs_review": False,
        },
        {
            "hs_code": "14049020",
            "description": "Betel leaves Rs.600/Kg",
            "chapter": "14",
            "heading": "1404",
            "customs_duty_pct": None,
            "tariff_year": "FY2025-26",
            "source_page": 29,
            "needs_review": True,
        },
    ]
    with Session(isolated_database) as db:
        loaded_count = load_rows(db, sample_rows)
        assert loaded_count == 3

        rows = {
            r.hs_code: r
            for r in db.execute(select(TariffLine)).scalars().all()
        }

    assert rows["61091000"].additional_duty_pct == 6.0
    assert rows["61091000"].regulatory_duty_pct == 0.0
    assert rows["61091000"].sales_tax_pct == 18.0
    assert rows["61091000"].income_tax_pct == 5.5

    assert rows["52051100"].additional_duty_pct == 2.0
    assert rows["52051100"].regulatory_duty_pct == 0.0
    assert rows["52051100"].sales_tax_pct == 18.0
    assert rows["52051100"].income_tax_pct == 5.5

    assert rows["14049020"].additional_duty_pct is None
    assert rows["14049020"].regulatory_duty_pct is None
    assert rows["14049020"].sales_tax_pct is None
    assert rows["14049020"].income_tax_pct is None


def test_calculate_duties_auto_resolves_db_and_schedule_and_rejects_unknown(
    isolated_database: Engine,
) -> None:
    with Session(isolated_database) as db:
        db.add(
            TariffLine(
                hs_code="62034200",
                description="Men's or boys' trousers - of cotton.",
                chapter="62",
                heading="6203",
                customs_duty_pct=20.0,
                additional_duty_pct=6.0,
                regulatory_duty_pct=5.0,
                sales_tax_pct=18.0,
                income_tax_pct=5.5,
                tariff_year="FY2025-26",
                source_page=158,
                needs_review=False,
            )
        )
        db.add(
            TariffLine(
                hs_code="62034900",
                description="Unconfirmed rate row",
                chapter="62",
                heading="6203",
                customs_duty_pct=None,
                tariff_year="FY2025-26",
                source_page=158,
                needs_review=True,
            )
        )
        db.commit()

    # 1. Auto-resolves from tariff_lines DB row, including DB regulatory_duty_pct=5.00
    db_resp = client.post(
        "/api/v1/compliance/calculate-duties",
        json={"hs_code": "6203.4200", "assessed_value": "1000.00"},
    )
    assert db_resp.status_code == 200
    db_data = db_resp.json()
    assert db_data["rate_source"] == "tariff_lines_db"
    assert db_data["source_page"] == 158
    assert db_data["breakdown"]["customs_duty_pct"] == "20.00"
    assert db_data["breakdown"]["regulatory_duty_pct"] == "5.00"

    # 2. Auto-resolves verified textile schedule when code is absent from DB
    sched_resp = client.post(
        "/api/v1/compliance/calculate-duties",
        json={"hs_code": "61091000", "assessed_value": "550.00"},
    )
    assert sched_resp.status_code == 200
    sched_data = sched_resp.json()
    assert sched_data["rate_source"] == "pct_fy2025_26_schedule"
    assert sched_data["tariff_year"] == "FY2025-26"
    assert sched_data["breakdown"]["customs_duty_pct"] == "20.00"
    assert sched_data["breakdown"]["additional_customs_duty_pct"] == "6.00"

    # 3. Unknown or needs_review HS code without explicit CD must fail closed (422)
    assert (
        client.post(
            "/api/v1/compliance/calculate-duties",
            json={"hs_code": "99999999", "assessed_value": "1000.00"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/compliance/calculate-duties",
            json={"hs_code": "62034900", "assessed_value": "1000.00"},
        ).status_code
        == 422
    )

    # 4. Explicit customs_duty_pct override stays caller_supplied
    manual_resp = client.post(
        "/api/v1/compliance/calculate-duties",
        json={
            "hs_code": "99999999",
            "assessed_value": "1000.00",
            "customs_duty_pct": "15.00",
        },
    )
    assert manual_resp.status_code == 200
    manual_data = manual_resp.json()
    assert manual_data["rate_source"] == "caller_supplied"
    assert manual_data["breakdown"]["customs_duty_pct"] == "15.00"


def test_build_audit_report_attaches_line_and_shipment_duty_estimates() -> None:
    from tests.unit.test_customs_audit import (
        PASSED_LEGAL_CHECK,
        line,
        make_extraction,
    )

    extraction = make_extraction(
        [
            line(
                pct="6109.1000",
                product="Cotton knitted T-shirts",
                quantity="100",
                unit_price="5.50",
                line_total="550.00",
                status="passed",
                compliance_checks=[PASSED_LEGAL_CHECK],
                item_checks=[{"check_id": "item_quantity_match", "status": "passed"}],
            ),
            line(
                pct="9999.9999",
                product="Unmapped novelty item",
                quantity="10",
                unit_price="10.00",
                line_total="100.00",
                supported=False,
                status="manual_review",
                compliance_checks=[PASSED_LEGAL_CHECK],
                item_checks=[{"check_id": "item_quantity_match", "status": "passed"}],
            ),
        ],
        "manual_review",
    )
    extraction["invoice"]["currency"] = {
        "value": "USD",
        "source_page": 1,
        "confidence": "0.99",
        "validation_status": "verified",
    }
    state = {
        "workflow_id": str(uuid4()),
        "extraction_result": extraction,
        "deterministic_compliance_result": {"overall_status": "manual_review"},
        "consensus_result": {"requires_human_review": True},
    }

    report = build_audit_report(state)
    items = report["line_items"]
    assert len(items) == 2

    est1 = items[0]["duty_estimate"]
    expected = calculate_cascading_customs_taxes(
        assessed_value=Decimal("550.00"),
        customs_duty_pct=Decimal("20.00"),
        additional_customs_duty_pct=Decimal("6.00"),
        regulatory_duty_pct=Decimal("0.00"),
        sales_tax_pct=Decimal("18.00"),
        advance_income_tax_pct=Decimal("5.50"),
    ).to_dict()
    assert est1 is not None
    assert est1["hs_code"] == "61091000"
    assert est1["currency"] == "USD"
    assert est1["breakdown"] == expected

    assert items[1]["duty_estimate"] is None

    summary = report["duty_estimation_summary"]
    assert summary is not None
    assert summary["lines_estimated"] == 1
    assert summary["total_lines"] == 2
    assert summary["currency"] == "USD"
    assert summary["total_assessed_value"] == "550.00"
    assert summary["total_customs_duty"] == expected["customs_duty"]
    assert summary["total_duties_and_taxes"] == expected["total_duties_and_taxes"]
    assert summary["total_landed_cost"] == expected["total_landed_cost"]


def test_workflow_dossier_pdf_and_json_endpoints(
    isolated_database: Engine,
) -> None:
    from app.api.routes.customs_audit import get_customs_audit_service
    from app.services.customs_audit.agents import (
        DeterministicAuditorAgent,
        DeterministicBrokerAgent,
    )
    from app.services.customs_audit.checkpointer import build_memory_checkpointer
    from app.services.customs_audit.deps import WorkflowDeps
    from app.services.customs_audit.factory import build_service
    from tests.unit.test_customs_audit import fld, passed_extraction

    extraction_payload = passed_extraction()
    extraction_payload["invoice"]["exporter_name"] = fld("A & B Textiles (Pvt.) Ltd.")
    extraction_payload["invoice"]["buyer_name"] = fld("Shanghai Imports Co.")
    extraction_payload["invoice"]["currency"] = fld("USD")
    extraction_payload["items"][0]["compliance"]["checks"] = [
        {
            "check_id": "xr_61091000_form_e",
            "check_name": "Form-E requirement for textile export",
            "status": "passed",
            "source_document": "Foreign Exchange Manual",
            "sro_number": "S.R.O. 957(I)/2021",
            "issuing_authority": "SBP",
            "source_page": 12,
            "effective_date": "2021-07-01",
            "pct_code": "61091000",
        }
    ]

    deps = WorkflowDeps(
        session_factory=lambda: Session(isolated_database),
        run_pipeline=lambda _db, _req: extraction_payload,
        evidence_provider=lambda _db, _pct, _q: [
            {
                "source_document": "Foreign Exchange Manual",
                "sro_number": "S.R.O. 957(I)/2021",
                "page_number": 12,
                "section": "Ch. 12",
                "issuing_authority": "SBP",
                "effective_date": "2021-07-01",
                "child_evidence_text": "Exporters must file Form-E via PSW prior to shipment.",
                "parent_evidence_text": "Exporters must file Form-E via PSW prior to shipment.",
                "rule_data_version": "sha256:testrules",
                "validation_status": "verified",
                "cross_encoder_score": 0.91,
            }
        ],
        broker_agent=DeterministicBrokerAgent(),
        auditor_agent=DeterministicAuditorAgent(),
        human_review_required=False,
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
            },
        )
        assert start_resp.status_code == 200
        wf_id = start_resp.json()["workflow_id"]

        pdf_resp = client.get(
            f"/api/v1/customs-audit/workflows/{wf_id}/dossier",
            params={"format": "pdf"},
        )
        assert pdf_resp.status_code == 200
        assert pdf_resp.headers["content-type"].startswith("application/pdf")
        assert pdf_resp.content.startswith(b"%PDF-")

        pdf_doc = pymupdf.open(stream=pdf_resp.content, filetype="pdf")
        try:
            full_text = "\n".join(pdf_doc[i].get_text() for i in range(pdf_doc.page_count))
        finally:
            pdf_doc.close()

        assert wf_id[:8] in full_text
        assert "PASSED" in full_text
        assert "A & B Textiles" in full_text
        assert "6109.1000" in full_text
        assert "550.00" in full_text
        assert "S.R.O. 957(I)/2021" in full_text

        json_resp = client.get(
            f"/api/v1/customs-audit/workflows/{wf_id}/dossier",
            params={"format": "json"},
        )
        assert json_resp.status_code == 200
        assert json_resp.headers["content-type"].startswith("application/json")
        dossier = json_resp.json()
        assert dossier["dossier_version"] == "1.0"
        assert dossier["workflow"]["workflow_id"] == wf_id
        assert dossier["duty_estimation_summary"]["total_assessed_value"] == "550.00"
        assert len(dossier["audit_event_chain"]) > 0
        assert dossier["audit_event_chain"][-1]["new_state_hash"]

        assert (
            client.get(f"/api/v1/customs-audit/workflows/{uuid4()}/dossier").status_code
            == 404
        )
    finally:
        app.dependency_overrides.pop(get_customs_audit_service, None)
