"""Compliance evidence for Layer 1 input guard, Layer 2 scope router, and Layer 4 output guard."""

from __future__ import annotations

from app.services.customs_chat.guards.input_guard import check_input
from app.services.customs_chat.guards.output_guard import check_output
from app.services.customs_chat.guards.scope_router import deterministic_scope
from cace_testing.harnesses.fixtures_paths import load_golden_questions


def test_golden_questions_satisfy_injection_and_scope_routing_rules() -> None:
    cases = load_golden_questions()
    assert len(cases) > 0

    for case in cases:
        if case.category == "injection":
            verdict = check_input(case.question)
            assert verdict.ok is False, case.question_id
            assert verdict.injection_detected is True, case.question_id

            obfuscated = case.question.replace("ignore", "ig\u200bnore").replace(
                "Ignore", "Ig\u200bnore"
            )
            obfuscated_verdict = check_input(obfuscated)
            assert obfuscated_verdict.injection_detected is True, case.question_id
        elif case.category == "out_of_scope" and case.resolved_by == "fast":
            scope = deterministic_scope(case.question)
            assert scope is not None, case.question_id
            assert scope.in_scope is False, case.question_id
        elif case.category in {"grounded", "general"} and case.resolved_by == "fast":
            scope = deterministic_scope(case.question)
            assert scope is not None, case.question_id
            assert scope.in_scope is True, case.question_id
        elif case.question_id == "adversarial-001":
            assert deterministic_scope(case.question) is None, case.question_id


def test_output_guard_enforces_refusal_grounding_and_retry_escalation() -> None:
    leaked = check_output(
        "Here is who you are and the one rule that overrides everything.",
        mode="GENERAL",
        evidence_text="",
        attempt=0,
    )
    assert leaked.ok is False
    assert leaked.action == "refuse"

    drifted = check_output(
        "Here is a great recipe for biryani.",
        mode="GENERAL",
        evidence_text="",
        attempt=0,
    )
    assert drifted.ok is False
    assert drifted.action == "refuse"

    general_numeric = check_output(
        "The customs duty rate is 20%.",
        mode="GENERAL",
        evidence_text="",
        attempt=0,
    )
    assert general_numeric.ok is False
    assert general_numeric.action == "regenerate"

    escalated = check_output(
        "The customs duty rate is 20%.",
        mode="GENERAL",
        evidence_text="",
        attempt=1,
    )
    assert escalated.ok is False
    assert escalated.action == "downgrade"

    grounded_unbacked = check_output(
        "Customs duty on this line is 25%.",
        mode="GROUNDED",
        evidence_text="Customs duty on HS 62034200 is 20%.",
        attempt=0,
    )
    assert grounded_unbacked.ok is False
    assert grounded_unbacked.action == "regenerate"

    grounded_backed = check_output(
        "HS 6203.4200 carries a 20% customs duty in the tariff schedule.",
        mode="GROUNDED",
        evidence_text="HS code 62034200. Customs duty: 20%.",
        attempt=0,
    )
    assert grounded_backed.ok is True
    assert grounded_backed.action == "pass"

    legalese = check_output(
        "Pursuant to export procedures, retain the invoice.",
        mode="GENERAL",
        evidence_text="",
        attempt=0,
    )
    assert legalese.ok is False
    assert legalese.action == "regenerate"
