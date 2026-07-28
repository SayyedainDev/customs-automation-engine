from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest

from scripts import run_synthetic_factory_evaluation as runner


class _Response:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _entry() -> dict[str, Any]:
    manifest = json.loads(runner.MANIFEST_PATH.read_text(encoding="utf-8"))
    return deepcopy(
        next(
            row
            for row in manifest["supporting_document_scenarios"]
            if row["scenario_id"] == "clean_cotton_yarn_supporting"
            and row["variant"] == "text"
        )
    )


def _workflow_payload(
    *,
    status: str = "completed",
    deterministic_status: str = "passed",
) -> dict[str, Any]:
    return {
        "workflow_id": "7a502418-0bf9-48d4-a4e5-11997d8e7291",
        "thread_id": "customs-audit-7a5024180bf948d4a4e511997d8e7291",
        "status": status,
        "current_node": (
            "interrupt_for_human_review"
            if status == "awaiting_human_review"
            else "completed"
        ),
        "deterministic_status": deterministic_status,
        "requires_human_review": status == "awaiting_human_review",
        "final_report": {
            "broker_findings": {
                "verified_supporting_documents": [
                    "form_e_or_psw_export_declaration",
                    "certificate_of_origin",
                ],
                "observed_deterministic_status": deterministic_status,
            },
            "auditor_findings": {
                "confirmed_supporting_documents": [
                    "certificate of origin: independently re-checked and consistent"
                ],
                "challenged_supporting_documents": [],
                "observed_deterministic_status": deterministic_status,
            },
            "consensus_result": {
                "consensus_reached": True,
                "requires_human_review": status == "awaiting_human_review",
                "deterministic_status": deterministic_status,
            },
            "deterministic_compliance_status": deterministic_status,
            "original_deterministic_status": deterministic_status,
            "user_report": {
                "overall_result": deterministic_status.upper(),
                "workflow_summary": ["Broker and Auditor completed their review."],
            },
        },
        "errors": None,
    }


def _events(*, completed: bool = True) -> dict[str, Any]:
    event_types = [
        "workflow_started",
        "broker_report_created",
        "deterministic_status_frozen",
        "auditor_report_created",
        "consensus_computed",
    ]
    if completed:
        event_types.extend(["final_report_built", "workflow_finalized"])
    return {
        "workflow_id": "7a502418-0bf9-48d4-a4e5-11997d8e7291",
        "events": [
            {
                "event_type": event_type,
                "node_name": event_type,
                "actor_type": "system",
                "event_payload": {},
            }
            for event_type in event_types
        ],
    }


class _SupportingWorkflowApi:
    def __init__(
        self,
        *,
        workflow_status_code: int = 200,
        workflow_payload: dict[str, Any] | None = None,
        events_payload: dict[str, Any] | None = None,
        entry: dict[str, Any] | None = None,
        corrupt_field: tuple[str, str, Any] | None = None,
    ) -> None:
        self.workflow_status_code = workflow_status_code
        self.workflow_payload = workflow_payload or _workflow_payload()
        self.events_payload = events_payload or _events()
        self.entry = entry or _entry()
        self.corrupt_field = corrupt_field
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self._uploaded: dict[Path, str] = {}

    def upload(self, path: Path) -> str:
        resolved = path.resolve()
        document_id = str(uuid5(UUID(int=0), str(resolved)))
        self._uploaded[resolved] = document_id
        return document_id

    def post(self, path: str, **kwargs: Any) -> _Response:
        body = kwargs["json"]
        self.posts.append((path, body))
        if path == "/api/v1/compliance/check-documents/multi-line":
            expected_by_type = {
                document["claimed_document_type"]: document
                for document in self.entry["expected_documents"]
            }
            documents: list[dict[str, Any]] = []
            for reference in body["supporting_documents"]:
                expected = expected_by_type[reference["document_type"]]
                extracted_fields = {
                    name: {"value": value}
                    for name, value in expected["expected_fields"].items()
                }
                if (
                    self.corrupt_field is not None
                    and self.corrupt_field[0] == reference["document_type"]
                ):
                    extracted_fields[self.corrupt_field[1]] = {
                        "value": self.corrupt_field[2]
                    }
                documents.append(
                    {
                    "claimed_document_type": reference["document_type"],
                    "canonical_document_type": reference["document_type"],
                    "uploaded": True,
                    "document_id": reference["document_id"],
                    "detected_document_type": expected["expected_detected_type"],
                    "state": "shipment_matched",
                    "content_status": "passed",
                    "authenticity_status": "not_externally_verified",
                    "required_action": expected["expected_required_action"],
                    "extraction_confidence": "0.98",
                    "ocr_confidence": None,
                    "source_page": 1,
                    "checks": [],
                    "extraction": extracted_fields,
                }
                )
            return _Response(
                200,
                {
                    "overall_status": "passed",
                    "supporting_documents": documents,
                },
            )
        if path == "/api/v1/customs-audit/workflows":
            return _Response(self.workflow_status_code, self.workflow_payload)
        raise AssertionError(f"Unexpected POST {path}")

    def get(self, path: str) -> _Response:
        if path.endswith("/events"):
            return _Response(200, self.events_payload)
        if path.endswith("/review"):
            return _Response(404, {"detail": "no open human-review task"})
        if "/api/v1/customs-audit/workflows/" in path:
            return _Response(200, self.workflow_payload)
        raise AssertionError(f"Unexpected GET {path}")


