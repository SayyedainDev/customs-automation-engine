"""Regression tests for the four-document (invoice, packing list, Form E, COO) review.

Each test here pins one defect found in a live review of a raw-cotton
shipment where all four documents were uploaded and correct, yet the result
reported the exporter and consignee as unreadable, blamed the uploaded files
for paperwork that was simply not in hand, and reported one missing letter of
credit as two separate problems.

The document text below is the text of the real uploaded documents, kept
verbatim (including the "Exporter" / "Exporter address" label pairing that
caused the collision) so a future parser change cannot quietly reintroduce it.
"""

from uuid import uuid4

from app.schemas.compliance import ComplianceCheckStatus
from app.schemas.supporting_documents import SupportingDocumentType
from app.services.compliance.document_requirements import (
    collect_outstanding_documents,
    is_outstanding_document_check,
)
from app.services.extraction import supporting_document_hybrid as hybrid
from app.services.extraction.document_bundle import DocumentTextBundle, StoredPage
from app.services.extraction.patterns import normalise_org
from app.services.extraction.regex_extractor import extract_field


REAL_INVOICE_TEXT = """SYNTHETIC TEST DOCUMENT
COMMERCIAL INVOICE
CACE TEST - Raw cotton, other - PCT 5201.0090
Exporter
Multan Raw Cotton Traders (Pvt.) Ltd.
Exporter address
Multan Industrial Estate, Punjab, Pakistan
Buyer / Consignee
Al Ain Fibre Trading LLC
Buyer address
Industrial Area, Al Ain, UAE
Invoice number
MRC-INV-2026-101
Invoice date
2026-08-02
Destination country
United Arab Emirates
Currency
USD
"""

REAL_FORM_E_AMOUNT_TEXT = """FORM E / PSW EXPORT DECLARATION
Form E number
SYN-PSW-52010090-01
Exporter
Multan Raw Cotton Traders (Pvt.) Ltd.
Declared export value
USD 2000.00
"""


def _page_bundle(text: str) -> DocumentTextBundle:
    return DocumentTextBundle(
        document_id=uuid4(),
        document_type="supporting_document",
        pages=[
            StoredPage(
                page_number=1,
                text=text,
                original_embedded_text=text,
                extraction_method="pdf_embedded_text",
                ocr_confidence=None,
                ocr_validation_status=None,
            )
        ],
        reviews=[],
    )


def test_address_label_does_not_block_the_exporter_and_consignee_names() -> None:
    """"Exporter" and "Exporter address" are two labels, not one ambiguity.

    Both matched the exporter-name pattern, so extraction saw two candidate
    values - the real company and the bare word "address" from the second
    label's own line - and refused to choose, reporting the exporter as
    unreadable on an invoice that states it plainly.
    """
    exporter = extract_field("exporter_name", REAL_INVOICE_TEXT)
    consignee = extract_field("consignee_name", REAL_INVOICE_TEXT)

    assert exporter.value == "Multan Raw Cotton Traders (Pvt.) Ltd"
    assert exporter.confidence == "high"
    assert consignee.value == "Al Ain Fibre Trading LLC"
    assert consignee.confidence == "high"


def test_address_field_still_reads_its_own_value() -> None:
    """The fix must not be "ignore anything near the word address"."""
    address = extract_field("exporter_address", REAL_INVOICE_TEXT)
    assert address.value == "Multan Industrial Estate, Punjab, Pakistan"


def test_a_real_name_containing_a_heading_word_survives() -> None:
    """Only captures made *entirely* of heading vocabulary are rejected."""
    assert normalise_org("Karachi Address Exporters Ltd") == (
        "Karachi Address Exporters Ltd"
    )
    assert normalise_org("address") is None
    assert normalise_org("Name and Address") is None


