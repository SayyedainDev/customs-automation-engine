"""Scenario evidence for deterministic supporting-document hybrid extraction."""

from __future__ import annotations

from uuid import uuid4

from app.schemas.shipment_extraction import FieldValidationStatus
from app.schemas.supporting_documents import (
    SupportingDocumentCandidates,
    SupportingDocumentType,
)
from app.services.extraction.document_bundle import DocumentTextBundle, StoredPage
from app.services.extraction.supporting_document_hybrid import (
    DETERMINISTIC_HIGH_CONFIDENCE,
    DeterministicField,
    extract_deterministically,
    merge_gapfill,
    select_snippets,
    to_candidates,
)


def _bundle(document_type: SupportingDocumentType, page_text: str) -> DocumentTextBundle:
    return DocumentTextBundle(
        document_id=uuid4(),
        document_type=document_type.value,
        pages=[StoredPage(page_number=1, text=page_text)],
        reviews=[],
    )


def test_form_e_deterministic_extraction_preserves_provenance_and_no_verdict() -> None:
    bundle = _bundle(
        SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION,
        "\n".join(
            [
                "FORM-E EXPORT DECLARATION",
                "Form-E Number: PSW-FE-2026-0091",
                "Exporter Name: Faisalabad Home Textiles (Pvt.) Ltd.",
                "Buyer / Consignee: Hangzhou Home Living Co., Ltd.",
                "PCT Code: 6302.3110",
                "Currency: USD",
                "Declared Amount: 13500.00",
                "Invoice Reference: FHT-INV-2026-305",
            ]
        ),
    )

    extraction = extract_deterministically(
        bundle,
        SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION,
    )
    assert extraction.fields["document_number"].resolved is True
    assert extraction.fields["document_number"].value == "PSW-FE-2026-0091"
    assert extraction.fields["document_number"].confidence >= DETERMINISTIC_HIGH_CONFIDENCE
    assert extraction.fields["pct_code"].value == "63023110"

    candidates = to_candidates(extraction)
    assert isinstance(candidates, SupportingDocumentCandidates)
    assert candidates.document_number.validation_status == FieldValidationStatus.VERIFIED
    assert "overall_status" not in candidates.model_dump()


def test_coo_gapfill_snippets_and_conflict_preservation() -> None:
    bundle = _bundle(
        SupportingDocumentType.CERTIFICATE_OF_ORIGIN,
        "\n".join(
            [
                "CERTIFICATE OF ORIGIN (CPFTA)",
                "Certificate Number: LCCI-COO-2026-771",
                "Exporter Name: Lahore Cotton Garments (Pvt.) Ltd.",
                "Country of Origin: Pakistan",
                "Destination Country: China",
            ]
        ),
    )

    extraction = extract_deterministically(
        bundle,
        SupportingDocumentType.CERTIFICATE_OF_ORIGIN,
    )
    unresolved = [
        name
        for name, field_state in extraction.fields.items()
        if name != "detected_document_type" and not field_state.resolved
    ]
    snippets = select_snippets(extraction, unresolved)
    assert len(snippets) > 0

    existing_doc_number = extraction.fields["document_number"].normalized_value
    conflicts = merge_gapfill(
        extraction,
        {
            "document_number": DeterministicField(
                value="ALT-COO-999",
                normalized_value="ALT-COO-999",
                method="llm_gapfill",
                validation_status="valid",
            )
        },
    )
    assert conflicts == ["document_number"]
    assert extraction.fields["document_number"].normalized_value == existing_doc_number
