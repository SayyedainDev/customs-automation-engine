"""Conformance evidence for the Model Context Protocol (MCP) tool server surface."""

from __future__ import annotations

from app.mcp_server import (
    calculate_pakistan_customs_duties,
    get_textile_export_checklist,
    lookup_pakistan_tariff,
    mcp,
    search_pakistan_customs_regulations,
)
from app.models.regulatory import RegulatoryChunk
from app.models.tariff import TariffLine
from cace_testing.harnesses.database import CaceRuntime


def test_mcp_server_exposes_and_executes_all_four_deterministic_tools(
    cace_runtime: CaceRuntime,
) -> None:
    assert mcp.name == "cace-pakistan-customs-engine"
    if hasattr(mcp, "tools"):
        registered = set(mcp.tools)
        assert {
            "lookup_pakistan_tariff",
            "calculate_pakistan_customs_duties",
            "get_textile_export_checklist",
            "search_pakistan_customs_regulations",
        } <= registered

    with cace_runtime.open_session() as db:
        db.add(
            TariffLine(
                hs_code="61091000",
                description="T-shirts, singlets and other vests, knitted or crocheted - Of cotton.",
                chapter="61",
                heading="6109",
                customs_duty_pct=20.0,
                tariff_year="FY2025-26",
                source_page=156,
                needs_review=False,
            )
        )
        db.add(
            RegulatoryChunk(
                chunk_id="mcp_sro:p0",
                parent_chunk_id=None,
                role="parent",
                is_parent=True,
                chunk_index=0,
                source_document="Export Policy Order",
                source_path="regulatory_data/epo.txt",
                source_url="https://www.fbr.gov.pk/",
                document_checksum="sha256:mcpsro",
                issuing_authority="Ministry of Commerce",
                document_type="export_policy",
                sro_number="544(I)/2022",
                page_number=4,
                section="Para 4",
                pct_codes=["61091000"],
                validation_status="verified",
                rule_data_version="sha256:testrules",
                ingestion_version="test-v1",
                text="Cotton knitted T-shirts under PCT 6109.1000 require Form-E.",
                char_count=62,
            )
        )
        db.add(
            RegulatoryChunk(
                chunk_id="mcp_sro:p0:c0",
                parent_chunk_id="mcp_sro:p0",
                role="child",
                is_parent=False,
                chunk_index=0,
                source_document="Export Policy Order",
                source_path="regulatory_data/epo.txt",
                source_url="https://www.fbr.gov.pk/",
                document_checksum="sha256:mcpsro",
                issuing_authority="Ministry of Commerce",
                document_type="export_policy",
                sro_number="544(I)/2022",
                page_number=4,
                section="Para 4",
                pct_codes=["61091000"],
                validation_status="verified",
                rule_data_version="sha256:testrules",
                ingestion_version="test-v1",
                text="Cotton knitted T-shirts under PCT 6109.1000 require Form-E.",
                char_count=62,
            )
        )
        db.commit()

    tariff = lookup_pakistan_tariff("61091000")
    assert tariff["matched"] is True
    assert tariff["exact"] is True
    assert tariff["customs_duty_pct"] == 20.0
    assert tariff["cascading_tax_breakdown"] is not None

    duties = calculate_pakistan_customs_duties(
        assessed_value="100.00",
        customs_duty_pct="20.00",
    )
    assert duties["breakdown"]["customs_duty"] == "20.00"
    assert duties["breakdown"]["additional_customs_duty"] == "6.00"
    assert len(duties["formula_steps"]) > 0

    checklist = get_textile_export_checklist(
        "What documents do I need to export cotton t-shirts to Germany?",
        destination="Germany",
    )
    assert checklist["matched"] is True
    assert checklist["pct_code"] == "61091000"
    assert len(checklist["required_documents"]) > 0

    regulations = search_pakistan_customs_regulations(
        "Form-E requirement for cotton knitted T-shirts 61091000",
        pct_code="61091000",
    )
    assert regulations["status"] == "ok"
    assert len(regulations["results"]) == 1
    assert regulations["results"][0]["sro_number"] == "544(I)/2022"
