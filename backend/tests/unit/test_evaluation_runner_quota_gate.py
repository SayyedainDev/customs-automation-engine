from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from scripts import evaluation_cache as evaluation_cache_module
from app.core.config import get_settings
from scripts import run_synthetic_factory_evaluation as runner
from scripts.evaluation_cache import CacheFingerprint, EvaluationCache
from scripts.groq_quota_preflight import (
    DEFAULT_QUOTA_PROBE_TOKENS,
    DailyTokenQuota,
    QuotaPreflightError,
)


def test_configured_quota_reader_disables_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[dict[str, Any]] = []
    closed = False
    client = object()

    def fake_groq(**kwargs: Any) -> object:
        constructed.append(kwargs)
        return client

    def fake_probe(
        *,
        client: object,
        model: str,
        requested_tokens: int,
    ) -> DailyTokenQuota:
        assert client is client_instance
        assert model == "openai/gpt-oss-20b"
        assert requested_tokens == DEFAULT_QUOTA_PROBE_TOKENS
        return DailyTokenQuota(limit=200_000, used=199_257, requested=1_000_000)

    client_instance = client

    def fake_close(value: object) -> None:
        nonlocal closed
        assert value is client_instance
        closed = True

    monkeypatch.setattr(runner, "Groq", fake_groq, raising=False)
    monkeypatch.setattr(runner, "probe_daily_token_quota", fake_probe, raising=False)
    monkeypatch.setattr(runner, "_close_groq_client", fake_close, raising=False)

    quota = runner.read_daily_token_quota(
        api_key="gsk_not_a_real_key",
        model="openai/gpt-oss-20b",
    )

    assert quota.headroom == 743
    assert constructed == [
        {"api_key": "gsk_not_a_real_key", "max_retries": 0}
    ]
    assert closed is True


def test_insufficient_quota_stops_before_upload_and_preserves_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_calls: list[str] = []

    class FakeApi:
        def __init__(self, _base_url: str) -> None:
            pass

        def close(self) -> None:
            api_calls.append("close")

        def get(self, *_args: Any, **_kwargs: Any) -> Any:
            api_calls.append("get")
            raise AssertionError("No API call is allowed after quota refusal.")

        def post(self, *_args: Any, **_kwargs: Any) -> Any:
            api_calls.append("post")
            raise AssertionError("No API call is allowed after quota refusal.")

        def upload(self, *_args: Any, **_kwargs: Any) -> str:
            api_calls.append("upload")
            raise AssertionError("No upload is allowed after quota refusal.")

    reports = tmp_path / "reports"
    reports.mkdir()
    existing = reports / "quota_gate_sentinel.json"
    sentinel = {"completed_run": True}
    existing.write_text(json.dumps(sentinel), encoding="utf-8")

    monkeypatch.setattr(runner, "REPORTS_DIR", reports)
    monkeypatch.setattr(runner, "LiveApiClient", FakeApi)
    monkeypatch.setattr(
        runner,
        "preflight",
        lambda *_args, **_kwargs: {"api": "ok"},
    )

    def refuse(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise QuotaPreflightError(
            "Groq TPD headroom 743 is below estimated need 139400 "
            "(shortfall 138657); refusing to start."
        )

    monkeypatch.setattr(
        runner,
        "quota_preflight",
        refuse,
        raising=False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_synthetic_factory_evaluation",
            "--supporting-documents-only",
            "--variant",
            "text",
            "--only",
            "clean_cotton_yarn_supporting",
            "--no-workflow",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--output-prefix",
            "quota_gate_sentinel",
        ],
    )

    result = runner.main()

    assert result == 2
    assert api_calls == ["close"]
    assert json.loads(existing.read_text(encoding="utf-8")) == sentinel
    assert "refusing to start" in capsys.readouterr().err


