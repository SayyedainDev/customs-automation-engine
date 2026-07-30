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
NEW_REVIEW = (ROOT / "frontend/src/pages/NewReviewPage.tsx").read_text()
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


def test_agent_audit_cannot_cross_review_revisions() -> None:
    assert "activeReviewRevisionRef" in REVIEW_HOOK
    assert "workflowRevisionId" in REVIEW_HOOK
    assert "activeReviewRevisionRef.current = null" in REVIEW_HOOK
    assert "activeReviewRevisionRef.current = reviewRevisionId" in REVIEW_HOOK
    assert (
        "activeReviewRevisionRef.current !== expectedRevisionId"
        in REVIEW_HOOK
    )
    assert "next.review_revision_id !== expectedRevisionId" in REVIEW_HOOK
    assert "loadWorkflowAssets(next, expectedRevisionId)" in REVIEW_HOOK
    assert "review.workflowIsCurrent" in NEW_REVIEW
    assert (
        "review.compliance && review.workflow && review.workflowIsCurrent"
        in NEW_REVIEW
    )


def test_canonical_form_e_and_coo_values_are_sent() -> None:
    assert 'value: "form_e_or_psw_export_declaration"' in SUPPORTING
    assert 'value: "certificate_of_origin"' in SUPPORTING


def test_review_sections_use_latest_response_and_clear_order() -> None:
    headings = [
        REVIEW_RESULT.index("Customs submission readiness"),
        REVIEW_RESULT.index("Supporting documents"),
        REVIEW_RESULT.index("Findings in the uploaded documents"),
        REVIEW_RESULT.index("What was checked"),
        REVIEW_RESULT.index("Technical details: extracted fields and matched lines"),
    ]
    assert headings == sorted(headings)
    assert "result.review_revision_id" in REVIEW_RESULT
    assert "Uploaded but does not match this shipment" in REVIEW_RESULT


def test_supporting_document_states_are_rendered_in_separate_groups() -> None:
    for state_filter in (
        'document.presenceStatus === "shipment_matched"',
        'document.presenceStatus === "unresolved"',
        'document.presenceStatus === "shipment_mismatched"',
        'document.presenceStatus === "invalid"',
    ):
        assert state_filter in REVIEW_RESULT
    for heading in (
        '{ heading: "Matched"',
        '{ heading: "Needs confirmation"',
        '{ heading: "Does not match"',
        '{ heading: "Cannot be accepted"',
        "<h3>Still missing</h3>",
    ):
        assert heading in REVIEW_RESULT
    assert "Missing or mismatched supporting documents" not in REVIEW_RESULT
    assert 'data-presence-status={document.presenceStatus}' in REVIEW_RESULT
    assert 'data-presence-status="missing"' in REVIEW_RESULT


def test_uploaded_unresolved_document_is_not_also_rendered_as_missing() -> None:
    assert "const uploadedSupportingKeys = new Set(" in REVIEW_RESULT
    assert "const stillMissing = outstanding.filter(" in REVIEW_RESULT
    assert (
        "!uploadedSupportingKeys.has(normalizedDocumentKey(document.document_type))"
        in REVIEW_RESULT
    )
    # "Still to obtain" combines the rule engine's outstanding list with
    # documents the exporter named but never uploaded a file for
    # (presence_status "missing") - both are the same fact to the exporter,
    # so one combined count feeds both the readiness copy and the summary tile.
    assert "const documentsStillToObtain =" in REVIEW_RESULT
    assert "stillMissing.length + uniqueMissingSupporting.length" in REVIEW_RESULT
    # The headline verdict judges the documents the exporter uploaded, not
    # the full submission. `overall_status` folds in rules that are unresolved
    # only because paperwork has not been obtained yet, which put a red FAILED
    # on a correct set of invoice, packing list, Form E and COO. Those are
    # counted separately as documents still to obtain.
    assert (
        "submissionCopy(documentStatus, documentsStillToObtain)" in REVIEW_RESULT
    )
    assert "const documentStatus = result.document_review_status" in REVIEW_RESULT
    assert "<strong>{documentsStillToObtain}</strong>" in REVIEW_RESULT


def test_claimed_but_never_uploaded_document_is_missing_not_unresolved() -> None:
    """A document the exporter named but never uploaded a file for is
    "missing", not "unresolved" - they call for different actions (upload the
    file vs. confirm a field), so they must not share one bucket or heading.
    """
    assert 'missing: "Not yet uploaded"' in REVIEW_RESULT
    assert 'presenceStatus === "missing"' in REVIEW_RESULT
    assert "const missingSupporting = supportingExtractionStatuses.filter(" in REVIEW_RESULT
    # Rendered inside the same "Still missing" section as the rule engine's
    # outstanding list, not a new heading and not folded into "Needs
    # confirmation".
    assert "const uniqueMissingSupporting = missingSupporting.filter(" in REVIEW_RESULT
    assert "{uniqueMissingSupporting.map((document) => (" in REVIEW_RESULT


def test_raw_supporting_check_messages_are_collapsed() -> None:
    technical_summary = REVIEW_RESULT.index(
        "<summary>Technical check details</summary>"
    )
    raw_message = REVIEW_RESULT.index("{check.message}", technical_summary)
    closing_details = REVIEW_RESULT.index("</details>", technical_summary)
    assert technical_summary < raw_message < closing_details
