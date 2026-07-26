from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import run_synthetic_factory_evaluation as runner
from scripts.groq_quota_preflight import (
    ADMISSION_RESERVE_PERCENT,
    DEFAULT_AGENT_SDK_ATTEMPTS,
    DEFAULT_EXTRACTION_SDK_ATTEMPTS,
    DEFAULT_QUOTA_PROBE_TOKENS,
    DEFAULT_QUOTA_SNAPSHOT_MAX_AGE_SECONDS,
    DEFAULT_TOKENS_PER_AGENT_NARRATION,
    DEFAULT_TOKENS_PER_EXTRACTION,
    GROQ_MAX_COMPLETION_TOKENS_CEILING,
    MULTI_LINE_LOGICAL_CALL_UPPER_BOUND,
    SUPPORTING_LOGICAL_CALL_UPPER_BOUND,
    DailyTokenQuota,
    QuotaPreflightError,
    assess_quota_capacity,
    estimate_selected_scenarios,
    load_operator_quota_snapshot,
    operator_cli_quota,
    parse_tpd_quota_error,
    probe_daily_token_quota,
)


REAL_TPD_MESSAGE = (
    "Rate limit reached for model `openai/gpt-oss-20b` in organization "
    "`org_private` service tier `on_demand` on tokens per day (TPD): "
    "Limit 200,000, Used 199,257, Requested 1,000,000. "
    "Please try again later."
)


class ProviderError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        error_type: str = "rate_limit_exceeded",
    ) -> None:
        super().__init__("provider request failed")
        self.status_code = status_code
        self.body = {
            "error": {
                "type": error_type,
                "message": message,
            }
        }


def test_real_tpd_error_parses_limit_used_requested_and_headroom() -> None:
    quota = parse_tpd_quota_error(
        ProviderError(status_code=429, message=REAL_TPD_MESSAGE)
    )

    assert quota == DailyTokenQuota(
        limit=200_000,
        used=199_257,
        requested=1_000_000,
    )
    assert quota.headroom == 743


@pytest.mark.parametrize(
    "error",
    [
        ProviderError(
            status_code=429,
            message=(
                "tokens per minute (TPM): Limit 8000, Used 7000, "
                "Requested 2000"
            ),
        ),
        ProviderError(status_code=401, message="invalid API key"),
        ProviderError(status_code=503, message="upstream unavailable"),
        ProviderError(status_code=429, message="rate limit reached"),
    ],
)
def test_non_tpd_or_malformed_errors_fail_closed(error: Exception) -> None:
    with pytest.raises(QuotaPreflightError):
        parse_tpd_quota_error(error)


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, "HTTP 400"),
        (429, "HTTP 429 without a parseable TPD limit"),
        (503, "HTTP 503"),
    ],
)
def test_unusable_quota_response_reports_only_sanitized_http_class(
    status_code: int,
    expected: str,
) -> None:
    private_message = "organization org_private key gsk_PRIVATE"

    with pytest.raises(QuotaPreflightError) as captured:
        parse_tpd_quota_error(
            ProviderError(status_code=status_code, message=private_message)
        )

    rendered = str(captured.value)
    assert expected in rendered
    assert private_message not in rendered
    assert "org_private" not in rendered
    assert "gsk_PRIVATE" not in rendered


def test_quota_probe_token_value_is_within_groq_accepted_range() -> None:
    """DEF-013 (live): the probe must be a value Groq accepts for `max_tokens`.

    Groq enforces a hard per-request ceiling on `max_tokens`/
    `max_completion_tokens` independent of the model's context window. A probe
    of 1_000_000 was rejected as HTTP 400 ("max_tokens must be less than or
    equal to 65536"), which is caught by ``parse_tpd_quota_error`` and
    correctly fails closed - but that means the diagnostic never reaches the
    TPD check it exists to trigger. A too-large probe silently degrades the
    preflight from "reliably observes daily headroom" to "always refuses to
    run", which is safe but makes the whole mechanism useless. Confirmed live:
    the identical request with max_completion_tokens=65536 was accepted for
    validation and returned a parseable TPD 429.
    """
    assert 0 < DEFAULT_QUOTA_PROBE_TOKENS <= GROQ_MAX_COMPLETION_TOKENS_CEILING


