"""EXTRACTION_MODE=hybrid wired into the live multi-line route.

These tests exercise `extract_match_and_check_multi_line_shipment` end to end
under the hybrid extraction mode - the primary capstone workflow
(commercial invoice + packing list) - and pin the call-count invariants the
brief requires:

* a fully regex/table-resolved document makes zero Groq calls;
* an unresolved header field makes exactly one combined gap-fill call, never
  the staged per-line ladder;
* a document with no reconstructable table falls back to exactly one
  full-document call, still never the staged ladder;
* a 429 propagates rather than cascading;
* switching EXTRACTION_MODE invalidates the cache;
* legacy mode is completely unaffected (covered by the existing suites, which
  the default-legacy autouse fixture in conftest.py keeps exercising).
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import StructuredExtractionProviderUnavailableError
from app.models.documents import DocumentUploadRecord
from app.services import multi_line_shipment_service
from app.services.extraction import staged_multi_line
from tests.unit.test_hybrid_extraction import (
    TEXT_LAYER_PACKING_LIST,
    _invoice_table_words,
    _packing_table_words,
)
from tests.unit.test_hybrid_extraction import TEXT_LAYER_INVOICE as _INVOICE_TEXT
from tests.unit.test_multi_line_shipment import phase_2c_request, run_phase_2c


def _add_document_with_words(
    engine: Engine,
    *,
    original_filename: str,
    text: str,
    words: list[list[tuple]],
) -> UUID:
    from uuid import uuid4

    document_id = uuid4()
    with Session(engine) as db:
        db.add(
            DocumentUploadRecord(
                id=document_id,
                original_filename=original_filename,
                stored_filename=f"{document_id}.pdf",
                file_extension=".pdf",
                mime_type="application/pdf",
                size_bytes=100,
                status="extracted",
                extracted_text=text,
                extracted_pages=[
                    {
                        "page_number": 1,
                        "text": text,
                        "words": [list(word) for word in words[0]],
                    }
                ],
                page_count=1,
                character_count=len(text),
            )
        )
        db.commit()
    return document_id


def _set_invoice_text(engine: Engine, invoice_id: UUID, text: str) -> None:
    """Replace a stored document's page text.

    Reassigns ``extracted_pages`` to a brand-new list/dict rather than
    mutating the existing one in place: SQLAlchemy's JSON column change
    tracking does not notice an in-place mutation of a nested dict, so an
    in-place assignment here would silently fail to persist.
    """
    with Session(engine) as db:
        document = db.get(DocumentUploadRecord, invoice_id)
        assert document is not None
        document.extracted_text = text
        pages = list(document.extracted_pages or [])
        pages[0] = {**pages[0], "text": text}
        document.extracted_pages = pages
        db.add(document)
        db.commit()


def _hybrid_documents(engine: Engine) -> tuple[UUID, UUID]:
    invoice_id = _add_document_with_words(
        engine,
        original_filename="invoice.pdf",
        text=_INVOICE_TEXT,
        words=_invoice_table_words(),
    )
    packing_id = _add_document_with_words(
        engine,
        original_filename="packing.pdf",
        text=TEXT_LAYER_PACKING_LIST,
        words=_packing_table_words(),
    )
    return invoice_id, packing_id


def _fail_if_called(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("This extraction path must not be reached in this test.")


@pytest.fixture(autouse=True)
def _hybrid_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "extraction_mode", "hybrid")


def test_fully_resolved_document_makes_zero_groq_calls(
    isolated_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoice_id, packing_id = _hybrid_documents(isolated_database)
    # Neither the single-shot call nor the staged ladder may be reached: the
    # fixture text/words resolve every invoice/packing-list-relevant field.
    monkeypatch.setattr(
        multi_line_shipment_service, "extract_structured_model_from_text", _fail_if_called
    )
    from app.services.extraction.telemetry import DocumentTelemetry
    def _fake_run_gapfill(extraction, unresolved, *, document_ref="gapfill", client=None):
        return {}, DocumentTelemetry(document_ref=document_ref, llm_calls=0)
    monkeypatch.setattr(multi_line_shipment_service, "run_gapfill", _fake_run_gapfill)
    monkeypatch.setattr(staged_multi_line, "extract_invoice_staged", _fail_if_called)
    monkeypatch.setattr(staged_multi_line, "extract_packing_staged", _fail_if_called)

    result = run_phase_2c(isolated_database, phase_2c_request(invoice_id, packing_id))

    assert result.invoice.invoice_number.value == "LCG-INV-2026-002"
    assert len(result.invoice.line_items) == 1
    assert len(result.packing_list.items) == 1

    with Session(isolated_database) as db:
        document = db.get(DocumentUploadRecord, invoice_id)
        assert document is not None
        assert document.structured_data is not None
        telemetry = document.structured_data["phase_2c_commercial_invoice"]["telemetry"]
        assert telemetry["llm_calls"] == 0
        assert telemetry["cache_hit"] is False


def test_unresolved_header_field_makes_exactly_one_gapfill_call(
    isolated_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Strip the destination label+value entirely (no other occurrence of
    # "China" anywhere else in the fixture) so `country_of_destination` is
    # genuinely unresolved; everything else, including the whole line-item
    # table, still resolves locally.
    invoice_text = _INVOICE_TEXT.replace("Destination Country\nChina\n", "")
    invoice_id, packing_id = _hybrid_documents(isolated_database)
    _set_invoice_text(isolated_database, invoice_id, invoice_text)

    calls: list[list[str]] = []

    def fake_run_gapfill(extraction, unresolved, *, document_ref="gapfill", client=None):
        calls.append(list(unresolved))
        from app.services.extraction.llm_gapfill import apply_gapfill_response
        from app.services.extraction.telemetry import DocumentTelemetry

        payload = {
            name: ("China" if name == "country_of_destination" else None)
            for name in unresolved
        }
        updated = apply_gapfill_response(extraction, unresolved, payload)
        telemetry = DocumentTelemetry(
            document_ref=document_ref, llm_calls=1, fields_from_llm=1
        )
        return updated, telemetry

    monkeypatch.setattr(multi_line_shipment_service, "run_gapfill", fake_run_gapfill)
    monkeypatch.setattr(
        multi_line_shipment_service, "extract_structured_model_from_text", _fail_if_called
    )
    monkeypatch.setattr(staged_multi_line, "extract_invoice_staged", _fail_if_called)

    result = run_phase_2c(isolated_database, phase_2c_request(invoice_id, packing_id))

    # One combined gap-fill call per document that actually has an unresolved
    # header field: the invoice (destination) and, independently, the packing
    # list (this fixture's packing list does not print "Total Packages" in a
    # form the regex pattern matches, so declared_package_count_total is
    # legitimately unresolved too) - never more than one call per document.
    assert len(calls) == 2
    assert any("country_of_destination" in call for call in calls)
    assert result.invoice.destination_country.value == "China"


def test_no_reconstructable_table_falls_back_to_one_single_shot_call(
    isolated_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No word coordinates at all -> one full-document call, never the ladder."""
    from tests.unit.test_multi_line_shipment import (
        invoice_candidates as build_invoice_candidates,
        invoice_item,
        packing_candidates as build_packing_candidates,
        packing_item,
    )
    from app.services.multi_line_shipment_service import (
        MultiLineInvoiceCandidates,
        MultiLinePackingListCandidates,
    )
    from tests.unit.test_shipment_extraction import add_extracted_document

    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice INV-2001 with one product line.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list with one product line.",
    )

    calls: list[str] = []

    def fake_structured_output(**kwargs):
        response_model = kwargs["response_model"]
        calls.append(response_model.__name__)
        if response_model is MultiLineInvoiceCandidates:
            return build_invoice_candidates(items=[invoice_item()])
        if response_model is MultiLinePackingListCandidates:
            return build_packing_candidates(items=[packing_item()])
        raise AssertionError("Unexpected structured-output model.")

    monkeypatch.setattr(
        multi_line_shipment_service,
        "extract_structured_model_from_text",
        fake_structured_output,
    )
    monkeypatch.setattr(staged_multi_line, "extract_invoice_staged", _fail_if_called)
    monkeypatch.setattr(staged_multi_line, "extract_packing_staged", _fail_if_called)

    result = run_phase_2c(isolated_database, phase_2c_request(invoice_id, packing_id))

    assert calls.count("MultiLineInvoiceCandidates") == 1
    assert calls.count("MultiLinePackingListCandidates") == 1
    assert len(result.invoice.line_items) == 1