def test_supporting_workflow_posts_typed_refs_and_grades_all_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "RAW_DIR", tmp_path)
    api = _SupportingWorkflowApi()

    outcome = runner.evaluate_supporting_scenario(
        api,  # type: ignore[arg-type]
        _entry(),
        run_workflow=True,
        poll_timeout=0,
    )

    direct_body = api.posts[0][1]
    workflow_path, workflow_body = api.posts[1]
    assert workflow_path == "/api/v1/customs-audit/workflows"
    assert workflow_body["supporting_documents"] == direct_body["supporting_documents"]
    assert workflow_body["additional_uploaded_document_types"] == []
    assert workflow_body["additional_document_ids"] == [
        reference["document_id"]
        for reference in direct_body["supporting_documents"]
    ]
    assert outcome.workflow_id == "7a502418-0bf9-48d4-a4e5-11997d8e7291"
    assert outcome.workflow_status == "completed"
    assert outcome.workflow_reached_terminal_state is True
    assert outcome.workflow_validation["deterministic_status_unchanged"] is True
    assert outcome.workflow_validation["broker_findings_present"] is True
    assert outcome.workflow_validation["auditor_findings_present"] is True
    assert outcome.workflow_validation["consensus_present"] is True
    assert outcome.workflow_validation["readable_report_present"] is True
    assert outcome.workflow_validation["required_events_present"] is True
    assert outcome.ok is True

    raw = json.loads(
        (tmp_path / "clean_cotton_yarn_supporting__text.json").read_text(
            encoding="utf-8"
        )
    )
    assert raw["workflow_final"]["thread_id"].startswith("customs-audit-")
    assert raw["workflow_events"]["events"]
    assert raw["outcome"]["workflow_id"] == outcome.workflow_id


def test_workflow_cannot_override_direct_deterministic_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "RAW_DIR", tmp_path)
    entry = _entry()
    entry["expected_primary_status"] = "passed"
    workflow = _workflow_payload(deterministic_status="failed")
    workflow["final_report"]["deterministic_compliance_status"] = "failed"
    workflow["final_report"]["original_deterministic_status"] = "failed"
    api = _SupportingWorkflowApi(workflow_payload=workflow)

    outcome = runner.evaluate_supporting_scenario(
        api,  # type: ignore[arg-type]
        entry,
        run_workflow=True,
        poll_timeout=0,
    )

    assert outcome.deterministic_status == "passed"
    assert outcome.workflow_validation["deterministic_status_unchanged"] is False
    assert outcome.ok is False
    assert any("deterministic status" in note for note in outcome.notes)


def test_supporting_field_mismatch_cannot_hide_behind_passing_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "RAW_DIR", tmp_path)
    entry = _entry()
    api = _SupportingWorkflowApi(
        entry=entry,
        corrupt_field=(
            "certificate_of_origin",
            "invoice_reference",
            "WRONG-INVOICE",
        ),
    )

    outcome = runner.evaluate_supporting_scenario(
        api,  # type: ignore[arg-type]
        entry,
        run_workflow=False,
        poll_timeout=0,
    )

    certificate = next(
        document
        for document in outcome.supporting_documents["documents"]
        if document["claimed_document_type"] == "certificate_of_origin"
    )
    invoice_field = next(
        field
        for field in certificate["fields"]
        if field["name"] == "invoice_reference"
    )
    assert invoice_field == {
        "name": "invoice_reference",
        "expected": "FYS-INV-2026-101",
        "actual": "WRONG-INVOICE",
        "correct": False,
    }
    assert outcome.ok is False
    assert any("invoice_reference" in note for note in outcome.notes)