def test_quota_probe_makes_exactly_one_oversized_tiny_prompt_request() -> None:
    calls: list[dict[str, Any]] = []

    class Completions:
        def create(self, **kwargs: Any) -> None:
            calls.append(kwargs)
            raise ProviderError(status_code=429, message=REAL_TPD_MESSAGE)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()),
    )

    quota = probe_daily_token_quota(
        client=client,
        model="openai/gpt-oss-20b",
    )

    assert quota.headroom == 743
    assert len(calls) == 1
    assert calls[0]["max_completion_tokens"] == DEFAULT_QUOTA_PROBE_TOKENS
    assert calls[0]["model"] == "openai/gpt-oss-20b"
    assert calls[0]["messages"] == [
        {"role": "user", "content": "Reply with OK."}
    ]


def test_successful_diagnostic_probe_reports_tpd_is_unobservable() -> None:
    class Completions:
        def create(self, **_kwargs: Any) -> object:
            return object()

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()),
    )

    with pytest.raises(
        QuotaPreflightError,
        match="TPD headroom is unobservable.*may have consumed",
    ):
        probe_daily_token_quota(
            client=client,
            model="openai/gpt-oss-20b",
        )


def test_probe_error_does_not_repeat_provider_body_or_identifiers() -> None:
    private_message = (
        "organization org_private key gsk_PRIVATE tokens per minute (TPM): "
        "Limit 8000, Used 8000, Requested 1"
    )

    class Completions:
        def create(self, **_kwargs: Any) -> None:
            raise ProviderError(status_code=429, message=private_message)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()),
    )

    with pytest.raises(QuotaPreflightError) as captured:
        probe_daily_token_quota(
            client=client,
            model="openai/gpt-oss-20b",
        )

    rendered = str(captured.value)
    assert "org_private" not in rendered
    assert "gsk_PRIVATE" not in rendered
    assert private_message not in rendered


def _write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path.relative_to(path.parents[2]))


def test_estimate_deduplicates_same_bytes_per_extraction_profile(
    tmp_path: Path,
) -> None:
    factory = tmp_path / "synthetic_factory"
    invoice = factory / "base" / "invoice_text.pdf"
    packing = factory / "base" / "packing_text.pdf"
    supporting = factory / "supporting" / "coo_text.pdf"
    invoice.parent.mkdir(parents=True)
    supporting.parent.mkdir(parents=True)
    invoice.write_bytes(b"same invoice and supporting bytes")
    packing.write_bytes(b"packing bytes")
    supporting.write_bytes(invoice.read_bytes())

    base_documents = {
        "commercial_invoice_text": str(invoice.relative_to(factory)),
        "packing_list_text": str(packing.relative_to(factory)),
    }
    expected_document = {
        "uploaded": True,
        "pdf": str(supporting.relative_to(factory)),
    }
    entries = [
        {
            "scenario_id": "one",
            "variant": "text",
            "kind": "supporting_documents",
            "base_scenario_documents": base_documents,
            "expected_documents": [expected_document],
            "claimed_only_document_types": ["form_e"],
        },
        {
            "scenario_id": "two",
            "variant": "text",
            "kind": "supporting_documents",
            "base_scenario_documents": base_documents,
            "expected_documents": [expected_document],
            "claimed_only_document_types": ["bill_of_lading"],
        },
    ]

    estimate = estimate_selected_scenarios(entries, factory_root=factory)

    assert estimate.selected_scenarios == 2
    assert estimate.document_references == 6
    # Invoice and supporting bytes are identical but use different prompts and
    # schemas, so they are two extraction profiles. The packing list is third.
    assert estimate.unique_extractions == 3
    assert estimate.planning_estimate_tokens == (
        3 * DEFAULT_TOKENS_PER_EXTRACTION
    )
    assert estimate.admission_estimate_tokens == 14_760
    assert estimate.stress_path_tokens == (
        2
        * MULTI_LINE_LOGICAL_CALL_UPPER_BOUND
        * DEFAULT_EXTRACTION_SDK_ATTEMPTS
        + SUPPORTING_LOGICAL_CALL_UPPER_BOUND
        * DEFAULT_EXTRACTION_SDK_ATTEMPTS
    ) * DEFAULT_TOKENS_PER_EXTRACTION
    assert estimate.planned_same_run_reuses == 3
    assert estimate.persisted_cache_hits == 0