def test_form_e_declared_export_value_needs_no_gap_fill_call() -> None:
    """The amount is on the page; reading it must not cost a Groq call."""
    extraction = hybrid.extract_deterministically(
        _page_bundle(REAL_FORM_E_AMOUNT_TEXT),
        SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION,
    )

    amount = extraction.fields["amount"]
    assert amount.value is not None
    assert str(amount.value) == "2000.00"
    # The excerpt above deliberately omits the invoice reference, so only the
    # amount is asserted to be resolved - it is the field that used to spend a
    # gap-fill call on a figure the page states outright.
    assert "amount" not in extraction.unresolved_important_fields()


class _Check:
    """Minimal stand-in for a built compliance check result."""

    def __init__(
        self,
        check_id: str,
        status: str,
        required_document: str | None,
        message: str = "unverifiable",
    ):
        self.check_id = check_id
        self.check_name = check_id
        self.status = status
        self.message = message
        self.required_document = required_document
        self.source_document = "SRO 2486(I)/2025"
        self.sro_number = "2486(I)/2025"
        self.source_page = 1


def test_unverifiable_180_day_window_is_paperwork_not_a_document_defect() -> None:
    """An absent letter of credit belongs on the checklist, not the findings.

    Without a letter of credit there is no date to measure the 180-day window
    from. The check reported this as a finding about the uploaded files, which
    told the exporter their correct invoice and packing list were at fault.
    Naming the required document routes it to "still to obtain" instead.
    """
    check = _Check(
        "raw_cotton_shipment_within_180_days",
        ComplianceCheckStatus.MANUAL_REVIEW.value,
        "irrevocable_letter_of_credit",
    )
    assert is_outstanding_document_check(check) is True


def test_held_letter_of_credit_with_an_unread_date_is_not_paperwork_to_obtain() -> None:
    """Holding the document but not its date is a different problem.

    Routing the unverifiable window to the "still to obtain" checklist is only
    right when the letter of credit was never supplied. If it was uploaded and
    only its date could not be read, telling the exporter to go and obtain a
    document they already hold would be wrong, so the check stays a finding.
    """
    check = _Check(
        "raw_cotton_shipment_within_180_days",
        ComplianceCheckStatus.MANUAL_REVIEW.value,
        None,
        "The 180-day shipment window could not be checked because the "
        "letter-of-credit date could not be read from the documents provided.",
    )
    assert is_outstanding_document_check(check) is False


def test_executable_deadline_twin_also_points_at_the_missing_document() -> None:
    """The legacy rule and its executable twin must agree.

    Both the legacy raw-cotton rule and the generated executable rule check
    the same 180-day window. Fixing only the legacy one left the twin in the
    findings panel, so the exporter still saw their correct invoice blamed
    for an absent letter of credit.
    """
    from app.services.compliance.executable_rule_checks import _ANCHOR_DOCUMENTS

    assert _ANCHOR_DOCUMENTS["letter_of_credit_date"] == "irrevocable_letter_of_credit"


def test_one_missing_letter_of_credit_is_reported_once() -> None:
    """Rules naming the same document are one thing for the exporter."""
    outstanding = collect_outstanding_documents(
        [
            _Check(
                "xr_52010090_shipment_within_180_days",
                ComplianceCheckStatus.MANUAL_REVIEW.value,
                "irrevocable_letter_of_credit",
                "Raw cotton shipment within 180 days: the letter of credit has "
                "not been provided.",
            ),
            _Check(
                "raw_cotton_irrevocable_letter_of_credit",
                ComplianceCheckStatus.FAILED.value,
                "irrevocable_letter_of_credit",
                "Raw cotton requires an irrevocable letter of credit.",
            ),
            _Check(
                "raw_cotton_shipment_within_180_days",
                ComplianceCheckStatus.MANUAL_REVIEW.value,
                "irrevocable_letter_of_credit",
                "The 180-day shipment window could not be checked.",
            ),
        ]
    )

    assert len(outstanding) == 1
    assert outstanding[0].document_type == "irrevocable_letter_of_credit"
    # Every rule's citation is kept against the single entry.
    assert len(outstanding[0].reasons) == 3


