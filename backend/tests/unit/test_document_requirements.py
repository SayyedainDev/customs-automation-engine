"""Sorting already-computed checks into 'defective upload' vs 'paperwork due'.

The rules themselves are untouched by this module, so these tests only pin the
sorting: which failures describe the two uploaded documents, and which describe
a customs document that has to be obtained elsewhere.
"""

from __future__ import annotations

from app.services.compliance.document_requirements import (
    collect_outstanding_documents,
    is_outstanding_document_check,
)


def _check(**overrides):
    check = {
        "check_id": "required_document_form_e",
        "check_name": "Form-E is present",
        "status": "failed",
        "message": "Missing required document: Form-E.",
        "required_document": "form_e",
        "source_document": "TIPP Customs Clearance Procedure",
        "sro_number": None,
        "source_page": None,
    }
    check.update(overrides)
    return check


def test_a_missing_supporting_document_is_outstanding_paperwork():
    assert is_outstanding_document_check(_check()) is True


def test_b_a_missing_input_document_is_not_outstanding_paperwork():
    """The invoice and packing list are what the review consumes.

    Their absence is a defect in the submission to this tool, not a customs
    document to go and collect, so it must keep failing the document review.
    """
    assert (
        is_outstanding_document_check(
            _check(check_id="required_document_packing_list", required_document="packing_list")
        )
        is False
    )


def test_c_a_passed_document_rule_is_not_outstanding():
    assert is_outstanding_document_check(_check(status="passed")) is False


def test_d_a_check_without_a_required_document_is_not_outstanding():
    assert (
        is_outstanding_document_check(
            _check(check_id="item_quantity_match", required_document=None)
        )
        is False
    )


def test_e_rules_naming_the_same_document_produce_one_checklist_entry():
    """Legacy, destination and executable rule layers each require Form-E.

    The exporter still has to obtain exactly one document, so the checklist
    shows one entry - but every rule's citation is kept against it.
    """
    documents = collect_outstanding_documents(
        [
            _check(),
            _check(
                check_id="form_e_required_for_export_clearance",
                check_name="Form-E required for export clearance",
                message="Form-E required for export clearance: the required document 'form_e' is missing.",
                source_document="Export Policy Order 2022",
            ),
        ]
    )

    assert len(documents) == 1
    assert documents[0].display_name == "Form-E / PSW export declaration"
    assert documents[0].requirement == "required"
    assert len(documents[0].reasons) == 2
    assert documents[0].sources == [
        "TIPP Customs Clearance Procedure",
        "Export Policy Order 2022",
    ]


def test_f_an_undecidable_document_rule_is_conditional_not_required():
    documents = collect_outstanding_documents(
        [
            _check(
                check_id="conditional_certificate_of_origin",
                check_name="Conditional certificate of origin",
                status="manual_review",
                message="This requirement is conditional and cannot be decided from the shipment fields alone.",
                required_document="certificate_of_origin",
            )
        ]
    )

    assert [document.requirement for document in documents] == ["conditional"]


def test_g_one_firm_rule_outranks_a_conditional_one_for_the_same_document():
    documents = collect_outstanding_documents(
        [
            _check(
                check_id="conditional_certificate_of_origin",
                status="manual_review",
                required_document="certificate_of_origin",
                message="Conditional for other destinations.",
            ),
            _check(
                check_id="certificate_of_origin_china_cpfta",
                status="failed",
                required_document="certificate_of_origin",
                message="Export to China requires a certificate of origin under CPFTA.",
            ),
        ]
    )

    assert [document.requirement for document in documents] == ["required"]
