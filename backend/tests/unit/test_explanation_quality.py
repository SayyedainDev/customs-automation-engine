"""Plain-language explanation quality tests.

These pin down the six-section, non-technical report structure and the
validation gate that keeps a narrator answer honest: grounded in the frozen
findings, free of raw identifiers and unexplained jargon, and never claiming
an authority this software does not have. All of it is pure-function or
in-memory-database work - no Groq call is made anywhere in this file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Engine

from app.services.customs_audit.explanation import (
    _default_explanation,
    build_explanation_findings,
    explanation_exposes_raw_identifiers,
    explanation_has_prohibited_claims,
    explanation_has_ungrounded_facts,
    explanation_has_ungrounded_sro,
    explanation_has_unexplained_jargon,
    explanation_is_vague_for_passed,
    explanation_meets_bar,
    explanation_missing_limitation,
    explanation_validation_failure,
    generate_explanation_entry,
)
from app.services.customs_audit.report import EVIDENCE_SEARCH_EXPLANATION

from tests.unit.test_customs_audit import (
    PASSED_LEGAL_CHECK,
    accept_decision,
    line,
    make_extraction,
    make_service,
    review,
    start,
)


def _state(extraction: dict[str, Any], *, status: str) -> dict[str, Any]:
    return {
        "extraction_result": extraction,
        "deterministic_compliance_result": {"overall_status": status},
        "manual_review_reasons": [] if status != "manual_review" else ["needs confirmation"],
        "human_review_decision": None,
    }


def _findings_for(extraction: dict[str, Any], *, status: str) -> dict[str, Any]:
    return build_explanation_findings(
        _state(extraction, status=status),
        {"deterministic_compliance_status": status},
    )


def _passed_extraction() -> dict[str, Any]:
    return make_extraction(
        [
            line(
                status="passed",
                compliance_checks=[PASSED_LEGAL_CHECK],
                item_checks=[{"check_id": "item_quantity_match", "status": "passed"}],
            )
        ],
        "passed",
    )


def _four_document_extraction() -> dict[str, Any]:
    extraction = _passed_extraction()
    extraction["supporting_documents"] = [
        {
            "claimed_document_type": "form_e",
            "canonical_document_type": "form_e",
            "uploaded": True,
            "state": "shipment_matched",
            "content_status": "passed",
            "detected_document_type": "FORM E",
            "extraction": {},
            "checks": [],
        },
        {
            "claimed_document_type": "certificate_of_origin",
            "canonical_document_type": "certificate_of_origin",
            "uploaded": True,
            "state": "shipment_matched",
            "content_status": "passed",
            "detected_document_type": "CERTIFICATE OF ORIGIN",
            "extraction": {},
            "checks": [],
        },
    ]
    return extraction


def _failed_quantity_mismatch_extraction() -> dict[str, Any]:
    return make_extraction(
        [
            line(
                status="failed",
                item_checks=[
                    {
                        "check_id": "item_quantity_match",
                        "status": "failed",
                        "message": (
                            "Quantity mismatch: invoice has '100' and packing "
                            "list has '99'."
                        ),
                    }
                ],
            )
        ],
        "failed",
    )


def _manual_review_pct_extraction() -> dict[str, Any]:
    return make_extraction(
        [
            line(
                status="manual_review",
                compliance_checks=[
                    {
                        "check_id": "pct_code_confirmation",
                        "check_name": "PCT code confirmation",
                        "status": "manual_review",
                        "message": (
                            "The scanned invoice appears to show PCT code "
                            "61091000, but the image is not clear enough to "
                            "confirm it safely."
                        ),
                    }
                ],
            )
        ],
        "manual_review",
    )


# 1. A PASSED explanation includes actual quantity, price and total.
def test_passed_explanation_includes_actual_quantity_price_and_total() -> None:
    findings = _findings_for(_passed_extraction(), status="passed")
    text = _default_explanation("Explanation", findings)
    assert "100 units" in text
    assert "$5.50" in text
    assert "$550.00" in text


# 2. A PASSED explanation identifies all four document types.
def test_passed_explanation_identifies_all_four_document_types() -> None:
    findings = _findings_for(_four_document_extraction(), status="passed")
    assert findings["documents_checked"] == [
        "Commercial invoice",
        "Packing list",
        "Form-E",
        "Certificate of origin",
    ]
    text = _default_explanation("Explanation", findings)
    for document in ("Commercial invoice", "Packing list", "Form-E", "Certificate of origin"):
        assert document in text


# 3. A FAILED explanation shows both conflicting values.
def test_failed_explanation_shows_both_conflicting_values() -> None:
    findings = _findings_for(_failed_quantity_mismatch_extraction(), status="failed")
    text = _default_explanation("Explanation", findings)
    assert "100" in text
    assert "99" in text


# 4. A MANUAL REVIEW explanation states what must be confirmed.
def test_manual_review_explanation_states_what_must_be_confirmed() -> None:
    findings = _findings_for(_manual_review_pct_extraction(), status="manual_review")
    text = _default_explanation("Explanation", findings)
    assert "A person must confirm this" in text
    assert "61091000" in text


# 5. Raw check IDs are not exposed.
def test_raw_check_ids_are_not_exposed_in_any_template_output() -> None:
    assert explanation_exposes_raw_identifiers("The Quantity check passed.") is None
    assert (
        explanation_exposes_raw_identifiers("See item_quantity_match for detail.")
        == "item_quantity_match"
    )
    for extraction, status in (
        (_passed_extraction(), "passed"),
        (_failed_quantity_mismatch_extraction(), "failed"),
        (_manual_review_pct_extraction(), "manual_review"),
    ):
        findings = _findings_for(extraction, status=status)
        text = _default_explanation("Explanation", findings)
        assert explanation_exposes_raw_identifiers(text) is None, text


# 6. Necessary technical terms are explained when used.
def test_necessary_technical_terms_are_accepted_when_explained() -> None:
    explained = (
        "This was a deterministic check, meaning a fixed rule that always "
        "gives the same result. " + ("Extra detail sentence. " * 12)
    )
    assert explanation_has_unexplained_jargon(explained) is None


# 7. Unexplained jargon-heavy output is rejected.
def test_unexplained_jargon_heavy_output_is_rejected() -> None:
    jargon_text = (
        "The retrieval pipeline used embedding reranking with cross-encoder "
        "consensus to confirm provenance. " + ("Extra detail sentence. " * 12)
    )
    assert explanation_has_unexplained_jargon(jargon_text) is not None

    def jargon_narrator(role: str, findings: dict[str, Any]) -> str:
        return jargon_text

    findings = _findings_for(_passed_extraction(), status="passed")
    entry = generate_explanation_entry(
        state=_state(_passed_extraction(), status="passed"),
        final_report={"deterministic_compliance_status": "passed"},
        narrator=jargon_narrator,
        model_label="test-model",
    )
    assert entry["explanation_source"] == "template_fallback"
    assert entry["explanation_rejection_reason"] == "unexplained_jargon"
    del findings


# 8. Vague output such as "all checks passed" without detail is rejected or replaced.
def test_vague_passed_output_without_concrete_values_is_rejected() -> None:
    findings = _findings_for(_passed_extraction(), status="passed")
    assert explanation_is_vague_for_passed("All checks passed.", findings) is True
    grounded = "The shipment passed. The invoice shows 100 pieces, PCT 6109.1000."
    assert explanation_is_vague_for_passed(grounded, findings) is False

    def vague_narrator(role: str, findings_arg: dict[str, Any]) -> str:
        return (
            "Decision: all checks passed and everything is fine. "
            "Why this decision: every configured check passed with no "
            "issues found anywhere in the shipment. "
            "What to do next: keep the shipment record on file for the "
            "usual retention period. Limitations: this is not official "
            "customs clearance, external document authentication or "
            "permission to enter the destination country. "
            + ("Nothing else to report. " * 10)
        )

    entry = generate_explanation_entry(
        state=_state(_passed_extraction(), status="passed"),
        final_report={"deterministic_compliance_status": "passed"},
        narrator=vague_narrator,
        model_label="test-model",
    )
    assert entry["explanation_source"] == "template_fallback"
    assert entry["explanation_rejection_reason"] == "vague_passed_explanation"


# 9. Every failure includes a clear next action.
def test_every_failure_includes_a_clear_next_action() -> None:
    for extraction, status in (
        (_failed_quantity_mismatch_extraction(), "failed"),
        (_manual_review_pct_extraction(), "manual_review"),
    ):
        findings = _findings_for(extraction, status=status)
        text = _default_explanation("Explanation", findings)
        assert "What to do next" in text
        assert "1." in text.split("What to do next", 1)[1]


# 10. The limitation is always present.
def test_the_limitation_is_always_present() -> None:
    for extraction, status in (
        (_passed_extraction(), "passed"),
        (_failed_quantity_mismatch_extraction(), "failed"),
        (_manual_review_pct_extraction(), "manual_review"),
    ):
        findings = _findings_for(extraction, status=status)
        text = _default_explanation("Explanation", findings)
        assert explanation_missing_limitation(text) is False


# 11. Official customs-clearance claims are rejected.
def test_official_customs_clearance_claims_are_rejected() -> None:
    assert explanation_has_prohibited_claims("The shipment is customs cleared.") is not None
    findings = _findings_for(_passed_extraction(), status="passed")
    assert (
        explanation_validation_failure(
            "The shipment is customs cleared and officially compliant. "
            + ("Detail sentence. " * 15),
            findings,
        )
        == "prohibited_claim"
    )


# 12. Entry-approval claims are rejected.
def test_entry_approval_claims_are_rejected() -> None:
    assert (
        explanation_has_prohibited_claims("The shipment is cleared for entry into China.")
        is not None
    )
    findings = _findings_for(_passed_extraction(), status="passed")
    assert (
        explanation_validation_failure(
            "The shipment is cleared for entry into China. " + ("Detail sentence. " * 15),
            findings,
        )
        == "prohibited_claim"
    )


# 13. Only accepted citations may be mentioned.
def test_only_accepted_citations_may_be_mentioned() -> None:
    findings = {
        "status": "passed",
        "regulatory_evidence": [
            {
                "requirement": "Form-E requirement",
                "evidence_status": "evidence_verified",
                "citations": [
                    {
                        "source": "TIPP Customs Clearance Procedure",
                        "page": 4,
                        "section": "Export documentation",
                        "excerpt": "A Form-E declaration is required.",
                        "sro_number": "2486(I)/2025",
                    }
                ],
            }
        ],
    }
    # The grounded SRO is fine.
    assert explanation_has_ungrounded_sro("See SRO 2486(I)/2025 for detail.", findings) is None
    # A different SRO the evidence layer never returned is not fine.
    assert (
        explanation_has_ungrounded_sro("See SRO 9999(I)/2099 for detail.", findings)
        == "SRO 9999(I)/2099"
    )


# 14. The explanation cannot create a new compliance finding.
def test_explanation_cannot_invent_a_new_finding() -> None:
    findings = _findings_for(_passed_extraction(), status="passed")
    # $550.00 and 6109.1000 are real values from the fixture - grounded.
    assert explanation_has_ungrounded_facts("The total is $550.00.", findings) is None
    assert explanation_has_ungrounded_facts("PCT code 6109.1000 is correct.", findings) is None
    # $999.99 never appeared anywhere in the findings - an invented figure.
    assert explanation_has_ungrounded_facts("The total is $999.99.", findings) == "$999.99"


# 15. An unsafe narrator response triggers the deterministic fallback.
def test_unsafe_narrator_response_triggers_the_deterministic_fallback() -> None:
    def unsafe_narrator(role: str, findings: dict[str, Any]) -> str:
        return "The shipment is customs cleared. " + ("Detail sentence. " * 15)

    entry = generate_explanation_entry(
        state=_state(_passed_extraction(), status="passed"),
        final_report={"deterministic_compliance_status": "passed"},
        narrator=unsafe_narrator,
        model_label="test-model",
    )
    assert entry["explanation_source"] == "template_fallback"
    assert entry["explanation_rejection_reason"] == "prohibited_claim"
    assert "customs cleared" not in entry["explanation"].lower()


# 15b. The rejection reason is also visible on the final workflow report, not
# only on the internal entry dict, so a human reviewing the audit record can
# see *why* the template was used instead of the narrator's own words.
def test_rejection_reason_is_recorded_on_the_final_report(
    isolated_database: Engine,
) -> None:
    def unsafe_narrator(role: str, findings: dict[str, Any]) -> str:
        return "The shipment is customs cleared. " + ("Detail sentence. " * 15)

    svc = make_service(
        isolated_database, _passed_extraction(), explanation_narrator=unsafe_narrator
    )
    result = start(svc, isolated_database)
    assert result["final_report"]["explanation_rejection_reason"] == "prohibited_claim"


# 16. The deterministic PASSED / FAILED / MANUAL REVIEW status is unchanged.
def test_deterministic_status_is_unchanged_by_an_unsafe_narrator(
    isolated_database: Engine,
) -> None:
    def unsafe_narrator(role: str, findings: dict[str, Any]) -> str:
        return "The shipment is customs cleared. " + ("Detail sentence. " * 15)

    svc = make_service(
        isolated_database, _passed_extraction(), explanation_narrator=unsafe_narrator
    )
    result = start(svc, isolated_database)
    assert result["deterministic_status"] == "passed"
    assert result["final_report"]["explanation_source"] == "template_fallback"

    svc2 = make_service(
        isolated_database,
        _failed_quantity_mismatch_extraction(),
        explanation_narrator=unsafe_narrator,
    )
    started = start(svc2, isolated_database)
    resumed = review(svc2, isolated_database, started["workflow_id"], accept_decision())
    assert resumed["final_report"]["deterministic_compliance_status"] == "failed"

    svc3 = make_service(
        isolated_database,
        _manual_review_pct_extraction(),
        explanation_narrator=unsafe_narrator,
    )
    started3 = start(svc3, isolated_database)
    assert started3["status"] == "awaiting_human_review"


# 17. The technical evidence-search section is collapsed by default.
def test_evidence_search_section_is_collapsed_by_default_in_the_frontend() -> None:
    frontend_path = (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "src"
        / "components"
        / "AgentAuditResult.tsx"
    )
    source = frontend_path.read_text()
    marker = 'className="evidence-search-explanation"'
    assert marker in source
    start_index = source.index("<details", source.index(marker) - 40)
    tag_end = source.index(">", start_index)
    opening_tag = source[start_index:tag_end]
    assert "open" not in opening_tag


# 18. The technical section uses simple language before jargon.
def test_evidence_search_explanation_uses_plain_language_before_jargon() -> None:
    text = EVIDENCE_SEARCH_EXPLANATION
    assert text.index("exact words") < text.index("BM25")
    assert text.index("similar meaning") < text.index("embedding search")
    assert text.index("combined") < text.index("Reciprocal Rank Fusion")
    assert text.index("re-checked") < text.index("cross-encoder reranking")
    # No raw numeric scores in the plain-language summary.
    import re

    assert not re.search(r"\b0\.\d+\b", text)