# --------------------------------------------------------------------------- #
# Hybrid extraction: fewer tokens, because fewer calls are needed at all
# --------------------------------------------------------------------------- #
REAL_FORM_E_FULL = """SYNTHETIC TEST DOCUMENT
FORM E EXPORT DECLARATION
Form E number
SYN-PSW-52010090-01
Issue date
2026-07-28
Exporter
Multan Raw Cotton Traders (Pvt.) Ltd.
Consignee
Al Ain Fibre Trading LLC
Destination country
United Arab Emirates
Authorised Dealer
Habib Bank Limited, Multan Branch
Related invoice
MRC-INV-2026-101
Declared export value
USD 2000.00
Currency
USD
"""


def test_a_complete_form_e_needs_no_provider_call_at_all() -> None:
    """The whole point of the hybrid path is not to call the model.

    "Related invoice" had no alias, so the invoice reference stayed unresolved
    on a document that prints it, and a gap-fill call was made for a value
    already on the page.
    """
    extraction = hybrid.extract_deterministically(
        _page_bundle(REAL_FORM_E_FULL),
        SupportingDocumentType.FORM_E_OR_PSW_EXPORT_DECLARATION,
    )

    assert extraction.fields["invoice_reference"].value == "MRC-INV-2026-101"
    assert extraction.unresolved_important_fields() == []


def test_the_completion_ceiling_matches_what_was_asked_for() -> None:
    """A gap-fill reply is a flat object of a few values, not 512 tokens.

    The reservation is made from the ceiling, so a flat 512 made two small
    documents reserve nearly a thousand tokens to produce about twenty - budget
    the other documents in the same review then could not use.
    """
    one = hybrid._completion_ceiling(["invoice_reference"])
    two = hybrid._completion_ceiling(["invoice_reference", "amount"])

    assert one < two < 512
    # Never below a floor that could truncate a valid reply, and never above
    # the configured cap.
    assert one >= 128
    assert hybrid._completion_ceiling(["a"] * 50) <= 512


def test_completion_budget_scales_with_the_document() -> None:
    """A 700-character packing list cannot emit 2,000 tokens of JSON.

    The provider reserves against the ceiling, not the reply, so a flat 2,000
    made each extraction request 4,075 tokens - and two of them exceed an
    8,000-token-per-minute tier. That is why a first review of four documents
    was rate limited while a repeat, served from the extraction cache,
    succeeded.
    """
    from app.services.structured_extraction_service import (
        completion_ceiling_for_text,
    )

    small = completion_ceiling_for_text("x" * 700)
    medium = completion_ceiling_for_text("x" * 1200)
    large = completion_ceiling_for_text("x" * 12_000)

    assert small < medium < large
    # A small document costs materially less than the flat budget did.
    assert small < 1_400
    # A large one is unaffected: it still gets the full configured budget.
    assert large == 2_000
    # Never so small that a valid reply would be truncated.
    assert completion_ceiling_for_text("") >= 600


def test_completion_budget_leaves_headroom_over_a_realistic_reply() -> None:
    """Under-reserving truncates the JSON and fails the whole extraction.

    Worse than reserving too much, so the multiplier is checked against a
    fully populated multi-line payload rather than a minimal one.
    """
    import json

    from app.services.structured_extraction_service import (
        completion_ceiling_for_text,
    )

    field = {
        "value": "Cotton bed sheets, mill-made, printed",
        "source_page": 1,
        "confidence": 0.93,
        "validation_status": "verified",
        "validation_note": "Read from the goods table on page 1.",
    }
    payload = {"line_items": [dict.fromkeys(range(6), field)]}
    reply_tokens = len(json.dumps(payload)) // 4

    assert completion_ceiling_for_text("x" * 700) > reply_tokens * 1.3
