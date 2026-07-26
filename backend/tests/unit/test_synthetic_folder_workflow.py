import json
from pathlib import Path

import pytest

from scripts.run_synthetic_folder_workflow import (
    DocumentPair,
    TerminalRunnerError,
    build_review_payload,
    discover_document_pairs,
    load_workflow_defaults,
    narrate_event,
    report_sections,
)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_discovers_and_pairs_text_and_scanned_documents(tmp_path: Path) -> None:
    _touch(tmp_path / "synthetic_commercial_invoice_text.pdf")
    _touch(tmp_path / "synthetic_packing_list_text.pdf")
    _touch(tmp_path / "synthetic_commercial_invoice_scanned.pdf")
    _touch(tmp_path / "synthetic_packing_list_scanned.pdf")

    pairs, ignored = discover_document_pairs(tmp_path)

    assert [pair.key for pair in pairs] == ["scanned", "text"]
    assert pairs[0].invoice_path.name.endswith("invoice_scanned.pdf")
    assert pairs[0].packing_list_path.name.endswith("list_scanned.pdf")
    assert ignored == []


def test_nested_bundles_do_not_cross_pair(tmp_path: Path) -> None:
    for bundle in ("bundle_a", "bundle_b"):
        _touch(tmp_path / bundle / "commercial_invoice.pdf")
        _touch(tmp_path / bundle / "packing_list.pdf")

    pairs, _ = discover_document_pairs(tmp_path)

    assert [pair.key for pair in pairs] == [
        "bundle_a/default",
        "bundle_b/default",
    ]


def test_incomplete_pair_is_rejected(tmp_path: Path) -> None:
    _touch(tmp_path / "commercial_invoice_text.pdf")

    with pytest.raises(TerminalRunnerError, match="missing packing list"):
        discover_document_pairs(tmp_path)


def test_request_defaults_are_loaded_without_document_placeholders(
    tmp_path: Path,
) -> None:
    invoice = _touch(tmp_path / "commercial_invoice_text.pdf")
    packing = _touch(tmp_path / "packing_list_text.pdf")
    (tmp_path / "multi_line_api_request.json").write_text(
        json.dumps(
            {
                "commercial_invoice_document_id": "<placeholder>",
                "packing_list_document_id": "<placeholder>",
                "shipment_date": "2026-07-20",
                "letter_of_credit_date": None,
                "additional_uploaded_document_types": [
                    "form_e",
                    "certificate_of_origin",
                ],
            }
        ),
        encoding="utf-8",
    )
    pair = DocumentPair("text", invoice, packing)

    defaults = load_workflow_defaults(pair)

    assert defaults.shipment_date == "2026-07-20"
    assert defaults.letter_of_credit_date is None
    assert defaults.additional_uploaded_document_types == (
        "form_e",
        "certificate_of_origin",
    )


def test_review_payload_carries_human_correction_without_document_ids() -> None:
    payload = build_review_payload(
        action="correct_extracted_value",
        reviewer_reference="reviewer-1",
        field_path="invoice.line_items[1].quantity",
        original_value="100",
        corrected_value="99",
        reason="Checked page 1",
        source="invoice page 1",
    )

    assert payload["action"] == "correct_extracted_value"
    assert payload["original_value"] == "100"
    assert payload["corrected_value"] == "99"
    assert payload["provided_document_ids"] == []


def test_event_narration_is_plain_english() -> None:
    lines = narrate_event(
        {
            "event_type": "auditor_report_created",
            "event_payload": {
                "recommended_action": "continue",
                "evidence_support": "full",
                "violations": [],
            },
        }
    )

    assert lines and "Auditor agent" in lines[0]
    assert "no violations" in lines[0]
    # No raw internal identifiers leak into the narration.
    assert "auditor_report_created" not in lines[0]


def test_report_sections_render_business_summary_from_user_report() -> None:
    status = {
        "status": "failed",
        "final_report": {
            "user_report": {
                "overall_result": "FAILED",
                "overall_reason": "The shipment failed because form-e is missing.",
                "shipment_summary": {"destination": "China", "exporter": "ACME"},
                "line_items": [
                    {"line_number": 1, "product_name": "Cotton knitted T-shirts"}
                ],
                "checks_passed": ["Quantity"],
                "problems": {"missing_documents": ["Form-E is missing."]},
                "required_actions": ["Upload the Form-E."],
                "compliance_evidence": [],
                "workflow_summary": ["Broker completed extraction."],
            }
        },
    }
    headings = [heading for heading, _ in report_sections(status)]
    assert "Overall Result" in headings
    assert "Shipment Summary" in headings
    assert "Problems Found" in headings
    assert "Required Action" in headings
    body = "\n".join(
        line for _, lines in report_sections(status) for line in lines
    )
    assert "China" in body
    assert "Form-E is missing." in body
    assert "Upload the Form-E." in body