def test_resume_planning_includes_supporting_cache_and_workflow_mode(
    tmp_path: Path,
) -> None:
    manifest = json.loads(runner.MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = next(
        row
        for row in manifest["supporting_document_scenarios"]
        if row["scenario_id"] == "clean_cotton_yarn_supporting"
        and row["variant"] == "text"
    )
    cache = EvaluationCache(tmp_path / "cache")
    paths = runner.scenario_document_paths(entry)
    key_without_workflow = cache.key(
        scenario_id=entry["scenario_id"],
        variant=entry["variant"],
        document_paths=paths,
        scenario_inputs=entry,
        workflow_profile={"enabled": False},
        workflow_enabled=False,
    )
    cache.put(
        key_without_workflow,
        {"outcome": {"scenario_id": entry["scenario_id"], "variant": "text"}},
        http_status=200,
        technical_failure=None,
    )

    without_workflow = runner.entries_requiring_live_extraction(
        [entry],
        cache=cache,
        resume=True,
        run_workflow=False,
    )
    with_workflow = runner.entries_requiring_live_extraction(
        [entry],
        cache=cache,
        resume=True,
        run_workflow=True,
    )

    assert without_workflow == []
    assert with_workflow == [entry]


def test_scenario_cache_hit_is_not_mislabeled_as_persisted_document_cache_hit(
    tmp_path: Path,
) -> None:
    manifest = json.loads(runner.MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = next(
        row
        for row in manifest["supporting_document_scenarios"]
        if row["scenario_id"] == "clean_cotton_yarn_supporting"
        and row["variant"] == "text"
    )
    cache = EvaluationCache(tmp_path / "cache")
    key = cache.key(
        scenario_id=entry["scenario_id"],
        variant=entry["variant"],
        document_paths=runner.scenario_document_paths(entry),
        scenario_inputs=entry,
        workflow_profile={"enabled": False},
        workflow_enabled=False,
    )
    cache.put(
        key,
        {"outcome": {"scenario_id": entry["scenario_id"], "variant": "text"}},
        http_status=200,
        technical_failure=None,
    )

    report = runner.quota_preflight(
        [entry],
        cache=cache,
        resume=True,
        run_workflow=False,
    )

    assert report["estimate"]["selected_scenarios"] == 0
    assert report["estimate"]["persisted_cache_hits"] == 0
    assert report["capacity"]["admission_estimate_tokens"] == 0


def test_retry_blocked_forces_live_work_even_when_an_older_success_is_cached(
    tmp_path: Path,
) -> None:
    manifest = json.loads(runner.MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = next(
        row
        for row in manifest["supporting_document_scenarios"]
        if row["scenario_id"] == "clean_cotton_yarn_supporting"
        and row["variant"] == "text"
    )
    cache = EvaluationCache(tmp_path / "cache")
    paths = runner.scenario_document_paths(entry)
    workflow_profile = runner.workflow_cache_profile(run_workflow=False)
    key = cache.key(
        scenario_id=entry["scenario_id"],
        variant=entry["variant"],
        document_paths=paths,
        scenario_inputs=entry,
        workflow_profile=workflow_profile,
        workflow_enabled=False,
    )
    cache.put(
        key,
        {"outcome": {"scenario_id": entry["scenario_id"], "variant": "text"}},
        http_status=200,
        technical_failure=None,
    )

    planned = runner.entries_requiring_live_extraction(
        [entry],
        cache=cache,
        resume=True,
        run_workflow=False,
        force_live=True,
    )

    assert planned == [entry]


def test_cache_key_binds_normalized_manifest_and_request_inputs(
    tmp_path: Path,
) -> None:
    manifest = json.loads(runner.MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = next(
        row
        for row in manifest["supporting_document_scenarios"]
        if row["scenario_id"] == "clean_cotton_yarn_supporting"
        and row["variant"] == "text"
    )
    cache = EvaluationCache(tmp_path / "cache")
    paths = runner.scenario_document_paths(entry)
    profile = {"enabled": False}
    original = cache.key(
        scenario_id=entry["scenario_id"],
        variant=entry["variant"],
        document_paths=paths,
        scenario_inputs=entry,
        workflow_profile=profile,
        workflow_enabled=False,
    )

    changed_date = deepcopy(entry)
    changed_date["shipment"]["shipment_date"] = "2026-07-26"
    changed_claim = deepcopy(entry)
    changed_claim["expected_documents"][0]["claimed_document_type"] = (
        "different_document_type"
    )

    assert cache.key(
        scenario_id=entry["scenario_id"],
        variant=entry["variant"],
        document_paths=paths,
        scenario_inputs=changed_date,
        workflow_profile=profile,
        workflow_enabled=False,
    ) != original
    assert cache.key(
        scenario_id=entry["scenario_id"],
        variant=entry["variant"],
        document_paths=paths,
        scenario_inputs=changed_claim,
        workflow_profile=profile,
        workflow_enabled=False,
    ) != original


def test_cache_key_binds_workflow_models_settings_and_checkpoint_backend(
    tmp_path: Path,
) -> None:
    document = tmp_path / "invoice.pdf"
    document.write_bytes(b"invoice")
    cache = EvaluationCache(tmp_path / "cache")
    base = {
        "enabled": True,
        "live_agents_enabled": True,
        "broker_model": "broker/model-a",
        "auditor_model": "auditor/model-a",
        "human_review_required": True,
        "checkpoint_backend": "postgres",
    }

    first = cache.key(
        scenario_id="scenario",
        variant="text",
        document_paths=[document],
        scenario_inputs={"shipment_date": "2026-07-25"},
        workflow_profile=base,
        workflow_enabled=True,
    )

    for field, changed_value in (
        ("broker_model", "broker/model-b"),
        ("auditor_model", "auditor/model-b"),
        ("human_review_required", False),
        ("checkpoint_backend", "sqlite"),
    ):
        changed = {**base, field: changed_value}
        assert cache.key(
            scenario_id="scenario",
            variant="text",
            document_paths=[document],
            scenario_inputs={"shipment_date": "2026-07-25"},
            workflow_profile=changed,
            workflow_enabled=True,
        ) != first


def test_evaluation_fingerprint_combines_every_runtime_extraction_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = {
        name: {
            "extraction_model": "extraction/model",
            "prompt_version": f"{name}-prompt-v1",
            "schema_version": f"{name}-schema-v1",
            "ocr_settings": {"language": "eng", "dpi": 300},
        }
        for name in (
            "commercial_invoice",
            "packing_list",
            "supporting_document",
        )
    }
    monkeypatch.setattr(
        evaluation_cache_module,
        "runtime_extraction_cache_capability",
        lambda: {"profiles": deepcopy(profiles)},
    )

    original = CacheFingerprint.current()
    profiles["supporting_document"]["prompt_version"] = (
        "supporting_document-prompt-v2"
    )
    prompt_changed = CacheFingerprint.current()
    profiles["packing_list"]["schema_version"] = "packing_list-schema-v2"
    schema_changed = CacheFingerprint.current()

    assert prompt_changed.prompt_version != original.prompt_version
    assert prompt_changed.schema_version == original.schema_version
    assert schema_changed.schema_version != prompt_changed.schema_version


def test_quota_preflight_observes_each_distinct_runtime_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = json.loads(runner.MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = next(
        row
        for row in manifest["scenarios"]
        if row["variant"] == "text"
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_test_only"))
    monkeypatch.setattr(settings, "groq_model", "extraction/model")
    monkeypatch.setattr(settings, "langgraph_enable_live_agents", True)
    monkeypatch.setattr(settings, "langgraph_broker_model", "broker/model")
    monkeypatch.setattr(settings, "langgraph_auditor_model", "auditor/model")
    snapshot = tmp_path / "groq_console_snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "source": "groq_console",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "models": {
                    model: {"limit": 100_000_000, "used": 0}
                    for model in (
                        "extraction/model",
                        "broker/model",
                        "auditor/model",
                    )
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner,
        "read_daily_token_quota",
        lambda **_kwargs: pytest.fail(
            "An exact operator snapshot must not send a diagnostic request."
        ),
    )
    report = runner.quota_preflight(
        [entry],
        cache=EvaluationCache(tmp_path / "cache", enabled=False),
        resume=False,
        run_workflow=True,
        quota_snapshot_path=snapshot,
    )

    assert report["quota_source"] == "operator_console_snapshot"
    assert set(report["capacity_by_model"]) == {
        "auditor/model",
        "broker/model",
        "extraction/model",
    }


def test_quota_preflight_without_snapshot_sends_one_probe_and_reports_costs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = json.loads(runner.MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = next(row for row in manifest["scenarios"] if row["variant"] == "text")
    settings = get_settings()
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_test_only"))
    calls: list[str] = []

    def observed_quota(*, api_key: str, model: str) -> DailyTokenQuota:
        assert api_key == "gsk_test_only"
        calls.append(model)
        return DailyTokenQuota(
            limit=200_000,
            used=0,
            requested=1_000_000,
        )

    monkeypatch.setattr(runner, "read_daily_token_quota", observed_quota)

    report = runner.quota_preflight(
        [entry],
        cache=EvaluationCache(tmp_path / "cache", enabled=False),
        resume=False,
        run_workflow=False,
    )

    assert calls == [settings.groq_model]
    assert report["quota_source"] == "oversized_diagnostic_tpd_429"
    assert report["estimate"]["planning_estimate_tokens"] == 8_200
    assert report["estimate"]["admission_estimate_tokens"] == 9_840
    assert report["estimate"]["stress_path_tokens"] > 9_840
    assert report["capacity"]["can_run"] is True


def test_unusable_quota_probe_still_reports_every_selected_run_estimate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = json.loads(runner.MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = next(row for row in manifest["scenarios"] if row["variant"] == "text")
    settings = get_settings()
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_test_only"))

    def unusable_probe(**_kwargs: Any) -> DailyTokenQuota:
        raise QuotaPreflightError(
            "Groq quota diagnostic returned HTTP 400; daily-token capacity "
            "could not be verified."
        )

    monkeypatch.setattr(runner, "read_daily_token_quota", unusable_probe)

    with pytest.raises(QuotaPreflightError) as captured:
        runner.quota_preflight(
            [entry],
            cache=EvaluationCache(tmp_path / "cache", enabled=False),
            resume=False,
            run_workflow=False,
        )

    rendered = str(captured.value)
    assert "planning_estimate_tokens=8200" in rendered
    assert "admission_estimate_tokens=9840" in rendered
    assert "stress_path_tokens=" in rendered
    assert "HTTP 400" in rendered


def test_default_quota_diagnostic_refuses_insufficient_admission_headroom(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = json.loads(runner.MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = next(row for row in manifest["scenarios"] if row["variant"] == "text")
    settings = get_settings()
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_test_only"))
    calls: list[str] = []

    def observed_quota(*, api_key: str, model: str) -> DailyTokenQuota:
        assert api_key == "gsk_test_only"
        calls.append(model)
        return DailyTokenQuota(
            limit=200_000,
            used=199_257,
            requested=1_000_000,
        )

    monkeypatch.setattr(runner, "read_daily_token_quota", observed_quota)

    with pytest.raises(QuotaPreflightError, match="admission estimate"):
        runner.quota_preflight(
            [entry],
            cache=EvaluationCache(tmp_path / "cache", enabled=False),
            resume=False,
            run_workflow=False,
        )

    assert calls == [settings.groq_model]


@pytest.mark.parametrize("provider_status", [502, 503])
def test_http_502_or_503_stops_before_next_scenario_and_reports_partial_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_status: int,
) -> None:
    manifest_path = tmp_path / "scenario_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "scenarios": [
                    {"scenario_id": "first", "variant": "text"},
                    {"scenario_id": "second", "variant": "text"},
                ],
                "supporting_document_scenarios": [],
            }
        ),
        encoding="utf-8",
    )
    reports = tmp_path / "reports"
    calls: list[str] = []

    class FakeApi:
        uploads_reused = 0

        def __init__(self, _base_url: str) -> None:
            pass

        def close(self) -> None:
            calls.append("close")

    def fake_evaluate(
        _api: Any,
        entry: dict[str, Any],
        **_kwargs: Any,
    ) -> runner.ScenarioOutcome:
        calls.append(entry["scenario_id"])
        return runner.ScenarioOutcome(
            scenario_id=entry["scenario_id"],
            variant=entry["variant"],
            http_status=provider_status,
            technical_failure=f"multi-line HTTP {provider_status}",
        )

    monkeypatch.setattr(runner, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(runner, "REPORTS_DIR", reports)
    monkeypatch.setattr(runner, "LiveApiClient", FakeApi)
    monkeypatch.setattr(
        runner,
        "preflight",
        lambda *_args, **_kwargs: {"api": "ok"},
    )
    monkeypatch.setattr(
        runner,
        "quota_preflight",
        lambda *_args, **_kwargs: {
            "capacity": {
                "can_run": True,
                "planning_estimate_tokens": 1,
                "admission_estimate_tokens": 2,
                "stress_path_tokens": 3,
            }
        },
    )
    monkeypatch.setattr(runner, "evaluate_scenario", fake_evaluate)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_synthetic_factory_evaluation",
            "--no-workflow",
            "--no-cache",
            "--output-prefix",
            "stopped",
        ],
    )

    assert runner.main() == 2
    assert calls == ["first", "close"]
    report = json.loads((reports / "stopped.json").read_text(encoding="utf-8"))
    assert report["run_stopped_early"] is True
    assert report["stopped_early_http_status"] == provider_status
    assert report["unattempted_scenarios"] == 1
    assert [row["scenario_id"] for row in report["scenarios"]] == ["first"]
