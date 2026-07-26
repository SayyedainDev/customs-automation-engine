"""Tests for the one Groq call the hybrid extractor is allowed to make.

``llm_gapfill.py`` already has its own unit tests for prompt/context/
validation. These tests exercise the network-call layer on top of it
(``groq_gapfill.run_gapfill``): exactly one call regardless of how many
fields are unresolved, no retry, no cascade into a per-field ladder, and
every returned value re-validated before it is trusted.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app.services.extraction.groq_gapfill import run_gapfill
from app.services.extraction.regex_extractor import extract_document


class _RecordingCompletions:
    """A fake Groq ``chat.completions`` that records every call it receives."""

    def __init__(self, response_by_call: list[dict[str, Any] | Exception]):
        self._responses = list(response_by_call)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        response = self._responses[len(self.calls) - 1]
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=json.dumps(response)))
            ]
        )


def _client(completions: _RecordingCompletions) -> Any:
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _provider_error(status_code: int) -> Exception:
    exc = RuntimeError("provider error")
    exc.status_code = status_code  # type: ignore[attr-defined]
    exc.body = {"error": {"message": "provider error", "code": "err"}}  # type: ignore[attr-defined]
    return exc


def _unresolved_extraction(text: str = "nothing useful printed here") -> Any:
    """A document where every requested field is unresolved."""
    return extract_document(text)


def test_no_unresolved_fields_makes_zero_calls() -> None:
    extraction = _unresolved_extraction()
    updated, telemetry = run_gapfill(extraction, [], client=object())  # type: ignore[arg-type]
    assert updated == {}
    assert telemetry.llm_calls == 0


def test_exactly_one_call_resolves_every_unresolved_field_together() -> None:
    extraction = _unresolved_extraction()
    unresolved = ["invoice_number", "currency"]
    completions = _RecordingCompletions(
        [{"invoice_number": "INV-2026-777", "currency": "USD"}]
    )
    updated, telemetry = run_gapfill(
        extraction, unresolved, client=_client(completions)
    )
    assert len(completions.calls) == 1
    assert telemetry.llm_calls == 1
    assert updated["invoice_number"].value == "INV-2026-777"
    assert updated["invoice_number"].method == "llm_gapfill"
    assert updated["currency"].value == "USD"
    assert telemetry.fields_from_llm == 2
    assert telemetry.fields_missing == 0


def test_gapfill_prompt_never_contains_the_full_document() -> None:
    huge_text = "Invoice Number\nINV-1\n" + ("filler text " * 5000)
    extraction = extract_document(huge_text)
    unresolved = extraction.unresolved_fields()
    completions = _RecordingCompletions([{name: None for name in unresolved}])
    run_gapfill(extraction, unresolved, client=_client(completions))
    sent_prompt = completions.calls[0]["messages"][1]["content"]
    assert "filler text" not in sent_prompt or len(sent_prompt) < len(huge_text)
    assert len(sent_prompt) < len(huge_text)


def test_provider_unavailable_leaves_fields_unresolved_and_does_not_raise() -> None:
    """A 429/5xx/timeout must never raise out of run_gapfill or cascade."""
    extraction = _unresolved_extraction()
    unresolved = ["invoice_number"]
    completions = _RecordingCompletions([_provider_error(429)])
    updated, telemetry = run_gapfill(
        extraction, unresolved, client=_client(completions)
    )
    assert len(completions.calls) == 1
    assert updated == {}
    assert telemetry.llm_calls == 1
    assert any("unavailable" in note for note in telemetry.notes)


def test_malformed_json_leaves_fields_unresolved_with_no_retry() -> None:
    extraction = _unresolved_extraction()
    unresolved = ["invoice_number"]

    class _BadJsonCompletions(_RecordingCompletions):
        def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{not-json"))]
            )

    completions = _BadJsonCompletions([])
    updated, telemetry = run_gapfill(
        extraction, unresolved, client=_client(completions)
    )
    assert len(completions.calls) == 1  # exactly one attempt, never a retry
    assert updated == {}
    assert telemetry.llm_calls == 1


def test_invalid_returned_value_is_discarded_not_passed_through() -> None:
    extraction = _unresolved_extraction()
    unresolved = ["invoice_number", "total_invoice_value"]
    completions = _RecordingCompletions(
        [{"invoice_number": "INV-2026-777", "total_invoice_value": "not a number"}]
    )
    updated, telemetry = run_gapfill(
        extraction, unresolved, client=_client(completions)
    )
    assert updated["invoice_number"].value == "INV-2026-777"
    assert updated["total_invoice_value"].value is None
    assert updated["total_invoice_value"].confidence == "missing"
    assert telemetry.fields_from_llm == 1
    assert telemetry.fields_missing == 1
