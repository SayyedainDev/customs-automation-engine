"""Executable-source contracts for the focused CACE frontend repair.

The project intentionally has no browser test runner. These tests pin the
state/API wiring while ``npm run build`` type-checks and bundles the actual
React components.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ASK = (ROOT / "frontend/src/pages/RegulatoryAssistantPage.tsx").read_text()
REVIEW_HOOK = (ROOT / "frontend/src/hooks/useDocumentReview.ts").read_text()
REVIEW_RESULT = (
    ROOT / "frontend/src/components/ComplianceReviewResult.tsx"
).read_text()
AUDIT_RESULT = (ROOT / "frontend/src/components/AgentAuditResult.tsx").read_text()
API_CLIENT = (ROOT / "frontend/src/api/client.ts").read_text()
SUPPORTING = (ROOT / "frontend/src/lib/supportingDocuments.ts").read_text()


def test_ask_cace_uses_one_active_question_and_answer() -> None:
    assert "const [activeQuestion, setActiveQuestion]" in ASK
    assert "const [activeAnswer, setActiveAnswer]" in ASK
    assert "const [messages, setMessages]" not in ASK


def test_second_question_clears_old_answer_and_sources_before_request() -> None:
    cleared = ASK.index("setActiveAnswer(null)")
    requested = ASK.index("await api.askRegulatory")
    assert cleared < requested
    assert "setActiveQuestion({" in ASK


def test_refresh_does_not_rehydrate_conversation_history() -> None:
    assert "localStorage" not in ASK
    assert "sessionStorage" not in ASK
    assert "getConversation" not in ASK
    assert "conversation_id:" not in ASK


def test_ask_copy_and_sources_match_current_scope() -> None:
    assert (
        "deterministic compliance decisions are available for 17 validated "
        "textile PCT codes in Prepare an Export."
    ) in ASK
    assert "Relevant sources (" in ASK
    assert "response.sources.slice(0, 3)" in ASK
    assert "top_k: 3" in ASK


def test_internal_answer_and_evidence_badges_are_not_rendered() -> None:
    for raw_label in (
        '"Explanation"',
        '"Evidence accepted"',
        '"No sufficiently relevant evidence"',
    ):
        assert raw_label not in ASK


def test_shipment_assistant_and_chat_request_are_absent() -> None:
    assert not (ROOT / "frontend/src/components/AssistantPanel.tsx").exists()
    assert "AssistantPanel" not in AUDIT_RESULT
    assert "sendChat" not in API_CLIENT
    assert "/api/v1/assistant/shipments/" not in API_CLIENT


def test_supporting_document_changes_invalidate_stale_results() -> None:
    add_body = REVIEW_HOOK[
        REVIEW_HOOK.index("const addSupportingSlot") :
        REVIEW_HOOK.index("const removeSupportingSlot")
    ]
    remove_body = REVIEW_HOOK[
        REVIEW_HOOK.index("const removeSupportingSlot") :
        REVIEW_HOOK.index("const chooseSupportingFile")
    ]
    choose_body = REVIEW_HOOK[
        REVIEW_HOOK.index("const chooseSupportingFile") :
        REVIEW_HOOK.index("const loadWorkflowAssets")
    ]
    assert all("clearWorkflow()" in body for body in (add_body, remove_body, choose_body))
    assert "slots.some((slot) => slot.documentType === documentType)" in add_body


def test_all_document_ids_and_revision_are_bound_to_one_request() -> None:
    assert "supporting_documents: supportingDocuments" in REVIEW_HOOK
    assert "review_revision_id: reviewRevisionId" in REVIEW_HOOK
    assert "result.review_revision_id !== reviewRevisionId" in REVIEW_HOOK
    assert "...supportingDocuments.map((document) => document.document_id)" in REVIEW_HOOK


def test_canonical_form_e_and_coo_values_are_sent() -> None:
    assert 'value: "form_e_or_psw_export_declaration"' in SUPPORTING
    assert 'value: "certificate_of_origin"' in SUPPORTING


def test_review_sections_use_latest_response_and_clear_order() -> None:
    headings = [
        REVIEW_RESULT.index("Customs submission readiness"),
        REVIEW_RESULT.index("Supporting-document extraction"),
        REVIEW_RESULT.index("Findings in the uploaded documents"),
        REVIEW_RESULT.index("Missing or mismatched supporting documents"),
        REVIEW_RESULT.index("What was checked"),
        REVIEW_RESULT.index("Technical details: extracted fields and matched lines"),
    ]
    assert headings == sorted(headings)
    assert "result.review_revision_id" in REVIEW_RESULT
    assert "Uploaded but does not match this shipment" in REVIEW_RESULT