def test_estimate_keeps_distinct_text_and_scanned_bytes(tmp_path: Path) -> None:
    factory = tmp_path / "synthetic_factory"
    scenario = factory / "shipment"
    scenario.mkdir(parents=True)
    (scenario / "synthetic_commercial_invoice_text.pdf").write_bytes(b"invoice text")
    (scenario / "synthetic_packing_list_text.pdf").write_bytes(b"packing text")
    (scenario / "synthetic_commercial_invoice_scanned.pdf").write_bytes(
        b"invoice scan"
    )
    (scenario / "synthetic_packing_list_scanned.pdf").write_bytes(b"packing scan")
    entries = [
        {"scenario_id": "shipment", "variant": "text"},
        {"scenario_id": "shipment", "variant": "scanned"},
    ]

    estimate = estimate_selected_scenarios(entries, factory_root=factory)

    assert estimate.document_references == 4
    assert estimate.unique_extractions == 4


def test_capacity_assessment_allows_equality_and_reports_shortfall() -> None:
    estimate = SimpleNamespace(admission_estimate_tokens=10_000)

    exact = assess_quota_capacity(
        DailyTokenQuota(limit=20_000, used=10_000, requested=4_100),
        estimate,
    )
    short = assess_quota_capacity(
        DailyTokenQuota(limit=20_000, used=10_001, requested=4_100),
        estimate,
    )

    assert exact.can_run is True
    assert exact.admission_estimate_tokens == 10_000
    assert exact.shortfall == 0
    assert short.can_run is False
    assert short.shortfall == 1


def test_live_workflow_agent_narrations_are_included_in_estimate(
    tmp_path: Path,
) -> None:
    factory = tmp_path / "synthetic_factory"
    scenario = factory / "shipment"
    scenario.mkdir(parents=True)
    (scenario / "synthetic_commercial_invoice_text.pdf").write_bytes(b"invoice")
    (scenario / "synthetic_packing_list_text.pdf").write_bytes(b"packing")

    estimate = estimate_selected_scenarios(
        [{"scenario_id": "shipment", "variant": "text"}],
        factory_root=factory,
        workflow_enabled=True,
        live_agents_enabled=True,
        extraction_model="extraction/model",
        broker_model="broker/model",
        auditor_model="auditor/model",
    )

    assert estimate.agent_narrations == 2
    assert estimate.sdk_attempts_per_logical_call == 1
    assert estimate.agent_sdk_attempts_per_narration == 1
    assert estimate.planning_estimate_tokens == (
        2 * DEFAULT_TOKENS_PER_EXTRACTION
        + 2 * DEFAULT_TOKENS_PER_AGENT_NARRATION
    )
    assert estimate.admission_estimate_tokens == 19_680
    assert estimate.stress_path_tokens == (
        2
        * MULTI_LINE_LOGICAL_CALL_UPPER_BOUND
        * DEFAULT_EXTRACTION_SDK_ATTEMPTS
        * DEFAULT_TOKENS_PER_EXTRACTION
        + 2
        * DEFAULT_AGENT_SDK_ATTEMPTS
        * DEFAULT_TOKENS_PER_AGENT_NARRATION
    )
    assert estimate.planning_estimate_tokens_by_model == {
        "extraction/model": 2 * DEFAULT_TOKENS_PER_EXTRACTION,
        "broker/model": DEFAULT_TOKENS_PER_AGENT_NARRATION,
        "auditor/model": DEFAULT_TOKENS_PER_AGENT_NARRATION,
    }
    assert estimate.admission_estimate_tokens_by_model == {
        "extraction/model": 9_840,
        "broker/model": 4_920,
        "auditor/model": 4_920,
    }
    assert estimate.stress_path_tokens_by_model == {
        "extraction/model": (
            2
            * MULTI_LINE_LOGICAL_CALL_UPPER_BOUND
            * DEFAULT_EXTRACTION_SDK_ATTEMPTS
            * DEFAULT_TOKENS_PER_EXTRACTION
        ),
        "broker/model": (
            DEFAULT_AGENT_SDK_ATTEMPTS * DEFAULT_TOKENS_PER_AGENT_NARRATION
        ),
        "auditor/model": (
            DEFAULT_AGENT_SDK_ATTEMPTS * DEFAULT_TOKENS_PER_AGENT_NARRATION
        ),
    }


