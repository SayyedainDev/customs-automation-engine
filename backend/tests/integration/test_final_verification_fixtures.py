"""Route-level coverage for the three final-verification fixture families."""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api.routes.customs_audit import get_customs_audit_service
from app.core.config import get_settings
from app.main import app
from app.models.customs_audit import CustomsAuditEvent, CustomsAuditWorkflow
from app.models.assistant import AssistantMessage
from app.models.documents import DocumentUploadRecord
from app.models.shipment_chunks import ShipmentDocumentChunk
from app.schemas.shipment_extraction import CandidateField, FieldValidationStatus
from app.schemas.supporting_documents import SupportingDocumentCandidates
from app.services import document_service, document_upload_service
from app.services import multi_line_shipment_service
from app.services import supporting_document_service
from app.services.assistant import shipment_assistant
from app.services.customs_audit.checkpointer import build_memory_checkpointer
from app.services.customs_audit.deps import build_default_deps
from app.services.customs_audit.factory import build_service
from app.services.extraction.llm_gapfill import apply_gapfill_response
from app.services.extraction.telemetry import DocumentTelemetry
from scripts.generate_final_verification_fixtures import (
    FAMILIES,
    FINAL_FIXTURE_ROOT,
    materialize_variant,
)

client = TestClient(app)


def _configure_real_route_service(
    monkeypatch: pytest.MonkeyPatch,
    engine: Engine,
    upload_dir: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "extraction_mode", "hybrid")
    monkeypatch.setattr(settings, "groq_api_key", None)
    monkeypatch.setattr(settings, "langgraph_enable_live_agents", False)
    monkeypatch.setattr(document_upload_service, "UPLOAD_DIRECTORY", upload_dir)
    monkeypatch.setattr(document_service, "UPLOAD_DIRECTORY", upload_dir)

    def offline_gapfill(extraction, unresolved, *, document_ref="gapfill", client=None):
        del client
        payload = {
            name: (
                extraction.fields[name].candidates[0]
                if len(extraction.fields[name].candidates) == 1
                else None
            )
            for name in unresolved
        }
        updates = apply_gapfill_response(extraction, unresolved, payload)
        return updates, DocumentTelemetry(
            document_ref=document_ref,
            fields_total=len(unresolved),
            fields_from_llm=0,
            fields_missing=sum(value.value is None for value in updates.values()),
            notes=["deterministic offline test gap-fill; no external call"],
        )

    monkeypatch.setattr(
        multi_line_shipment_service,
        "run_gapfill",
        offline_gapfill,
    )

    def candidate(value: Any = None) -> CandidateField[Any]:
        return CandidateField[Any](
            value=value,
            source_page=1 if value is not None else None,
            confidence=Decimal("0.99") if value is not None else Decimal("0"),
            validation_status=(
                FieldValidationStatus.VERIFIED
                if value is not None
                else FieldValidationStatus.MANUAL_REVIEW
            ),
            validation_note=(
                "Read by deterministic test provider."
                if value is not None
                else "Not printed."
            ),
        )

    def read_label(text: str, *labels: str) -> str | None:
        for label in labels:
            match = re.search(
                rf"(?m)^{re.escape(label)}[ \t]*\n[ \t]*([^\n<]+?)\s*$",
                text,
            )
            if match:
                return match.group(1).strip()
        return None

    def offline_supporting_provider(**kwargs: Any) -> SupportingDocumentCandidates:
        text = kwargs["extracted_text"]
        is_form = "FORM E EXPORT DECLARATION" in text
        detected = (
            "Form E Export Declaration" if is_form else "Certificate of Origin"
        )
        printed: dict[str, Any] = {
            "detected_document_type": detected,
            "document_number": read_label(
                text, "Form E Number", "Certificate Number"
            ),
            "issue_date": read_label(text, "Issue Date"),
            "exporter_or_applicant": read_label(text, "Exporter"),
            "buyer_or_beneficiary": read_label(text, "Buyer / Consignee"),
            "invoice_reference": read_label(text, "Invoice Number"),
            "pct_code": read_label(text, "PCT Code"),
            "product_or_commodity": read_label(text, "Commodity"),
            "destination_country": read_label(text, "Destination Country"),
            "issuing_authority": read_label(text, "Issuing Authority"),
            "bank_name": read_label(text, "Bank"),
            "currency": read_label(text, "Currency"),
            "related_reference": read_label(text, "Related Reference"),
        }
        for name, label in (("amount", "Amount"), ("quantity", "Quantity")):
            value = read_label(text, label)
            printed[name] = Decimal(value) if value is not None else None
        return SupportingDocumentCandidates.model_validate(
            {
                name: candidate(printed.get(name)).model_dump(mode="json")
                for name in SupportingDocumentCandidates.model_fields
            }
        )

    monkeypatch.setattr(
        supporting_document_service,
        "extract_structured_model_from_text",
        offline_supporting_provider,
    )

    def session_factory() -> Session:
        return Session(engine)

    service = build_service(
        session_factory,
        deps=build_default_deps(session_factory),
        checkpointer=build_memory_checkpointer(),
    )
    app.dependency_overrides[get_customs_audit_service] = lambda: service