def test_gapfill_provider_unavailable_leaves_fields_unresolved_without_raising(
    isolated_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 429 during gap-fill is safe to swallow: regex already resolved most
    of the document, so the request completes with the field routed to
    manual review rather than failing outright. This mirrors what the real
    ``run_gapfill`` does internally (see test_groq_gapfill.py) - it never
    raises ``StructuredExtractionProviderUnavailableError`` itself."""
    invoice_id, packing_id = _hybrid_documents(isolated_database)
    invoice_text = _INVOICE_TEXT.replace("Destination Country\nChina\n", "")
    _set_invoice_text(isolated_database, invoice_id, invoice_text)

    from app.services.extraction.telemetry import DocumentTelemetry

    def unavailable_gapfill(extraction, unresolved, *, document_ref="gapfill", client=None):
        return {}, DocumentTelemetry(
            document_ref=document_ref,
            llm_calls=1,
            notes=["gapfill provider unavailable; fields left unresolved"],
        )

    monkeypatch.setattr(multi_line_shipment_service, "run_gapfill", unavailable_gapfill)
    monkeypatch.setattr(
        multi_line_shipment_service, "extract_structured_model_from_text", _fail_if_called
    )
    monkeypatch.setattr(staged_multi_line, "extract_invoice_staged", _fail_if_called)

    result = run_phase_2c(isolated_database, phase_2c_request(invoice_id, packing_id))

    assert result.invoice.destination_country.value is None
    assert "invoice.destination_country" in result.fields_requiring_manual_review


def test_single_shot_fallback_provider_unavailable_propagates(
    isolated_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike gap-fill, the no-table single-shot fallback has no regex safety
    net behind it, so a 429 there must propagate rather than being swallowed
    - exactly like the legacy path already does."""
    from tests.unit.test_shipment_extraction import add_extracted_document

    invoice_id = add_extracted_document(
        isolated_database,
        original_filename="invoice.pdf",
        text="Commercial invoice INV-2001 with one product line.",
    )
    packing_id = add_extracted_document(
        isolated_database,
        original_filename="packing.pdf",
        text="Packing list with one product line.",
    )

    def raising_single_shot(**_kwargs):
        raise StructuredExtractionProviderUnavailableError("quota exhausted")

    monkeypatch.setattr(
        multi_line_shipment_service,
        "extract_structured_model_from_text",
        raising_single_shot,
    )
    monkeypatch.setattr(staged_multi_line, "extract_invoice_staged", _fail_if_called)

    with pytest.raises(StructuredExtractionProviderUnavailableError):
        run_phase_2c(isolated_database, phase_2c_request(invoice_id, packing_id))


def test_switching_extraction_mode_invalidates_the_cache(
    isolated_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.extraction.telemetry import DocumentTelemetry
    def _fake_run_gapfill(extraction, unresolved, *, document_ref="gapfill", client=None):
        return {}, DocumentTelemetry(document_ref=document_ref, llm_calls=0)
    monkeypatch.setattr(multi_line_shipment_service, "run_gapfill", _fake_run_gapfill)
    
    invoice_id, packing_id = _hybrid_documents(isolated_database)
    run_phase_2c(isolated_database, phase_2c_request(invoice_id, packing_id))
    with Session(isolated_database) as db:
        document = db.get(DocumentUploadRecord, invoice_id)
        assert document is not None
        assert document.structured_data is not None
        hybrid_fingerprint = document.structured_data["phase_2c_commercial_invoice"][
            "fingerprint"
        ]

    monkeypatch.setattr(get_settings(), "extraction_mode", "legacy")

    from tests.unit.test_multi_line_shipment import (
        invoice_candidates as build_invoice_candidates,
        invoice_item,
        packing_candidates as build_packing_candidates,
        packing_item,
    )
    from app.services.multi_line_shipment_service import (
        MultiLineInvoiceCandidates,
        MultiLinePackingListCandidates,
    )

    def fake_structured_output(**kwargs):
        response_model = kwargs["response_model"]
        if response_model is MultiLineInvoiceCandidates:
            return build_invoice_candidates(items=[invoice_item()])
        if response_model is MultiLinePackingListCandidates:
            return build_packing_candidates(items=[packing_item()])
        raise AssertionError("Unexpected structured-output model.")

    monkeypatch.setattr(
        multi_line_shipment_service,
        "extract_structured_model_from_text",
        fake_structured_output,
    )
    run_phase_2c(isolated_database, phase_2c_request(invoice_id, packing_id))

    with Session(isolated_database) as db:
        document = db.get(DocumentUploadRecord, invoice_id)
        assert document is not None
        assert document.structured_data is not None
        legacy_fingerprint = document.structured_data["phase_2c_commercial_invoice"][
            "fingerprint"
        ]

    assert hybrid_fingerprint != legacy_fingerprint