@pytest.mark.parametrize("status_code", [429, 503])
def test_provider_blocked_workflow_is_technical_and_not_complete(
    status_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "RAW_DIR", tmp_path)
    api = _SupportingWorkflowApi(
        workflow_status_code=status_code,
        workflow_payload={"detail": "provider unavailable"},
    )

    outcome = runner.evaluate_supporting_scenario(
        api,  # type: ignore[arg-type]
        _entry(),
        run_workflow=True,
        poll_timeout=0,
    )

    assert outcome.http_status == status_code
    assert outcome.workflow_reached_terminal_state is False
    assert outcome.technical_failure == f"workflow start HTTP {status_code}"
    assert outcome.ok is False


def test_nonterminal_workflow_is_not_a_completed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "RAW_DIR", tmp_path)
    api = _SupportingWorkflowApi(
        workflow_payload=_workflow_payload(status="running"),
        events_payload={"workflow_id": "unused", "events": []},
    )

    outcome = runner.evaluate_supporting_scenario(
        api,  # type: ignore[arg-type]
        _entry(),
        run_workflow=True,
        poll_timeout=0,
    )

    assert outcome.workflow_reached_terminal_state is False
    assert outcome.technical_failure == "workflow did not reach a terminal state"
    assert outcome.ok is False


class _PreflightApi:
    base_url = "http://current-api.test"

    def __init__(self, workflow_overrides: dict[str, Any] | None = None) -> None:
        from app.core.config import get_settings

        settings = get_settings()
        configured_key = settings.groq_api_key
        api_key = configured_key.get_secret_value() if configured_key else "fake-test-key"
        self.workflow = {
            "status": "ok",
            "checkpoint_backend": "postgres",
            "checkpoint_ready": True,
            "live_agents_enabled": settings.langgraph_enable_live_agents,
            "broker_model": settings.langgraph_broker_model or settings.groq_model,
            "auditor_model": settings.langgraph_auditor_model or settings.groq_model,
            "extraction_model": settings.groq_model,
            "human_review_required": settings.langgraph_human_review_required,
            "groq_credential_fingerprint": (
                "sha256:"
                + runner.hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
            ),
        }
        self.workflow.update(workflow_overrides or {})

    def get(self, path: str) -> _Response:
        if path == "/health":
            return _Response(200, {"status": "ok"})
        if path == "/health/database":
            return _Response(200, {"status": "ok", "database": "connected"})
        if path == "/health/extraction-cache":
            return _Response(200, runner.runtime_extraction_cache_capability())
        if path == "/health/customs-audit":
            return _Response(200, self.workflow)
        raise AssertionError(f"Unexpected GET {path}")

    def post(self, path: str, **_kwargs: Any) -> _Response:
        assert path == "/api/v1/regulatory-evidence/search"
        return _Response(200, {"degraded_mode": False, "results": [{}]})


def test_preflight_requires_server_proven_postgres_workflow_profile() -> None:
    report = runner.preflight(
        _PreflightApi(),  # type: ignore[arg-type]
        need_ocr=False,
        need_workflow=True,
        strict=True,
    )

    assert report["customs_audit"]["checkpoint_backend"] == "postgres"
    assert report["customs_audit"]["checkpoint_ready"] is True
    assert report["customs_audit"]["groq_credential"] == "matched"


@pytest.mark.parametrize(
    "override",
    [
        {"checkpoint_backend": "sqlite"},
        {"checkpoint_ready": False},
        {"groq_credential_fingerprint": "sha256:not-the-server-key"},
    ],
)
def test_preflight_refuses_wrong_workflow_runtime(
    override: dict[str, Any],
) -> None:
    with pytest.raises(runner.EvaluationError):
        runner.preflight(
            _PreflightApi(override),  # type: ignore[arg-type]
            need_ocr=False,
            need_workflow=True,
            strict=False,
        )