def _upload(path: Path) -> str:
    with path.open("rb") as handle:
        response = client.post(
            "/documents/upload",
            files={"file": (path.name, handle, "application/pdf")},
        )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _start_fixture(folder: Path) -> dict:
    invoice_id = _upload(folder / "commercial_invoice.pdf")
    packing_id = _upload(folder / "packing_list.pdf")
    form_id = _upload(folder / "form_e_psw_export_declaration.pdf")
    coo_path = folder / "certificate_of_origin.pdf"
    supporting_documents = [
        {
            "document_type": "form_e_or_psw_export_declaration",
            "document_id": form_id,
        }
    ]
    if coo_path.exists():
        supporting_documents.append(
            {
                "document_type": "certificate_of_origin",
                "document_id": _upload(coo_path),
            }
        )
    response = client.post(
        "/api/v1/customs-audit/workflows",
        json={
            "commercial_invoice_document_id": invoice_id,
            "packing_list_document_id": packing_id,
            "supporting_documents": supporting_documents,
            "shipment_date": "2026-07-20",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    result["invoice_document_id"] = invoice_id
    result["packing_list_document_id"] = packing_id
    result["supporting_document_ids"] = [
        item["document_id"] for item in supporting_documents
    ]
    return result


@pytest.mark.parametrize("family", FAMILIES, ids=lambda family: family.pct_code)
def test_clean_fixture_completes_real_upload_extraction_audit_route(
    family,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_database: Engine,
) -> None:
    _configure_real_route_service(
        monkeypatch,
        isolated_database,
        tmp_path / "uploads",
    )
    folder = FINAL_FIXTURE_ROOT / family.pct_code
    result = _start_fixture(folder)

    # The PDFs are mutually consistent and clear every document/matching check.
    # Four existing curated product-requirement records lack complete official
    # provenance, so the current executable evidence gate intentionally
    # retains manual review instead of turning those statements into legal
    # passes. This is the allowed "actual configured rule requires manual
    # review" boundary for a clean fixture.
    assert result["deterministic_status"] == "manual_review"
    assert result["status"] == "awaiting_human_review"
    assert result["requires_human_review"] is True
    assert result["final_report"]["consensus_result"]["deterministic_status"] == (
        "manual_review"
    )
    assert result["final_report"]["broker_findings"]["missing_documents"] == []
    assert result["final_report"]["broker_findings"]["unmatched_items"] == []

    with Session(isolated_database) as db:
        invoice = db.get(DocumentUploadRecord, UUID(result["invoice_document_id"]))
        packing = db.get(DocumentUploadRecord, UUID(result["packing_list_document_id"]))
        workflow = db.get(CustomsAuditWorkflow, UUID(result["workflow_id"]))
        assert invoice is not None and packing is not None and workflow is not None
        assert isinstance(invoice.structured_data, dict)
        invoice_extraction = invoice.structured_data["phase_2c_commercial_invoice"][
            "extraction"
        ]
        assert invoice_extraction["line_items"][0]["pct_code"]["value"].replace(".", "") == (
            family.pct_code
        )
        assert family.product_name.lower() in (
            invoice_extraction["line_items"][0]["product_name"]["value"].lower()
        )
        assert db.query(ShipmentDocumentChunk).count() >= 4
        events = (
            db.query(CustomsAuditEvent)
            .filter(CustomsAuditEvent.workflow_id == workflow.id)
            .all()
        )
        event_types = {event.event_type for event in events}
        assert "broker_report_created" in event_types
        assert "auditor_report_created" in event_types
        assert "audit_revision_frozen" in event_types


@pytest.mark.parametrize("family", FAMILIES, ids=lambda family: family.pct_code)
def test_fixture_variants_are_generated_without_redundant_committed_pdfs(
    family,
    tmp_path: Path,
) -> None:
    missing_dir = tmp_path / "missing"
    mismatch_dir = tmp_path / "mismatch"
    destination_dir = tmp_path / "destination"
    uncertain_dir = tmp_path / "uncertain"
    missing = materialize_variant(
        family.pct_code, "missing_supporting_document", missing_dir
    )
    mismatch = materialize_variant(
        family.pct_code, "invoice_packing_mismatch", mismatch_dir
    )
    destination = materialize_variant(
        family.pct_code, "destination_condition", destination_dir
    )
    uncertain = materialize_variant(
        family.pct_code, "uncertain_extraction", uncertain_dir
    )

    assert len(missing["documents"]) == 3
    assert not (missing_dir / "certificate_of_origin.pdf").exists()
    assert mismatch["expected_deterministic_status"] == "failed"
    assert destination["shipment"]["destination"] == "Germany"
    assert destination["expected_deterministic_status"] == "manual_review"
    assert "must not be guessed" in uncertain["injected_defect"]
    assert len(list((FINAL_FIXTURE_ROOT / family.pct_code).glob("*.pdf"))) == 4


@pytest.mark.parametrize("family", FAMILIES, ids=lambda family: family.pct_code)
@pytest.mark.parametrize(
    ("variant", "expected_status"),
    [
        ("missing_supporting_document", "failed"),
        ("invoice_packing_mismatch", "failed"),
        ("destination_condition", "manual_review"),
        ("uncertain_extraction", "manual_review"),
    ],
)
def test_controlled_variant_runs_through_real_route_and_preserves_semantics(
    family,
    variant: str,
    expected_status: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_database: Engine,
) -> None:
    _configure_real_route_service(
        monkeypatch,
        isolated_database,
        tmp_path / "uploads",
    )
    fixture_dir = tmp_path / "fixture"
    metadata = materialize_variant(family.pct_code, variant, fixture_dir)
    result = _start_fixture(fixture_dir)
    assert result["deterministic_status"] == expected_status
    findings = result["final_report"]["broker_findings"]

    if variant == "missing_supporting_document":
        assert any(
            "certificate of origin" in item["message"].casefold()
            for item in findings["document_discrepancies"]
        )
    elif variant == "invoice_packing_mismatch":
        mismatch = next(
            check
            for check in findings["deterministic_check_results"]
            if check["check_id"] == "item_gross_weight_match"
        )
        assert mismatch["status"] == "failed"
        document_check = next(
            check
            for check in result["final_report"]["user_report"]["document_evidence"]
            if check["check_id"] == "item_gross_weight_match"
        )
        values = {
            item["extracted_value"] for item in document_check["evidence"]
        }
        assert metadata["shipment"]["gross_weight_kg"] in values
        assert str(
            Decimal(metadata["shipment"]["gross_weight_kg"]) + Decimal("25")
        ) in values
    elif variant == "destination_condition":
        coo_checks = [
            check
            for check in findings["deterministic_check_results"]
            if check["check_id"] == "destination_certificate_of_origin"
        ]
        assert coo_checks and coo_checks[0]["status"] != "failed"
        assert not any(
            "export to china requires" in item["message"].casefold()
            for item in findings["document_discrepancies"]
        )
    else:
        assert any(
            "quantity" in path
            for path in findings["ocr_or_manual_review_fields"]
        )
        with Session(isolated_database) as db:
            invoice = db.get(
                DocumentUploadRecord, UUID(result["invoice_document_id"])
            )
            assert invoice is not None
            assert isinstance(invoice.structured_data, dict)
            extraction = invoice.structured_data["phase_2c_commercial_invoice"][
                "extraction"
            ]
            quantity = extraction["line_items"][0]["quantity"]
            assert quantity["value"] is None
            assert quantity["validation_status"] == "manual_review"
            assert "O" in (invoice.extracted_text or "")


@pytest.mark.parametrize("family", FAMILIES, ids=lambda family: family.pct_code)
def test_shipment_assistant_uses_structured_documents_frozen_audit_and_regulation(
    family,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_database: Engine,
) -> None:
    _configure_real_route_service(
        monkeypatch,
        isolated_database,
        tmp_path / "uploads",
    )
    result = _start_fixture(FINAL_FIXTURE_ROOT / family.pct_code)
    workflow_id = UUID(result["workflow_id"])

    fake_passage = SimpleNamespace(
        child_evidence_text=(
            f"Official tariff evidence for PCT {family.pct_code}."
        ),
        source_document="Pakistan Customs Tariff FY 2025-26",
        page_number=158,
    )
    monkeypatch.setattr(
        shipment_assistant,
        "run_evidence_search",
        lambda _db, _request: SimpleNamespace(
            status="ok",
            results=[fake_passage],
        ),
    )

    with Session(isolated_database) as db:
        workflow = db.get(CustomsAuditWorkflow, workflow_id)
        assert workflow is not None
        frozen_before = copy.deepcopy(workflow.final_report)
        status_before = workflow.status
        deterministic_before = workflow.deterministic_status
        updated_before = workflow.updated_at

    questions = [
        (
            "What is the invoice total?",
            (family.quantity * family.unit_price).quantize(Decimal("0.01")),
        ),
        ("What quantity is declared?", family.quantity),
        (
            "What does the packing list say about gross weight?",
            family.gross_weight,
        ),
        ("What PCT code was extracted?", family.pct_code),
        ("Did the invoice and packing list match?", "Yes"),
        ("Did the shipment pass?", "MANUAL_REVIEW"),
        ("Why did it pass or fail?", "missing legal source field"),
        ("Which documents were checked?", "Commercial Invoice"),
        (
            "What regulatory evidence supports this result?",
            "Pakistan Customs Tariff FY 2025-26",
        ),
    ]
    conversation_id = None
    responses: list[dict[str, Any]] = []
    for question, expected in questions:
        payload: dict[str, Any] = {"question": question}
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        response = client.post(
            f"/api/v1/assistant/shipments/{workflow_id}/chat",
            json=payload,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        conversation_id = body["conversation_id"]
        responses.append(body)
        assert str(expected) in body["answer"]

    assert responses[0]["sources"][0]["source_kind"] == "structured_extraction"
    assert responses[2]["sources"][0]["source_kind"] == "shipment_document"
    assert responses[4]["sources"][0]["source_kind"] == "frozen_audit"
    assert responses[5]["audit_revision_number"] == 1
    assert responses[-1]["sources"][0]["evidence_status"] == "accepted"

    with Session(isolated_database) as db:
        workflow = db.get(CustomsAuditWorkflow, workflow_id)
        assert workflow is not None
        assert workflow.final_report == frozen_before
        assert workflow.status == status_before
        assert workflow.deterministic_status == deterministic_before
        assert workflow.updated_at == updated_before
        assert (
            db.query(AssistantMessage)
            .filter(AssistantMessage.conversation_id == UUID(conversation_id))
            .count()
            == 18
        )


def test_shipment_assistant_refuses_off_topic_coding_question(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_database: Engine,
) -> None:
    _configure_real_route_service(
        monkeypatch,
        isolated_database,
        tmp_path / "uploads",
    )
    result = _start_fixture(FINAL_FIXTURE_ROOT / "62034200")
    response = client.post(
        f"/api/v1/assistant/shipments/{result['workflow_id']}/chat",
        json={"question": "Write Python code for a sorting algorithm."},
    )
    assert response.status_code == 200
    assert response.json()["answer_type"] == "out_of_scope"
    assert "outside the scope" in response.json()["answer"]