def test_real_text_error_batch_estimate_reflects_document_deduplication() -> None:
    manifest = runner.json.loads(runner.MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = [
        entry
        for entry in manifest["supporting_document_scenarios"]
        if entry["variant"] == "text"
        and entry["scenario_id"].startswith("supporting_")
    ]

    estimate = estimate_selected_scenarios(
        entries,
        factory_root=runner.FACTORY_ROOT,
    )

    assert len(entries) == 15
    assert estimate.document_references == 60
    assert estimate.unique_extractions == 34
    # The measured planning figure is one observed extraction cost per unique
    # document/profile. The fail-closed gate separately includes the bounded
    # staged fallback and strict + JSON-object transports. SDK retries are
    # disabled in the production extraction client.
    assert estimate.profile_counts == {
        "commercial_invoice": 2,
        "packing_list": 2,
        "supporting_document": 30,
    }
    assert estimate.planning_estimate_tokens == (
        34 * DEFAULT_TOKENS_PER_EXTRACTION
    )
    assert estimate.admission_estimate_tokens == 167_280
    assert estimate.stress_path_tokens == (
        (
            4 * MULTI_LINE_LOGICAL_CALL_UPPER_BOUND
            + 30 * SUPPORTING_LOGICAL_CALL_UPPER_BOUND
        )
        * DEFAULT_EXTRACTION_SDK_ATTEMPTS
        * DEFAULT_TOKENS_PER_EXTRACTION
    )
    assert estimate.planned_same_run_reuses == 26
    assert estimate.persisted_cache_hits == 0


def test_real_text_error_batch_admission_uses_the_measured_cost_estimate() -> None:
    """The first safety batch must remain runnable within a fresh free-tier day.

    The 4,100-token figure is the measured conservative cost of one completed
    extraction, not a per-call mathematical ceiling. Treating every reachable
    40-row recovery branch as if it necessarily consumed another 4,100 tokens
    makes even one scenario impossible to admit and defeats the requested
    preflight. Admission therefore compares headroom with the reported measured
    estimate, while provider 429/503 handling remains the runtime stop guard.
    """
    manifest = runner.json.loads(runner.MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = [
        entry
        for entry in manifest["supporting_document_scenarios"]
        if entry["variant"] == "text"
        and entry["scenario_id"].startswith("supporting_")
    ]
    estimate = estimate_selected_scenarios(
        entries,
        factory_root=runner.FACTORY_ROOT,
    )

    full_day = assess_quota_capacity(
        DailyTokenQuota(limit=200_000, used=0, requested=None),
        estimate,
    )
    one_token_short = assess_quota_capacity(
        DailyTokenQuota(
            limit=200_000,
            used=200_000 - estimate.admission_estimate_tokens + 1,
            requested=None,
        ),
        estimate,
    )

    assert ADMISSION_RESERVE_PERCENT == 20
    assert estimate.planning_estimate_tokens == 139_400
    assert estimate.admission_estimate_tokens == 167_280
    assert estimate.stress_path_tokens == 1_656_400
    assert full_day.headroom == 200_000
    assert full_day.can_run is True
    assert one_token_short.headroom == 167_279
    assert one_token_short.can_run is False
    assert one_token_short.shortfall == 1


def test_estimate_groups_distinct_extraction_and_agent_models(
    tmp_path: Path,
) -> None:
    factory = tmp_path / "synthetic_factory"
    scenario = factory / "shipment"
    scenario.mkdir(parents=True)
    (scenario / "synthetic_commercial_invoice_text.pdf").write_bytes(b"invoice")
    (scenario / "synthetic_packing_list_text.pdf").write_bytes(b"packing")

    estimate = estimate_selected_scenarios(
        [{"scenario_id": "shipment", "variant": "text"}],
        factory_root=factory,
        workflow_enabled=True,
        live_agents_enabled=True,
        extraction_model="extraction/model",
        broker_model="broker/model",
        auditor_model="auditor/model",
    )

    expected_models = {
        "extraction/model",
        "broker/model",
        "auditor/model",
    }
    assert set(estimate.planning_estimate_tokens_by_model) == expected_models
    assert set(estimate.admission_estimate_tokens_by_model) == expected_models
    assert set(estimate.stress_path_tokens_by_model) == expected_models


def test_fresh_operator_console_snapshot_supplies_each_exact_model(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    snapshot = tmp_path / "groq_tpd.json"
    snapshot.write_text(
        json.dumps(
            {
                "source": "groq_console",
                "observed_at": now.isoformat(),
                "models": {
                    "extraction/model": {"limit": 200_000, "used": 10_000},
                    "broker/model": {"limit": 50_000, "used": 2_000},
                },
            }
        ),
        encoding="utf-8",
    )

    quotas = load_operator_quota_snapshot(
        snapshot,
        required_models={"extraction/model", "broker/model"},
        now=now,
    )

    assert quotas == {
        "extraction/model": DailyTokenQuota(
            limit=200_000,
            used=10_000,
            requested=None,
        ),
        "broker/model": DailyTokenQuota(
            limit=50_000,
            used=2_000,
            requested=None,
        ),
    }


def test_stale_or_incomplete_operator_snapshot_fails_closed(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    snapshot = tmp_path / "groq_tpd.json"
    snapshot.write_text(
        json.dumps(
            {
                "source": "groq_console",
                "observed_at": (
                    now
                    - timedelta(
                        seconds=DEFAULT_QUOTA_SNAPSHOT_MAX_AGE_SECONDS + 1
                    )
                ).isoformat(),
                "models": {
                    "extraction/model": {"limit": 200_000, "used": 10_000},
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(QuotaPreflightError, match="stale"):
        load_operator_quota_snapshot(
            snapshot,
            required_models={"extraction/model"},
            now=now,
        )

    snapshot.write_text(
        json.dumps(
            {
                "source": "groq_console",
                "observed_at": now.isoformat(),
                "models": {
                    "extraction/model": {"limit": 200_000, "used": 10_000},
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(QuotaPreflightError, match="auditor/model"):
        load_operator_quota_snapshot(
            snapshot,
            required_models={"extraction/model", "auditor/model"},
            now=now,
        )


def test_cli_limit_used_is_allowed_only_for_one_selected_model() -> None:
    assert operator_cli_quota(
        limit=200_000,
        used=12_000,
        required_models={"extraction/model"},
    ) == {
        "extraction/model": DailyTokenQuota(
            limit=200_000,
            used=12_000,
            requested=None,
        )
    }
    with pytest.raises(QuotaPreflightError, match="multiple models"):
        operator_cli_quota(
            limit=200_000,
            used=12_000,
            required_models={"extraction/model", "auditor/model"},
        )
