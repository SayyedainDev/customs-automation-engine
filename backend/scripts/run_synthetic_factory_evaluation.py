"""Live evaluation of the whole synthetic factory against the real HTTP API.

This runner never calls internal service functions to produce a result. Every
observation comes from the running FastAPI application over HTTP, so what it
measures is the real deployed behaviour (real Groq, real Tesseract, real
PostgreSQL, real embedding/reranker models).

Per scenario/variant it:

1. uploads the commercial invoice and packing list,
2. uploads any supporting-document PDFs that exist on disk,
3. runs the multi-line compliance endpoint (field-level extraction, matching,
   per-item checks, shipment checks, deterministic + executable rule results),
4. starts the LangGraph customs-audit workflow and polls it to a terminal state
   (completed / awaiting_human_review / failed),
5. collects the Broker report, Auditor report, consensus, human-review request,
   user-readable report, legal evidence and audit events,
6. compares everything against the independent ``scenario_manifest.json``,
7. writes raw API responses for debugging and preserves every workflow ID.

Usage:
    python -m scripts.run_synthetic_factory_evaluation
    python -m scripts.run_synthetic_factory_evaluation --only multi_line_shipment
    python -m scripts.run_synthetic_factory_evaluation --variant text
    python -m scripts.run_synthetic_factory_evaluation --variant scanned
    python -m scripts.run_synthetic_factory_evaluation --resume-failed
    python -m scripts.run_synthetic_factory_evaluation --live-models-required
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from groq import Groq

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

FACTORY_ROOT = PROJECT_ROOT / "synthetic_factory"
MANIFEST_PATH = FACTORY_ROOT / "scenario_manifest.json"
REPORTS_DIR = BACKEND_ROOT / "reports"
RAW_DIR = REPORTS_DIR / "synthetic_factory_raw"

DEFAULT_API = "http://127.0.0.1:8000"

from scripts.evaluation_cache import EvaluationCache, file_digest  # noqa: E402
from scripts.groq_quota_preflight import (  # noqa: E402
    DEFAULT_QUOTA_PROBE_TOKENS,
    DEFAULT_QUOTA_SNAPSHOT_MAX_AGE_SECONDS,
    DailyTokenQuota,
    QuotaPreflightError,
    assess_quota_capacity,
    estimate_selected_scenarios,
    load_operator_quota_snapshot,
    operator_cli_quota,
    probe_daily_token_quota,
)
from app.services.extraction.cache_fingerprint import (  # noqa: E402
    runtime_extraction_cache_capability,
)
from app.schemas.supporting_documents import (  # noqa: E402
    SupportingDocumentType,
    canonical_supporting_type,
)


class EvaluationError(RuntimeError):
    """A fatal problem that makes the run not a valid live validation."""


# --------------------------------------------------------------------------- #
# Comparison helpers
# --------------------------------------------------------------------------- #
def _num(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def values_equal(expected: Any, actual: Any) -> bool:
    """Compare tolerantly across string/number representations only.

    This never rewrites an extracted value; it only decides whether the value
    the API returned means the same thing as the manifest expectation.
    """
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    expected_num, actual_num = _num(expected), _num(actual)
    if expected_num is not None and actual_num is not None:
        return expected_num == actual_num
    return str(expected).strip().casefold() == str(actual).strip().casefold()


def _field_value(field_obj: Any) -> Any:
    if isinstance(field_obj, dict):
        return field_obj.get("value")
    return None


@dataclass
class FieldOutcome:
    name: str
    expected: Any
    actual: Any
    correct: bool
    missing: bool
    manual_review: bool


@dataclass
class ScenarioOutcome:
    scenario_id: str
    variant: str
    ok: bool = False
    technical_failure: str | None = None
    workflow_id: str | None = None
    workflow_status: str | None = None
    deterministic_status: str | None = None
    expected_status: str | None = None
    status_correct: bool = False
    false_pass: bool = False
    false_failure: bool = False
    false_manual_review: bool = False
    fields: list[FieldOutcome] = field(default_factory=list)
    line_item_count_expected: int = 0
    line_item_count_actual: int = 0
    matching: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, Any] = field(default_factory=dict)
    ocr: dict[str, Any] = field(default_factory=dict)
    fallback_ran: bool = False
    fallback_expected: bool = False
    human_review_actual: bool = False
    human_review_expected: bool = False
    duration_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    #: HTTP status of the compliance call - the cache refuses to store 429/503.
    http_status: int | None = None
    #: False when a started workflow never reached a terminal state, which must
    #: never be cached as if it were this scenario's answer.
    workflow_reached_terminal_state: bool = True
    #: Evidence that the workflow exercised every required safety artifact. The
    #: direct deterministic result remains authoritative; these flags only
    #: grade the surrounding Broker/Auditor/LangGraph path.
    workflow_validation: dict[str, Any] = field(default_factory=dict)
    supporting_documents: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "variant": self.variant,
            "ok": self.ok,
            "technical_failure": self.technical_failure,
            "workflow_id": self.workflow_id,
            "workflow_status": self.workflow_status,
            "deterministic_status": self.deterministic_status,
            "expected_status": self.expected_status,
            "status_correct": self.status_correct,
            "false_pass": self.false_pass,
            "false_failure": self.false_failure,
            "false_manual_review": self.false_manual_review,
            "line_item_count_expected": self.line_item_count_expected,
            "line_item_count_actual": self.line_item_count_actual,
            "fields": [
                {
                    "name": f.name,
                    "expected": f.expected,
                    "actual": f.actual,
                    "correct": f.correct,
                    "missing": f.missing,
                    "manual_review": f.manual_review,
                }
                for f in self.fields
            ],
            "matching": self.matching,
            "checks": self.checks,
            "ocr": self.ocr,
            "fallback_ran": self.fallback_ran,
            "fallback_expected": self.fallback_expected,
            "human_review_actual": self.human_review_actual,
            "human_review_expected": self.human_review_expected,
            "duration_seconds": round(self.duration_seconds, 2),
            "notes": self.notes,
            "http_status": self.http_status,
            "workflow_reached_terminal_state": self.workflow_reached_terminal_state,
            "workflow_validation": self.workflow_validation,
            "supporting_documents": self.supporting_documents,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ScenarioOutcome":
        outcome = cls(scenario_id=data["scenario_id"], variant=data["variant"])
        for name, value in data.items():
            if name == "fields":
                outcome.fields = [FieldOutcome(**row) for row in value]
            elif hasattr(outcome, name):
                setattr(outcome, name, value)
        return outcome


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
class LiveApiClient:
    def __init__(self, base_url: str, timeout: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, trust_env=False)
        self._uploads: dict[str, str] = {}
        self.uploads_reused = 0

    def close(self) -> None:
        self._client.close()

    def get(self, path: str) -> httpx.Response:
        return self._client.get(path)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self._client.post(path, **kwargs)

    def upload(self, path: Path) -> str:
        """Upload a PDF, reusing the document already uploaded for identical bytes.

        Eight of the supporting-document bundles share one base invoice and
        packing list. Uploading each copy separately creates a separate document
        record, and every record is extracted separately - so the same two pages
        would be sent to the model eight times. Memoizing on content hash keeps
        one document per distinct file for the duration of a run.
        """
        digest = file_digest(path)
        cached = self._uploads.get(digest)
        if cached is not None:
            self.uploads_reused += 1
            return cached
        with path.open("rb") as handle:
            response = self._client.post(
                "/documents/upload",
                files={"file": (path.name, handle, "application/pdf")},
            )
        response.raise_for_status()
        document_id = str(response.json()["document_id"])
        self._uploads[digest] = document_id
        return document_id


# --------------------------------------------------------------------------- #
# Preflight: prove the run is genuinely live
# --------------------------------------------------------------------------- #
def preflight(
    api: LiveApiClient,
    *,
    need_ocr: bool,
    need_workflow: bool,
    strict: bool,
) -> dict[str, Any]:
    report: dict[str, Any] = {}

    try:
        health = api.get("/health")
        health.raise_for_status()
        report["api"] = health.json().get("status")
    except Exception as exc:  # noqa: BLE001
        raise EvaluationError(f"API is not reachable at {api.base_url}: {exc}") from exc

    try:
        database = api.get("/health/database")
        database.raise_for_status()
        report["database"] = database.json()
    except Exception as exc:  # noqa: BLE001
        raise EvaluationError(f"Database health check failed: {exc}") from exc

    from app.core.config import get_settings

    settings = get_settings()
    database_url = settings.database_url or ""
    report["database_url_driver"] = database_url.split("://", 1)[0] if database_url else None
    if "postgresql" not in database_url:
        raise EvaluationError(
            f"PostgreSQL is required for live validation; configured URL is '{database_url}'."
        )

    try:
        cache_health = api.get("/health/extraction-cache")
        cache_health.raise_for_status()
        server_capability = cache_health.json()
    except Exception as exc:  # noqa: BLE001
        raise EvaluationError(
            "The API server cannot prove that per-document extraction caching "
            "is active. Restart it with the current code before a live run."
        ) from exc
    local_capability = runtime_extraction_cache_capability()
    if server_capability != local_capability:
        raise EvaluationError(
            "The API server extraction fingerprint differs from the runner. "
            "Restart uvicorn with the current code before a live run."
        )
    report["per_document_extraction_cache"] = "enabled; runtime fingerprint matched"

    report["groq_configured"] = bool(
        settings.groq_api_key and settings.groq_api_key.get_secret_value().strip()
    )
    report["groq_model"] = settings.groq_model
    if not report["groq_configured"]:
        raise EvaluationError("GROQ_API_KEY is not configured; extraction cannot be live.")

    if need_workflow:
        try:
            workflow_health = api.get("/health/customs-audit")
            workflow_health.raise_for_status()
            workflow_capability = workflow_health.json()
        except Exception as exc:  # noqa: BLE001
            raise EvaluationError(
                "The API server cannot prove that the customs-audit workflow "
                "and its checkpoint are ready. Restart it with the current code."
            ) from exc

        if (
            workflow_capability.get("checkpoint_backend") != "postgres"
            or workflow_capability.get("checkpoint_ready") is not True
        ):
            raise EvaluationError(
                "Live LangGraph validation requires a ready PostgreSQL "
                "checkpointer; the server did not prove that configuration."
            )
        configured_key = settings.groq_api_key
        if configured_key is None:  # narrowed above; retained for type safety
            raise EvaluationError(
                "GROQ_API_KEY is not configured; workflow cannot be live."
            )
        api_key = configured_key.get_secret_value()
        local_credential = (
            "sha256:"
            + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
        )
        if workflow_capability.get("groq_credential_fingerprint") != local_credential:
            raise EvaluationError(
                "The API server and quota preflight do not use the same Groq "
                "credential; refusing a capacity check against the wrong account."
            )
        expected_profile = {
            "live_agents_enabled": settings.langgraph_enable_live_agents,
            "broker_model": settings.langgraph_broker_model or settings.groq_model,
            "auditor_model": settings.langgraph_auditor_model or settings.groq_model,
            "extraction_model": settings.groq_model,
            "human_review_required": settings.langgraph_human_review_required,
        }
        mismatches = {
            key: {
                "runner": expected,
                "server": workflow_capability.get(key),
            }
            for key, expected in expected_profile.items()
            if workflow_capability.get(key) != expected
        }
        if mismatches:
            raise EvaluationError(
                "The API server LangGraph profile differs from the runner "
                f"configuration: {mismatches}. Restart uvicorn."
            )
        report["customs_audit"] = {
            "checkpoint_backend": "postgres",
            "checkpoint_ready": True,
            **expected_profile,
            "groq_credential": "matched",
        }

    report["real_models_enabled"] = settings.regulatory_enable_real_models
    if strict and not settings.regulatory_enable_real_models:
        raise EvaluationError(
            "REGULATORY_ENABLE_REAL_MODELS is false; embeddings/reranker would be degraded."
        )

    # degraded_mode is reported by the regulatory search endpoint.
    try:
        search = api.post(
            "/api/v1/regulatory-evidence/search",
            json={"query": "certificate of origin", "pct_code": "61091000", "top_k": 1},
        )
        if search.status_code == 200:
            payload = search.json()
            report["degraded_mode"] = payload.get("degraded_mode")
            report["evidence_results"] = len(payload.get("results", []) or [])
        else:
            report["degraded_mode"] = f"unavailable (HTTP {search.status_code})"
    except Exception as exc:  # noqa: BLE001
        report["degraded_mode"] = f"unavailable ({exc})"
    if strict and report.get("degraded_mode") is True:
        raise EvaluationError("Regulatory retrieval is in degraded_mode; real models required.")

    if need_ocr:
        import shutil
        import subprocess

        executable = settings.ocr_executable
        resolved = shutil.which(executable) or (
            str(BACKEND_ROOT / executable)
            if (BACKEND_ROOT / executable).exists()
            else None
        )
        if resolved is None:
            raise EvaluationError(
                f"Tesseract executable '{executable}' was not found; scanned "
                "variants cannot be validated live. Run with --variant text or "
                "install Tesseract."
            )
        try:
            version = subprocess.run(
                [resolved, "--version"], capture_output=True, text=True, timeout=30
            )
            report["tesseract"] = (version.stdout or version.stderr).splitlines()[0]
        except Exception as exc:  # noqa: BLE001
            raise EvaluationError(f"Tesseract could not be executed: {exc}") from exc

    return report


def _close_groq_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def read_daily_token_quota(
    *,
    api_key: str,
    model: str,
) -> DailyTokenQuota:
    """Observe TPD only when one oversized diagnostic receives a TPD refusal."""
    client = Groq(api_key=api_key, max_retries=0)
    try:
        return probe_daily_token_quota(
            client=client,
            model=model,
            requested_tokens=DEFAULT_QUOTA_PROBE_TOKENS,
        )
    finally:
        _close_groq_client(client)


def workflow_cache_profile(*, run_workflow: bool) -> dict[str, Any]:
    """Every runtime setting that can change a workflow observation."""
    if not run_workflow:
        return {"enabled": False}

    from app.core.config import get_settings

    settings = get_settings()
    return {
        "enabled": True,
        "live_agents_enabled": settings.langgraph_enable_live_agents,
        "broker_model": settings.langgraph_broker_model or settings.groq_model,
        "auditor_model": settings.langgraph_auditor_model or settings.groq_model,
        "human_review_required": settings.langgraph_human_review_required,
        "checkpoint_backend": settings.langgraph_checkpoint_backend,
        "checkpoint_path": settings.langgraph_checkpoint_path,
        "workflow_timeout_seconds": settings.langgraph_workflow_timeout_seconds,
        "max_retries": settings.langgraph_max_retries,
    }


def entries_requiring_live_extraction(
    entries: list[dict[str, Any]],
    *,
    cache: EvaluationCache,
    resume: bool,
    run_workflow: bool,
    force_live: bool = False,
) -> list[dict[str, Any]]:
    """Plan only scenario-cache misses without changing cache statistics."""
    if force_live or not resume or not cache.enabled:
        return list(entries)
    workflow_profile = workflow_cache_profile(run_workflow=run_workflow)
    live: list[dict[str, Any]] = []
    for entry in entries:
        documents = scenario_document_paths(entry)
        if any(not path.exists() for path in documents):
            live.append(entry)
            continue
        key = cache.key(
            scenario_id=entry["scenario_id"],
            variant=entry["variant"],
            document_paths=documents,
            scenario_inputs=entry,
            workflow_profile=workflow_profile,
            workflow_enabled=run_workflow,
        )
        cached = cache.peek(key)
        payload = cached.get("payload") if isinstance(cached, dict) else None
        if not isinstance(payload, dict) or not isinstance(
            payload.get("outcome"), dict
        ):
            live.append(entry)
    return live


def quota_preflight(
    entries: list[dict[str, Any]],
    *,
    cache: EvaluationCache,
    resume: bool,
    run_workflow: bool,
    force_live: bool = False,
    quota_snapshot_path: Path | None = None,
    quota_snapshot_max_age_seconds: int = (
        DEFAULT_QUOTA_SNAPSHOT_MAX_AGE_SECONDS
    ),
    operator_tpd_limit: int | None = None,
    operator_tpd_used: int | None = None,
) -> dict[str, Any]:
    """Estimate selected work, read actual TPD headroom, and gate the run."""
    from app.core.config import get_settings

    settings = get_settings()
    live_entries = entries_requiring_live_extraction(
        entries,
        cache=cache,
        resume=resume,
        run_workflow=run_workflow,
        force_live=force_live,
    )
    broker_model = settings.langgraph_broker_model or settings.groq_model
    auditor_model = settings.langgraph_auditor_model or settings.groq_model
    estimate = estimate_selected_scenarios(
        live_entries,
        factory_root=FACTORY_ROOT,
        # Complete scenario-cache hits were already removed from ``live_entries``.
        # They are not proof of a persisted application-level document cache hit:
        # uploads in a new runner process receive new document UUIDs. Keep this
        # explicitly zero until a server endpoint can prove reusable UUID/profile
        # entries before admission.
        persisted_cache_hits=0,
        workflow_enabled=run_workflow,
        live_agents_enabled=settings.langgraph_enable_live_agents,
        extraction_model=settings.groq_model,
        broker_model=broker_model,
        auditor_model=auditor_model,
    )
    report: dict[str, Any] = {
        "estimate": estimate.to_json(),
        "estimate_basis": (
            "planning_estimate_tokens uses one measured 4100-token extraction "
            "per unique document/profile plus one 4100-token call per live "
            "agent narration. admission_estimate_tokens adds a transparent "
            "20% reserve per provider model and is the admission gate. "
            "stress_path_tokens applies 4100 tokens to every reachable strict-"
            "schema and staged-row call only as a stress diagnostic, not a "
            "hard upper bound. SDK retries are disabled. Identical bytes are "
            "deduplicated only within the same extraction profile."
        ),
    }
    if estimate.admission_estimate_tokens == 0:
        report["probe"] = "not required; all selected scenarios are cache hits"
        report["capacity"] = {
            "can_run": True,
            "planning_estimate_tokens": 0,
            "admission_estimate_tokens": 0,
            "stress_path_tokens": 0,
            "shortfall": 0,
        }
        return report

    required_models = set(estimate.admission_estimate_tokens_by_model)
    supplied_cli_value = (
        operator_tpd_limit is not None or operator_tpd_used is not None
    )
    if quota_snapshot_path is not None and supplied_cli_value:
        raise QuotaPreflightError(
            "Use either --groq-tpd-snapshot or CLI Limit/Used, not both."
        )
    if (operator_tpd_limit is None) != (operator_tpd_used is None):
        raise QuotaPreflightError(
            "--groq-tpd-limit and --groq-tpd-used must be supplied together."
        )
    has_operator_quota = (
        quota_snapshot_path is not None
        or (operator_tpd_limit is not None and operator_tpd_used is not None)
    )
    if not has_operator_quota and len(required_models) != 1:
        raise QuotaPreflightError(
            "The oversized TPD diagnostic can observe only one selected model "
            "per preflight. Supply a fresh --groq-tpd-snapshot covering every "
            "selected model; no provider request was sent."
        )
    if settings.groq_api_key is None:
        raise QuotaPreflightError(
            "GROQ_API_KEY is not configured; refusing to start the live evaluation."
        )

    quotas: dict[str, DailyTokenQuota]
    quota_source: str
    try:
        if quota_snapshot_path is not None:
            quotas = load_operator_quota_snapshot(
                quota_snapshot_path,
                required_models=required_models,
                max_age_seconds=quota_snapshot_max_age_seconds,
            )
            quota_source = "operator_console_snapshot"
        elif operator_tpd_limit is not None and operator_tpd_used is not None:
            quotas = operator_cli_quota(
                limit=operator_tpd_limit,
                used=operator_tpd_used,
                required_models=required_models,
            )
            quota_source = "operator_cli_limit_used"
        else:
            # There is exactly one required model here. Groq does not guarantee
            # whether TPD is evaluated before validating the oversized completion
            # request; every non-TPD outcome fails closed inside the reader.
            quota_source = "oversized_diagnostic_tpd_429"
            model = next(iter(required_models))
            quotas = {
                model: read_daily_token_quota(
                    api_key=settings.groq_api_key.get_secret_value(),
                    model=model,
                )
            }
    except QuotaPreflightError as exc:
        raise QuotaPreflightError(
            "Selected-run estimates: "
            f"planning_estimate_tokens={estimate.planning_estimate_tokens}, "
            f"admission_estimate_tokens={estimate.admission_estimate_tokens}, "
            f"stress_path_tokens={estimate.stress_path_tokens}. {exc}"
        ) from exc

    capacities: dict[str, dict[str, int | bool]] = {}
    for model, admission_estimate_tokens in sorted(
        estimate.admission_estimate_tokens_by_model.items()
    ):
        quota = quotas[model]
        capacity = assess_quota_capacity(quota, admission_estimate_tokens)
        capacity_row = capacity.to_json()
        capacity_row["planning_estimate_tokens"] = (
            estimate.planning_estimate_tokens_by_model.get(model, 0)
        )
        capacity_row["stress_path_tokens"] = (
            estimate.stress_path_tokens_by_model.get(model, 0)
        )
        capacities[model] = capacity_row
        if not capacity.can_run:
            raise QuotaPreflightError(
                f"Groq TPD headroom {capacity.headroom} for model {model} is "
                "below the selected-run admission estimate "
                f"{capacity.admission_estimate_tokens} (shortfall "
                f"{capacity.shortfall}); refusing to start."
            )
    report["quota_source"] = quota_source
    report["probe"] = (
        "not sent; exact operator-supplied Limit/Used was used"
        if has_operator_quota
        else "one oversized tiny-prompt request received a parseable TPD 429"
    )
    report["capacity_by_model"] = capacities
    report["capacity"] = {
        "can_run": all(bool(row["can_run"]) for row in capacities.values()),
        "planning_estimate_tokens": estimate.planning_estimate_tokens,
        "admission_estimate_tokens": estimate.admission_estimate_tokens,
        "stress_path_tokens": estimate.stress_path_tokens,
        "shortfall": sum(int(row["shortfall"]) for row in capacities.values()),
    }
    return report


# --------------------------------------------------------------------------- #
# Supporting documents
# --------------------------------------------------------------------------- #
def find_supporting_documents(scenario_id: str) -> dict[str, Path]:
    folder = FACTORY_ROOT / scenario_id / "supporting_documents"
    if not folder.is_dir():
        return {}
    found: dict[str, Path] = {}
    for pdf in sorted(folder.glob("*.pdf")):
        found[pdf.stem] = pdf
    return found


# --------------------------------------------------------------------------- #
# Scenario evaluation
# --------------------------------------------------------------------------- #
def _invoice_paths(scenario_id: str, variant: str) -> tuple[Path, Path]:
    folder = FACTORY_ROOT / scenario_id
    suffix = "text" if variant == "text" else "scanned"
    return (
        folder / f"synthetic_commercial_invoice_{suffix}.pdf",
        folder / f"synthetic_packing_list_{suffix}.pdf",
    )


def compare_extraction(entry: dict[str, Any], multi: dict[str, Any]) -> list[FieldOutcome]:
    invoice = multi.get("invoice") or {}
    packing = multi.get("packing_list") or {}
    outcomes: list[FieldOutcome] = []

    def record(name: str, expected: Any, field_obj: Any) -> None:
        actual = _field_value(field_obj)
        status = (field_obj or {}).get("validation_status") if isinstance(field_obj, dict) else None
        outcomes.append(
            FieldOutcome(
                name=name,
                expected=expected,
                actual=actual,
                correct=values_equal(expected, actual),
                missing=actual is None,
                manual_review=status == "manual_review",
            )
        )

    record("exporter", entry["expected_exporter"], invoice.get("exporter_name"))
    record("buyer", entry["expected_buyer"], invoice.get("buyer_name"))
    record("invoice_number", entry["expected_invoice_number"], invoice.get("invoice_number"))
    record("invoice_date", entry["expected_invoice_date"], invoice.get("invoice_date"))
    record("destination", entry["expected_destination"], invoice.get("destination_country"))
    record("currency", entry["expected_currency"], invoice.get("currency"))
    record("invoice_total", entry["expected_invoice_total"], invoice.get("invoice_total"))
    record(
        "total_net_weight",
        entry["expected_total_net_weight"],
        invoice.get("declared_net_weight_total"),
    )
    record(
        "total_gross_weight",
        entry["expected_total_gross_weight"],
        invoice.get("declared_gross_weight_total"),
    )

    invoice_lines = invoice.get("line_items") or []
    packing_items = packing.get("items") or []
    for index, expected_item in enumerate(entry["expected_items"]):
        actual = invoice_lines[index] if index < len(invoice_lines) else {}
        prefix = f"item{index + 1}"
        record(f"{prefix}.product_name", expected_item["product_name"], actual.get("product_name"))
        record(f"{prefix}.pct_code", expected_item["pct_code"], actual.get("pct_code"))
        record(f"{prefix}.quantity", expected_item["quantity"], actual.get("quantity"))
        record(f"{prefix}.unit", expected_item["unit"], actual.get("unit"))
        record(f"{prefix}.unit_price", expected_item["unit_price"], actual.get("unit_price"))
        record(f"{prefix}.line_total", expected_item["line_total"], actual.get("line_total"))
        # After the documented single-line fallback the line weight must equal
        # the declared document total, so that is the correct expectation.
        expected_net = expected_item["invoice_net_weight"] or entry["expected_total_net_weight"]
        expected_gross = (
            expected_item["invoice_gross_weight"] or entry["expected_total_gross_weight"]
        )
        record(f"{prefix}.net_weight", expected_net, actual.get("net_weight"))
        record(f"{prefix}.gross_weight", expected_gross, actual.get("gross_weight"))

        packing_actual = packing_items[index] if index < len(packing_items) else {}
        record(
            f"{prefix}.packing_quantity",
            expected_item["packing_quantity"],
            packing_actual.get("quantity"),
        )
        record(
            f"{prefix}.packing_net_weight",
            expected_item["packing_net_weight"],
            packing_actual.get("net_weight"),
        )
        record(
            f"{prefix}.packing_gross_weight",
            expected_item["packing_gross_weight"],
            packing_actual.get("gross_weight"),
        )
        record(
            f"{prefix}.package_count",
            expected_item["package_count"],
            packing_actual.get("package_count"),
        )
    return outcomes


def evaluate_scenario(
    api: LiveApiClient,
    entry: dict[str, Any],
    *,
    run_workflow: bool,
    poll_timeout: float,
    cache: EvaluationCache | None = None,
    force_live: bool = False,
) -> ScenarioOutcome:
    """Evaluate one scenario, reusing a cached answer when the inputs match.

    The cache wraps the live call rather than living inside it, so every early
    return in the live path is covered by one store decision instead of six.
    """
    if cache is None or not cache.enabled:
        if entry.get("kind") == "supporting_documents":
            return evaluate_supporting_scenario(
                api,
                entry,
                run_workflow=run_workflow,
                poll_timeout=poll_timeout,
            )
        return _evaluate_scenario_live(
            api,
            entry,
            run_workflow=run_workflow,
            poll_timeout=poll_timeout,
        )

    documents = scenario_document_paths(entry)
    if any(not path.exists() for path in documents):
        if entry.get("kind") == "supporting_documents":
            return evaluate_supporting_scenario(
                api,
                entry,
                run_workflow=run_workflow,
                poll_timeout=poll_timeout,
            )
        return _evaluate_scenario_live(
            api,
            entry,
            run_workflow=run_workflow,
            poll_timeout=poll_timeout,
        )

    key = cache.key(
        scenario_id=entry["scenario_id"],
        variant=entry["variant"],
        document_paths=documents,
        scenario_inputs=entry,
        workflow_profile=workflow_cache_profile(run_workflow=run_workflow),
        workflow_enabled=run_workflow,
    )
    hit = None if force_live else cache.get(key)
    if hit is not None:
        outcome = ScenarioOutcome.from_json(hit["payload"]["outcome"])
        outcome.notes = [*outcome.notes, "served from the evaluation cache"]
        outcome.duration_seconds = 0.0
        return outcome

    if entry.get("kind") == "supporting_documents":
        outcome = evaluate_supporting_scenario(
            api,
            entry,
            run_workflow=run_workflow,
            poll_timeout=poll_timeout,
        )
    else:
        outcome = _evaluate_scenario_live(
            api,
            entry,
            run_workflow=run_workflow,
            poll_timeout=poll_timeout,
        )
    cache.put(
        key,
        {"outcome": outcome.to_json()},
        http_status=outcome.http_status,
        technical_failure=outcome.technical_failure,
        workflow_finished=(not run_workflow) or outcome.workflow_reached_terminal_state,
    )
    return outcome


def scenario_document_paths(entry: dict[str, Any]) -> list[Path]:
    """Every PDF whose bytes can change this scenario's correct answer."""
    if entry.get("kind") == "supporting_documents":
        base = entry["base_scenario_documents"]
        variant = entry["variant"]
        paths = [
            FACTORY_ROOT / base[f"commercial_invoice_{variant}"],
            FACTORY_ROOT / base[f"packing_list_{variant}"],
        ]
        paths.extend(
            FACTORY_ROOT / document["pdf"]
            for document in entry["expected_documents"]
            if document["uploaded"] and document.get("pdf")
        )
        return paths
    invoice_path, packing_path = _invoice_paths(entry["scenario_id"], entry["variant"])
    return [invoice_path, packing_path]


_WORKFLOW_TERMINAL_STATES = {
    "completed",
    "awaiting_human_review",
    "failed",
    "rejected",
}
_WORKFLOW_CORE_EVENTS = (
    "workflow_started",
    "broker_report_created",
    "deterministic_status_frozen",
    "auditor_report_created",
    "consensus_computed",
)
_WORKFLOW_COMPLETION_EVENTS = (
    "final_report_built",
    "workflow_finalized",
)


def _ordered_events_present(
    actual: list[str],
    required: tuple[str, ...],
) -> bool:
    """Return whether every required event appears in the required order."""
    cursor = iter(actual)
    return all(any(event == required_event for event in cursor) for required_event in required)


def _workflow_has_provider_failure(workflow: dict[str, Any]) -> bool:
    for error in workflow.get("errors") or []:
        if not isinstance(error, dict):
            continue
        error_type = str(error.get("type") or "").casefold()
        message = str(error.get("message") or "").casefold()
        if (
            "providerunavailable" in error_type
            or "rate_limit" in message
            or "http_status=429" in message
            or "http_status=503" in message
        ):
            return True
    return False


def _run_and_grade_workflow(
    api: LiveApiClient,
    *,
    request_body: dict[str, Any],
    outcome: ScenarioOutcome,
    raw: dict[str, Any],
    poll_timeout: float,
) -> bool:
    """Run the real LangGraph leg and validate its non-authoritative artifacts.

    This helper never copies a status from an agent or report into
    ``outcome.deterministic_status``. It only proves that every workflow view
    retained the direct Python engine's already-frozen result.
    """
    outcome.workflow_reached_terminal_state = False
    validation: dict[str, Any] = {
        "valid": False,
        "checkpoint_thread_id_present": False,
        "deterministic_status_unchanged": False,
        "broker_findings_present": False,
        "auditor_findings_present": False,
        "consensus_present": False,
        "readable_report_present": False,
        "audit_events_present": False,
        "required_events_present": False,
        "human_review_routing_correct": False,
    }
    outcome.workflow_validation = validation

    try:
        response = api.post(
            "/api/v1/customs-audit/workflows",
            json=request_body,
        )
        raw["workflow_start_status"] = response.status_code
        workflow_payload = response.json()
        raw["workflow_start_response"] = workflow_payload
    except Exception as exc:  # noqa: BLE001
        outcome.technical_failure = f"workflow request error: {exc}"
        return False

    if response.status_code != 200:
        outcome.http_status = response.status_code
        outcome.technical_failure = f"workflow start HTTP {response.status_code}"
        return False
    if not isinstance(workflow_payload, dict):
        outcome.technical_failure = "workflow start returned a non-object response"
        return False

    workflow = workflow_payload
    workflow_id = workflow.get("workflow_id")
    thread_id = workflow.get("thread_id")
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        outcome.technical_failure = "workflow start returned no workflow ID"
        return False
    if not isinstance(thread_id, str) or not thread_id.strip():
        outcome.technical_failure = "workflow start returned no checkpoint thread ID"
        return False
    outcome.workflow_id = workflow_id
    validation["checkpoint_thread_id_present"] = True

    deadline = time.monotonic() + poll_timeout
    workflow_status = workflow.get("status")
    while (
        workflow_status not in _WORKFLOW_TERMINAL_STATES
        and time.monotonic() < deadline
    ):
        time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
        try:
            poll = api.get(f"/api/v1/customs-audit/workflows/{workflow_id}")
            raw["workflow_poll_status"] = poll.status_code
            if poll.status_code != 200:
                outcome.http_status = poll.status_code
                outcome.technical_failure = (
                    f"workflow status HTTP {poll.status_code}"
                )
                return False
            polled_payload = poll.json()
            if not isinstance(polled_payload, dict):
                outcome.technical_failure = (
                    "workflow status returned a non-object response"
                )
                return False
            workflow = polled_payload
            workflow_status = workflow.get("status")
        except Exception as exc:  # noqa: BLE001
            outcome.technical_failure = f"workflow polling error: {exc}"
            return False

    raw["workflow_final"] = workflow
    outcome.workflow_status = (
        str(workflow_status) if workflow_status is not None else None
    )
    outcome.workflow_reached_terminal_state = (
        workflow_status in _WORKFLOW_TERMINAL_STATES
    )
    if not outcome.workflow_reached_terminal_state:
        outcome.technical_failure = "workflow did not reach a terminal state"
        return False

    outcome.human_review_actual = workflow_status == "awaiting_human_review"
    if workflow_status == "failed":
        if _workflow_has_provider_failure(workflow):
            # The workflow endpoint reports its graph result with HTTP 200, but
            # the underlying provider refusal is still an effective 503 and
            # must stop the batch just like the direct extraction route.
            outcome.http_status = 503
            validation["provider_blocked"] = True
        outcome.technical_failure = "workflow ended in technical failure"
        return False
    if workflow_status == "rejected":
        outcome.technical_failure = "workflow was rejected before validation completed"
        return False

    try:
        events_response = api.get(
            f"/api/v1/customs-audit/workflows/{workflow_id}/events"
        )
        raw["workflow_events_status"] = events_response.status_code
        if events_response.status_code != 200:
            outcome.http_status = events_response.status_code
            outcome.technical_failure = (
                f"workflow events HTTP {events_response.status_code}"
            )
            return False
        events_payload = events_response.json()
        raw["workflow_events"] = events_payload
    except Exception as exc:  # noqa: BLE001
        outcome.technical_failure = f"workflow events error: {exc}"
        return False

    events = (
        events_payload.get("events")
        if isinstance(events_payload, dict)
        else None
    )
    event_types = [
        str(event.get("event_type"))
        for event in events or []
        if isinstance(event, dict)
    ]
    validation["audit_events_present"] = bool(event_types)
    required_events: tuple[str, ...] = _WORKFLOW_CORE_EVENTS
    if workflow_status == "completed":
        required_events += _WORKFLOW_COMPLETION_EVENTS
    validation["required_events_present"] = _ordered_events_present(
        event_types,
        required_events,
    )

    review_payload: Any = None
    try:
        review = api.get(f"/api/v1/customs-audit/workflows/{workflow_id}/review")
        raw["human_review_status"] = review.status_code
        if review.status_code == 200:
            review_payload = review.json()
            raw["human_review_request"] = review_payload
        elif review.status_code != 404:
            outcome.http_status = review.status_code
            outcome.technical_failure = (
                f"human-review request HTTP {review.status_code}"
            )
            return False
    except Exception as exc:  # noqa: BLE001
        outcome.technical_failure = f"human-review request error: {exc}"
        return False

    validation["human_review_routing_correct"] = (
        outcome.human_review_actual == outcome.human_review_expected
        and (
            (outcome.human_review_actual and isinstance(review_payload, dict))
            or (not outcome.human_review_actual and review_payload is None)
        )
    )

    final_report = workflow.get("final_report")
    if not isinstance(final_report, dict):
        final_report = {}
    broker = final_report.get("broker_findings")
    auditor = final_report.get("auditor_findings")
    consensus = final_report.get("consensus_result")
    user_report = final_report.get("user_report")
    validation["broker_findings_present"] = isinstance(broker, dict) and bool(
        broker
    )
    validation["auditor_findings_present"] = isinstance(auditor, dict) and bool(
        auditor
    )
    validation["consensus_present"] = isinstance(consensus, dict) and bool(
        consensus
    )
    validation["readable_report_present"] = isinstance(
        user_report, dict
    ) and bool(user_report)

    direct_status = outcome.deterministic_status
    status_views = {
        "workflow": workflow.get("deterministic_status"),
        "report": final_report.get("deterministic_compliance_status"),
        "original_report": final_report.get("original_deterministic_status"),
        "broker": broker.get("observed_deterministic_status")
        if isinstance(broker, dict)
        else None,
        "auditor": auditor.get("observed_deterministic_status")
        if isinstance(auditor, dict)
        else None,
        "consensus": consensus.get("deterministic_status")
        if isinstance(consensus, dict)
        else None,
    }
    validation["deterministic_status_views"] = status_views
    validation["deterministic_status_unchanged"] = (
        direct_status is not None
        and all(status == direct_status for status in status_views.values())
    )

    required_flags = (
        "checkpoint_thread_id_present",
        "deterministic_status_unchanged",
        "broker_findings_present",
        "auditor_findings_present",
        "consensus_present",
        "readable_report_present",
        "audit_events_present",
        "required_events_present",
        "human_review_routing_correct",
    )
    validation["valid"] = all(validation[name] is True for name in required_flags)
    if not validation["deterministic_status_unchanged"]:
        outcome.notes.append(
            "workflow deterministic status views did not preserve the direct "
            f"deterministic status {direct_status}"
        )
    for name in required_flags:
        if validation[name] is not True and name != "deterministic_status_unchanged":
            outcome.notes.append(f"workflow validation failed: {name}")
    return bool(validation["valid"])


def _evaluate_scenario_live(
    api: LiveApiClient,
    entry: dict[str, Any],
    *,
    run_workflow: bool,
    poll_timeout: float,
) -> ScenarioOutcome:
    scenario_id, variant = entry["scenario_id"], entry["variant"]
    outcome = ScenarioOutcome(
        scenario_id=scenario_id,
        variant=variant,
        expected_status=entry["expected_primary_status"],
        line_item_count_expected=entry["expected_line_item_count"],
        fallback_expected=entry["expected_single_line_fallback_runs"],
        human_review_expected=entry["expected_human_review"],
    )
    started = time.monotonic()
    raw: dict[str, Any] = {"scenario_id": scenario_id, "variant": variant}

    invoice_path, packing_path = _invoice_paths(scenario_id, variant)
    if not invoice_path.exists() or not packing_path.exists():
        outcome.technical_failure = f"missing PDF: {invoice_path.name}/{packing_path.name}"
        outcome.duration_seconds = time.monotonic() - started
        return outcome

    try:
        invoice_id = api.upload(invoice_path)
        packing_id = api.upload(packing_path)
        raw["invoice_document_id"] = invoice_id
        raw["packing_document_id"] = packing_id
    except Exception as exc:  # noqa: BLE001
        outcome.technical_failure = f"upload failed: {exc}"
        outcome.duration_seconds = time.monotonic() - started
        return outcome

    supporting = find_supporting_documents(scenario_id)
    supporting_ids: list[str] = []
    for _, pdf_path in supporting.items():
        try:
            supporting_ids.append(api.upload(pdf_path))
        except Exception as exc:  # noqa: BLE001
            outcome.notes.append(f"supporting upload failed for {pdf_path.name}: {exc}")
    raw["supporting_document_ids"] = supporting_ids

    request_body = {
        "commercial_invoice_document_id": invoice_id,
        "packing_list_document_id": packing_id,
        "shipment_date": entry["shipment_date"],
        "letter_of_credit_date": entry["letter_of_credit_date"],
        "additional_uploaded_document_types": entry["expected_supplied_document_types"],
    }

    # ---- field-level extraction + deterministic compliance ------------------ #
    try:
        response = api.post("/api/v1/compliance/check-documents/multi-line", json=request_body)
        raw["multi_line_http_status"] = response.status_code
        raw["multi_line_response"] = response.json()
        outcome.http_status = response.status_code
    except Exception as exc:  # noqa: BLE001
        outcome.technical_failure = f"multi-line request error: {exc}"
        outcome.duration_seconds = time.monotonic() - started
        _write_raw(raw)
        return outcome

    if response.status_code != 200:
        detail = raw["multi_line_response"]
        outcome.technical_failure = (
            f"multi-line HTTP {response.status_code}: "
            f"{detail.get('detail') if isinstance(detail, dict) else detail}"
        )
        outcome.duration_seconds = time.monotonic() - started
        _write_raw(raw)
        return outcome

    multi = raw["multi_line_response"]
    outcome.fields = compare_extraction(entry, multi)
    invoice_lines = (multi.get("invoice") or {}).get("line_items") or []
    outcome.line_item_count_actual = len(invoice_lines)
    outcome.deterministic_status = multi.get("overall_status")

    # OCR observations from page reviews.
    page_reviews = multi.get("page_reviews") or []
    outcome.ocr = {
        "pages_total": len(page_reviews),
        "pages_requiring_ocr": sum(1 for p in page_reviews if p.get("requires_ocr")),
        "ocr_attempted": sum(1 for p in page_reviews if p.get("ocr_attempted")),
        "ocr_completed": sum(
            1
            for p in page_reviews
            if p.get("ocr_attempted") and (p.get("ocr_confidence") is not None)
        ),
        "low_confidence_pages": sum(
            1
            for p in page_reviews
            if (p.get("ocr_confidence") is not None)
            and Decimal(str(p["ocr_confidence"])) < Decimal("0.80")
        ),
    }

    # Fallback detection: a derived line weight carries derivation_method.
    for line in invoice_lines:
        for weight_field in ("net_weight", "gross_weight"):
            value = line.get(weight_field)
            if isinstance(value, dict) and value.get("derivation_method") == "single_line_declared_total":
                outcome.fallback_ran = True

    # Matching + checks.
    items = multi.get("items") or []
    outcome.matching = {
        "items_returned": len(items),
        "matched": sum(1 for i in items if i.get("match_status") == "matched"),
        "invoice_only": sum(1 for i in items if i.get("match_status") == "invoice_only"),
        "packing_only": sum(1 for i in items if i.get("match_status") == "packing_only"),
        "strategies": [i.get("match_strategy") for i in items],
        "order_preserved": [i.get("invoice_item_index") for i in items]
        == sorted(
            [i.get("invoice_item_index") for i in items if i.get("invoice_item_index")]
        )
        + [i.get("invoice_item_index") for i in items if not i.get("invoice_item_index")],
    }

    failed_checks: list[str] = []
    manual_checks: list[str] = []
    passed_checks: list[str] = []
    for check in multi.get("shipment_level_checks") or []:
        bucket = {
            "failed": failed_checks,
            "manual_review": manual_checks,
            "passed": passed_checks,
        }.get(check.get("status"))
        if bucket is not None:
            bucket.append(check.get("check_id"))
    for item in items:
        for check in item.get("item_checks") or []:
            bucket = {
                "failed": failed_checks,
                "manual_review": manual_checks,
                "passed": passed_checks,
            }.get(check.get("status"))
            if bucket is not None:
                bucket.append(check.get("check_id"))
        compliance = item.get("compliance") or {}
        for key in ("checks", "executable_rule_checks"):
            for check in compliance.get(key) or []:
                bucket = {
                    "failed": failed_checks,
                    "manual_review": manual_checks,
                    "passed": passed_checks,
                }.get(check.get("status"))
                if bucket is not None:
                    bucket.append(check.get("check_id"))

    expected_failed = set(entry["expected_failed_checks"])
    outcome.checks = {
        "failed": sorted(set(failed_checks)),
        "manual_review": sorted(set(manual_checks)),
        "passed_count": len(set(passed_checks)),
        "expected_failed": sorted(expected_failed),
        "expected_failed_detected": sorted(expected_failed & set(failed_checks)),
        "unexpected_failed": sorted(set(failed_checks) - expected_failed),
        "missed_failed": sorted(expected_failed - set(failed_checks)),
    }

    # ---- LangGraph workflow ------------------------------------------------- #
    workflow_valid = True
    if run_workflow:
        workflow_valid = _run_and_grade_workflow(
            api,
            request_body={
                "commercial_invoice_document_id": invoice_id,
                "packing_list_document_id": packing_id,
                "additional_document_ids": supporting_ids,
                "shipment_date": entry["shipment_date"],
                "letter_of_credit_date": entry["letter_of_credit_date"],
                "additional_uploaded_document_types": entry[
                    "expected_supplied_document_types"
                ],
                "supporting_documents": [],
            },
            outcome=outcome,
            raw=raw,
            poll_timeout=poll_timeout,
        )

    # ---- Status grading ----------------------------------------------------- #
    actual_status = outcome.deterministic_status
    expected_status = entry["expected_primary_status"]
    outcome.status_correct = actual_status == expected_status
    if actual_status == "passed" and expected_status in {"failed", "manual_review"}:
        outcome.false_pass = True
    if actual_status == "failed" and expected_status == "passed":
        outcome.false_failure = True
    if actual_status == "manual_review" and expected_status == "passed":
        outcome.false_manual_review = True

    outcome.ok = (
        outcome.technical_failure is None
        and outcome.status_correct
        and not outcome.checks["missed_failed"]
        and not outcome.checks["unexpected_failed"]
        and outcome.line_item_count_actual == outcome.line_item_count_expected
        and all(f.correct for f in outcome.fields)
        and outcome.fallback_ran == outcome.fallback_expected
        and workflow_valid
    )
    outcome.duration_seconds = time.monotonic() - started
    raw["outcome"] = outcome.to_json()
    _write_raw(raw)
    return outcome


def evaluate_supporting_scenario(
    api: LiveApiClient,
    entry: dict[str, Any],
    *,
    run_workflow: bool,
    poll_timeout: float,
) -> ScenarioOutcome:
    """Upload real supporting-document PDFs and grade the verification result.

    This is the path that proves the headline claim: a document type only counts
    when its *UUID* was uploaded and the file behind it actually read as that
    document. Claimed-only types are deliberately sent as bare strings in
    ``additional_uploaded_document_types`` so the run can show they earn nothing.
    """
    scenario_id, variant = entry["scenario_id"], entry["variant"]
    outcome = ScenarioOutcome(
        scenario_id=scenario_id,
        variant=variant,
        expected_status=entry["expected_primary_status"],
        human_review_expected=entry["expected_human_review"],
    )
    started = time.monotonic()
    raw: dict[str, Any] = {
        "scenario_id": scenario_id,
        "variant": variant,
        "kind": "supporting_documents",
    }

    base = entry["base_scenario_documents"]
    invoice_path = FACTORY_ROOT / base[f"commercial_invoice_{variant}"]
    packing_path = FACTORY_ROOT / base[f"packing_list_{variant}"]
    missing = [p for p in (invoice_path, packing_path) if not p.exists()]
    if missing:
        outcome.technical_failure = f"missing base PDF: {[p.name for p in missing]}"
        outcome.duration_seconds = time.monotonic() - started
        return outcome

    try:
        invoice_id = api.upload(invoice_path)
        packing_id = api.upload(packing_path)
    except Exception as exc:  # noqa: BLE001
        outcome.technical_failure = f"upload failed: {exc}"
        outcome.duration_seconds = time.monotonic() - started
        return outcome
    raw["invoice_document_id"] = invoice_id
    raw["packing_document_id"] = packing_id

    supporting_refs: list[dict[str, str]] = []
    uploaded_by_type: dict[str, str] = {}
    for document in entry["expected_documents"]:
        if not document["uploaded"] or not document.get("pdf"):
            continue
        pdf_path = FACTORY_ROOT / document["pdf"]
        if not pdf_path.exists():
            outcome.notes.append(f"missing supporting PDF {document['pdf']}")
            continue
        try:
            document_id = api.upload(pdf_path)
        except Exception as exc:  # noqa: BLE001
            outcome.notes.append(f"supporting upload failed for {pdf_path.name}: {exc}")
            continue
        supporting_refs.append(
            {
                "document_type": document["claimed_document_type"],
                "document_id": document_id,
            }
        )
        uploaded_by_type[document["claimed_document_type"]] = document_id
    raw["supporting_documents_sent"] = supporting_refs

    request_body = {
        "commercial_invoice_document_id": invoice_id,
        "packing_list_document_id": packing_id,
        "supporting_documents": supporting_refs,
        "additional_uploaded_document_types": entry["claimed_only_document_types"],
        "shipment_date": entry["shipment"]["shipment_date"],
    }
    raw["request_body"] = request_body

    try:
        response = api.post(
            "/api/v1/compliance/check-documents/multi-line", json=request_body
        )
        outcome.http_status = response.status_code
        raw["multi_line_http_status"] = response.status_code
        raw["multi_line_response"] = response.json()
    except Exception as exc:  # noqa: BLE001
        outcome.technical_failure = f"multi-line request error: {exc}"
        outcome.duration_seconds = time.monotonic() - started
        _write_raw(raw)
        return outcome

    if response.status_code != 200:
        detail = raw["multi_line_response"]
        outcome.technical_failure = (
            f"multi-line HTTP {response.status_code}: "
            f"{detail.get('detail') if isinstance(detail, dict) else detail}"
        )
        outcome.duration_seconds = time.monotonic() - started
        _write_raw(raw)
        return outcome

    multi = raw["multi_line_response"]
    outcome.deterministic_status = multi.get("overall_status")
    actual_documents = multi.get("supporting_documents") or []
    # Match on the canonical type, not the raw string. A claimed-only entry
    # comes back under whatever alias the caller typed ("form_e"), while the
    # manifest names the canonical type, so a raw-string lookup silently
    # reported "no result returned" for exactly the documents this run exists
    # to check.
    by_claimed = {
        row.get("canonical_document_type") or row.get("claimed_document_type"): row
        for row in actual_documents
    }

    graded: list[dict[str, Any]] = []
    for expected in entry["expected_documents"]:
        claimed = expected["claimed_document_type"]
        actual = by_claimed.get(claimed)
        row: dict[str, Any] = {
            "claimed_document_type": claimed,
            "uploaded_expected": expected["uploaded"],
            "document_id_sent": uploaded_by_type.get(claimed),
            "present_in_response": actual is not None,
        }
        if actual is None:
            row["errors"] = ["no verification result returned for this document"]
            graded.append(row)
            continue

        errors: list[str] = []
        row["uploaded_actual"] = actual.get("uploaded")
        row["document_id_processed"] = actual.get("document_id")
        row["detected_type_actual"] = actual.get("detected_document_type")
        row["state_actual"] = actual.get("state")
        row["content_status_actual"] = actual.get("content_status")
        row["content_status_expected"] = expected["expected_content_status"]
        row["authenticity_actual"] = actual.get("authenticity_status")
        row["extraction_confidence"] = actual.get("extraction_confidence")
        row["ocr_confidence"] = actual.get("ocr_confidence")
        row["source_page"] = actual.get("source_page")

        if actual.get("uploaded") is not expected["uploaded"]:
            errors.append(
                f"uploaded={actual.get('uploaded')} but expected "
                f"{expected['uploaded']}"
            )
        # The UUID actually processed must be the UUID we uploaded.
        if expected["uploaded"]:
            sent = uploaded_by_type.get(claimed)
            if sent and str(actual.get("document_id")) != str(sent):
                errors.append(
                    f"processed document_id {actual.get('document_id')} is not "
                    f"the uploaded {sent}"
                )
        if actual.get("content_status") != expected["expected_content_status"]:
            errors.append(
                f"content_status {actual.get('content_status')} != expected "
                f"{expected['expected_content_status']}"
            )
        if expected["expected_state"] is not None and actual.get("state") != expected[
            "expected_state"
        ]:
            errors.append(
                f"state {actual.get('state')} != expected {expected['expected_state']}"
            )
        if actual.get("authenticity_status") != "not_externally_verified":
            errors.append(
                "authenticity_status must always be not_externally_verified, got "
                f"{actual.get('authenticity_status')}"
            )

        expected_detected = expected.get("expected_detected_type")
        detected_raw = actual.get("detected_document_type")
        if detected_raw is None:
            detected_canonical = None
        else:
            resolved_detected = canonical_supporting_type(str(detected_raw))
            detected_canonical = (
                None
                if resolved_detected is SupportingDocumentType.UNKNOWN
                else resolved_detected.value
            )
        row["detected_type_canonical_actual"] = detected_canonical
        row["detected_type_expected"] = expected_detected
        if detected_canonical != expected_detected:
            errors.append(
                f"detected document type {detected_canonical} != expected "
                f"{expected_detected}"
            )

        expected_action = expected.get("expected_required_action")
        row["required_action_actual"] = actual.get("required_action")
        row["required_action_expected"] = expected_action
        if actual.get("required_action") != expected_action:
            errors.append(
                f"required_action {actual.get('required_action')} != expected "
                f"{expected_action}"
            )

        counts_as_present = bool(
            actual.get("uploaded")
            and actual.get("content_status") != "failed"
            and actual.get("state")
            in {"type_verified", "fields_verified", "shipment_matched"}
        )
        row["counts_as_present_actual"] = counts_as_present
        row["counts_as_present_expected"] = expected["expected_counts_as_present"]
        if counts_as_present is not expected["expected_counts_as_present"]:
            errors.append(
                f"counts_as_present={counts_as_present} but expected "
                f"{expected['expected_counts_as_present']}"
            )

        extraction = actual.get("extraction")
        extraction_fields = extraction if isinstance(extraction, dict) else {}
        field_grades: list[dict[str, Any]] = []
        for field_name, expected_value in expected["expected_fields"].items():
            actual_value = _field_value(extraction_fields.get(field_name))
            correct = values_equal(expected_value, actual_value)
            field_grades.append(
                {
                    "name": field_name,
                    "expected": expected_value,
                    "actual": actual_value,
                    "correct": correct,
                }
            )
            if not correct:
                errors.append(
                    f"{field_name}={actual_value!r} but expected "
                    f"{expected_value!r}"
                )
        row["fields"] = field_grades

        actual_checks = {
            check.get("check_id"): check.get("status")
            for check in actual.get("checks") or []
        }
        row["checks"] = actual_checks
        for check_id in expected["expected_failed_checks"]:
            if actual_checks.get(check_id) != "failed":
                errors.append(
                    f"expected {check_id} to fail, got {actual_checks.get(check_id)}"
                )
        for check_id in expected["expected_manual_review_checks"]:
            if actual_checks.get(check_id) != "manual_review":
                errors.append(
                    f"expected {check_id} to need manual review, got "
                    f"{actual_checks.get(check_id)}"
                )
        row["errors"] = errors
        graded.append(row)

    # A claimed-only type must never appear as a verified, present document.
    claimed_only_leaks = [
        row.get("claimed_document_type")
        for row in actual_documents
        if not row.get("uploaded") and row.get("content_status") == "passed"
    ]
    if claimed_only_leaks:
        graded.append(
            {
                "claimed_document_type": "<claimed-only leak>",
                "errors": [
                    "claimed-only document types were treated as satisfied: "
                    + ", ".join(str(t) for t in claimed_only_leaks)
                ],
            }
        )

    outcome.supporting_documents = {
        "documents": graded,
        "claimed_only_types_sent": entry["claimed_only_document_types"],
        "claimed_only_satisfied_a_requirement": bool(claimed_only_leaks),
    }
    outcome.status_correct = outcome.deterministic_status == outcome.expected_status
    outcome.false_pass = (
        outcome.deterministic_status == "passed"
        and outcome.expected_status != "passed"
    )
    outcome.false_failure = (
        outcome.deterministic_status == "failed"
        and outcome.expected_status == "passed"
    )
    outcome.false_manual_review = (
        outcome.deterministic_status == "manual_review"
        and outcome.expected_status == "passed"
    )
    document_errors = [error for row in graded for error in row.get("errors", [])]
    outcome.notes.extend(document_errors)
    workflow_valid = True
    if run_workflow:
        workflow_valid = _run_and_grade_workflow(
            api,
            request_body={
                **request_body,
                "additional_document_ids": [
                    reference["document_id"] for reference in supporting_refs
                ],
                "letter_of_credit_date": entry["shipment"].get(
                    "letter_of_credit_date"
                ),
            },
            outcome=outcome,
            raw=raw,
            poll_timeout=poll_timeout,
        )
    outcome.ok = (
        outcome.status_correct
        and not document_errors
        and outcome.technical_failure is None
        and workflow_valid
    )
    outcome.duration_seconds = time.monotonic() - started
    raw["outcome"] = outcome.to_json()
    _write_raw(raw)
    return outcome


def _write_raw(raw: dict[str, Any]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{raw['scenario_id']}__{raw['variant']}.json"
    path.write_text(json.dumps(raw, indent=2, default=str, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
_FIELD_GROUPS = {
    "exporter": ["exporter"],
    "buyer": ["buyer"],
    "invoice_number": ["invoice_number"],
    "invoice_date": ["invoice_date"],
    "destination": ["destination"],
    "currency": ["currency"],
    "invoice_total": ["invoice_total"],
    "total_net_weight": ["total_net_weight"],
    "total_gross_weight": ["total_gross_weight"],
}
_ITEM_SUFFIXES = {
    "product_name": "product_name",
    "pct_code": "pct_code",
    "quantity": "quantity",
    "unit": "unit",
    "unit_price": "unit_price",
    "line_total": "line_total",
    "item_net_weight": "net_weight",
    "item_gross_weight": "gross_weight",
    "package_count": "package_count",
}


def compute_metrics(outcomes: list[ScenarioOutcome]) -> dict[str, Any]:
    def pct(numerator: int, denominator: int) -> float | None:
        return round(100.0 * numerator / denominator, 2) if denominator else None

    metrics: dict[str, Any] = {}
    usable = [o for o in outcomes if o.technical_failure is None]

    # Field accuracy
    field_metrics: dict[str, Any] = {}
    for label, names in _FIELD_GROUPS.items():
        total = correct = 0
        for outcome in usable:
            for f in outcome.fields:
                if f.name in names:
                    total += 1
                    correct += int(f.correct)
        field_metrics[f"{label}_accuracy"] = pct(correct, total)
    for label, suffix in _ITEM_SUFFIXES.items():
        total = correct = 0
        for outcome in usable:
            for f in outcome.fields:
                if f.name.endswith("." + suffix):
                    total += 1
                    correct += int(f.correct)
        field_metrics[f"{label}_accuracy"] = pct(correct, total)

    all_fields = [f for o in usable for f in o.fields]
    field_metrics["exact_field_accuracy"] = pct(
        sum(1 for f in all_fields if f.correct), len(all_fields)
    )
    field_metrics["missing_field_rate"] = pct(
        sum(1 for f in all_fields if f.missing), len(all_fields)
    )
    field_metrics["incorrect_field_rate"] = pct(
        sum(1 for f in all_fields if not f.correct and not f.missing), len(all_fields)
    )
    field_metrics["manual_review_field_rate"] = pct(
        sum(1 for f in all_fields if f.manual_review), len(all_fields)
    )
    field_metrics["line_item_count_accuracy"] = pct(
        sum(1 for o in usable if o.line_item_count_actual == o.line_item_count_expected),
        len(usable),
    )
    metrics["field_extraction"] = field_metrics

    # Document processing
    metrics["document_processing"] = {
        "pages_total": sum(o.ocr.get("pages_total", 0) for o in usable),
        "pages_requiring_ocr": sum(o.ocr.get("pages_requiring_ocr", 0) for o in usable),
        "ocr_attempted": sum(o.ocr.get("ocr_attempted", 0) for o in usable),
        "ocr_completed": sum(o.ocr.get("ocr_completed", 0) for o in usable),
        "low_confidence_pages": sum(o.ocr.get("low_confidence_pages", 0) for o in usable),
    }

    # Matching
    metrics["item_matching"] = {
        "scenarios_all_items_matched": sum(
            1
            for o in usable
            if o.matching.get("matched") == o.line_item_count_expected
        ),
        "scenarios_evaluated": len(usable),
        "match_accuracy": pct(
            sum(
                1
                for o in usable
                if o.matching.get("matched") == o.line_item_count_expected
            ),
            len(usable),
        ),
        "line_reference_matches": sum(
            sum(1 for s in o.matching.get("strategies", []) if s == "line_reference")
            for o in usable
        ),
        "pct_code_matches": sum(
            sum(1 for s in o.matching.get("strategies", []) if s == "pct_code")
            for o in usable
        ),
        "product_name_matches": sum(
            sum(1 for s in o.matching.get("strategies", []) if s == "product_name")
            for o in usable
        ),
    }

    # Compliance
    metrics["compliance"] = {
        "overall_status_accuracy": pct(
            sum(1 for o in usable if o.status_correct), len(usable)
        ),
        "expected_failed_checks_detected": pct(
            sum(len(o.checks.get("expected_failed_detected", [])) for o in usable),
            sum(len(o.checks.get("expected_failed", [])) for o in usable),
        ),
        "unexpected_failed_checks": sum(
            len(o.checks.get("unexpected_failed", [])) for o in usable
        ),
        "missed_failed_checks": sum(len(o.checks.get("missed_failed", [])) for o in usable),
        "false_pass_count": sum(1 for o in usable if o.false_pass),
        "false_failure_count": sum(1 for o in usable if o.false_failure),
        "false_manual_review_count": sum(1 for o in usable if o.false_manual_review),
        "fallback_behaviour_correct": pct(
            sum(1 for o in usable if o.fallback_ran == o.fallback_expected), len(usable)
        ),
    }

    # Workflow
    durations = [o.duration_seconds for o in outcomes if o.duration_seconds]
    metrics["workflow"] = {
        "attempted": len(outcomes),
        "technical_failures": sum(1 for o in outcomes if o.technical_failure),
        "completed": sum(1 for o in outcomes if o.workflow_status == "completed"),
        "awaiting_human_review": sum(
            1 for o in outcomes if o.workflow_status == "awaiting_human_review"
        ),
        "workflow_failed": sum(1 for o in outcomes if o.workflow_status == "failed"),
        "human_review_accuracy": pct(
            sum(
                1
                for o in outcomes
                if o.workflow_status is not None
                and o.human_review_actual == o.human_review_expected
            ),
            sum(1 for o in outcomes if o.workflow_status is not None),
        ),
        "average_duration_seconds": round(statistics.mean(durations), 2) if durations else None,
        "median_duration_seconds": round(statistics.median(durations), 2) if durations else None,
    }

    metrics["scenarios_fully_correct"] = sum(1 for o in outcomes if o.ok)
    metrics["scenarios_attempted"] = len(outcomes)
    return metrics


def render_markdown(payload: dict[str, Any], title: str) -> str:
    lines = [f"# {title}", "", f"API: `{payload['api_url']}`", ""]
    lines.append("## Live dependency preflight")
    lines.append("")
    for key, value in payload["preflight"].items():
        lines.append(f"- **{key}**: `{value}`")
    lines.append("")

    for variant in ("text", "scanned"):
        variant_metrics = payload["metrics_by_variant"].get(variant)
        if not variant_metrics:
            continue
        lines.append(f"## {variant.capitalize()} PDFs")
        lines.append("")
        lines.append(
            f"Scenarios fully correct: **{variant_metrics['scenarios_fully_correct']}"
            f"/{variant_metrics['scenarios_attempted']}**"
        )
        lines.append("")
        for section in ("field_extraction", "item_matching", "compliance", "workflow", "document_processing"):
            lines.append(f"### {section.replace('_', ' ').title()}")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("| --- | ---: |")
            for key, value in variant_metrics[section].items():
                lines.append(f"| {key} | {value} |")
            lines.append("")

    lines.append("## Per-scenario results")
    lines.append("")
    lines.append("| Scenario | Variant | OK | Expected | Actual | Lines exp/act | Notes |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in payload["scenarios"]:
        note = row["technical_failure"] or ", ".join(row["notes"]) or ""
        if row["checks"].get("missed_failed"):
            note = f"missed: {row['checks']['missed_failed']} {note}"
        if row["checks"].get("unexpected_failed"):
            note = f"unexpected: {row['checks']['unexpected_failed']} {note}"
        bad_fields = [f["name"] for f in row["fields"] if not f["correct"]]
        if bad_fields:
            note = f"bad fields: {bad_fields[:6]} {note}"
        lines.append(
            f"| {row['scenario_id']} | {row['variant']} | "
            f"{'yes' if row['ok'] else 'NO'} | {row['expected_status']} | "
            f"{row['deterministic_status']} | "
            f"{row['line_item_count_expected']}/{row['line_item_count_actual']} | "
            f"{note[:120]} |"
        )
    lines.append("")
    critical = [r for r in payload["scenarios"] if r["false_pass"]]
    lines.append("## Critical: false legal passes")
    lines.append("")
    if critical:
        for row in critical:
            lines.append(
                f"- **severity = critical** — `{row['scenario_id']}` ({row['variant']}): "
                f"expected `{row['expected_status']}`, returned `passed`."
            )
    else:
        lines.append("None. No scenario produced a legal pass it did not deserve.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API)
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--variant", choices=["text", "scanned", "both"], default="both")
    parser.add_argument("--resume-failed", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse cached answers; only run scenarios whose inputs changed.",
    )
    parser.add_argument(
        "--retry-blocked",
        action="store_true",
        help="Re-run only entries a previous run could not complete (429/502/"
        "503/technical failure). Those are never cached, so they always re-run "
        "live.",
    )
    parser.add_argument(
        "--include-supporting-documents",
        action="store_true",
        help="Also evaluate the supporting-document bundles through real uploads.",
    )
    parser.add_argument(
        "--supporting-documents-only",
        action="store_true",
        help="Evaluate only the supporting-document bundles.",
    )
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument(
        "--groq-tpd-snapshot",
        type=Path,
        default=None,
        help="Fresh JSON transcription of Groq console Limit/Used values. "
        "This bypasses the default oversized diagnostic and is required when "
        "the selected workflow uses multiple Groq models.",
    )
    parser.add_argument(
        "--groq-tpd-snapshot-max-age-seconds",
        type=int,
        default=DEFAULT_QUOTA_SNAPSHOT_MAX_AGE_SECONDS,
        help="Maximum accepted age of --groq-tpd-snapshot (default: 300).",
    )
    parser.add_argument(
        "--groq-tpd-limit",
        type=int,
        default=None,
        help="Operator-read Groq console TPD Limit for a single selected model; "
        "must be paired with --groq-tpd-used.",
    )
    parser.add_argument(
        "--groq-tpd-used",
        type=int,
        default=None,
        help="Operator-read Groq console TPD Used for a single selected model; "
        "must be paired with --groq-tpd-limit.",
    )
    parser.add_argument(
        "--pace-seconds",
        type=float,
        default=0.0,
        help="Sleep between scenarios to respect per-minute limits only. The "
        "daily-token preflight independently refuses runs that exceed TPD "
        "headroom; pacing cannot create daily capacity.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore and do not write the evaluation cache.",
    )
    parser.add_argument("--live-models-required", action="store_true")
    parser.add_argument("--no-workflow", action="store_true")
    parser.add_argument("--poll-timeout", type=float, default=180.0)
    parser.add_argument("--output-prefix", default="synthetic_factory_baseline")
    parser.add_argument("--title", default="Synthetic Factory Live Evaluation")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    supporting_entries = manifest.get("supporting_document_scenarios", [])
    if args.supporting_documents_only:
        entries = list(supporting_entries)
    elif args.include_supporting_documents:
        entries = [*manifest["scenarios"], *supporting_entries]
    else:
        entries = list(manifest["scenarios"])
    if args.variant != "both":
        entries = [e for e in entries if e["variant"] == args.variant]
    if args.only:
        wanted = set(args.only)
        entries = [e for e in entries if e["scenario_id"] in wanted]
    if args.resume_failed:
        previous_path = REPORTS_DIR / f"{args.output_prefix}.json"
        if previous_path.exists():
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            failed = {
                (row["scenario_id"], row["variant"])
                for row in previous.get("scenarios", [])
                if not row.get("ok")
            }
            entries = [e for e in entries if (e["scenario_id"], e["variant"]) in failed]
            print(f"--resume-failed: {len(entries)} previously-failing entries")
    if args.retry_blocked:
        previous_path = REPORTS_DIR / f"{args.output_prefix}.json"
        if previous_path.exists():
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            blocked = {
                (row["scenario_id"], row["variant"])
                for row in previous.get("scenarios", [])
                if row.get("technical_failure")
            }
            entries = [e for e in entries if (e["scenario_id"], e["variant"]) in blocked]
            print(
                f"--retry-blocked: {len(entries)} entries that could not complete "
                "last time (these are never cached, so they run live)"
            )
        else:
            print("--retry-blocked: no previous run found; nothing to retry.")
            entries = []

    if not entries:
        print("No scenarios selected.")
        return 0

    cache = EvaluationCache(
        Path(args.cache_dir) if args.cache_dir else None,
        enabled=not args.no_cache,
    )
    if args.resume and not args.no_cache:
        print(f"Cache: {cache.directory} (fingerprint {cache.fingerprint.code_version})")

    need_ocr = any(e["variant"] == "scanned" for e in entries)
    api = LiveApiClient(args.api_url)
    stopped_early_status: int | None = None
    try:
        try:
            checks = preflight(
                api,
                need_ocr=need_ocr,
                need_workflow=not args.no_workflow,
                strict=args.live_models_required,
            )
            checks["groq_daily_quota"] = quota_preflight(
                entries,
                cache=cache,
                resume=args.resume and not args.no_cache,
                run_workflow=not args.no_workflow,
                force_live=args.retry_blocked,
                quota_snapshot_path=args.groq_tpd_snapshot,
                quota_snapshot_max_age_seconds=(
                    args.groq_tpd_snapshot_max_age_seconds
                ),
                operator_tpd_limit=args.groq_tpd_limit,
                operator_tpd_used=args.groq_tpd_used,
            )
        except (EvaluationError, QuotaPreflightError) as exc:
            print(f"Live evaluation preflight refused: {exc}", file=sys.stderr)
            return 2
        print("Live dependency preflight:")
        for key, value in checks.items():
            print(f"  {key}: {value}")
        print()

        outcomes: list[ScenarioOutcome] = []
        for index, entry in enumerate(entries, start=1):
            label = f"{entry['scenario_id']} [{entry['variant']}]"
            print(f"[{index}/{len(entries)}] {label} ...", flush=True)
            outcome = evaluate_scenario(
                api,
                entry,
                run_workflow=not args.no_workflow,
                poll_timeout=args.poll_timeout,
                cache=cache if args.resume else None,
                force_live=args.retry_blocked,
            )
            outcomes.append(outcome)
            verdict = "OK" if outcome.ok else (outcome.technical_failure or "MISMATCH")
            print(
                f"      -> {verdict} | status={outcome.deterministic_status} "
                f"(expected {outcome.expected_status}) | "
                f"lines={outcome.line_item_count_actual}/{outcome.line_item_count_expected} "
                f"| {outcome.duration_seconds:.1f}s",
                flush=True,
            )
            if outcome.http_status in {429, 502, 503}:
                stopped_early_status = outcome.http_status
                print(
                    "Provider/service capacity became unavailable "
                    f"(HTTP {outcome.http_status}); stopping immediately. "
                    "Inspect the API server log for the Groq quota detail.",
                    file=sys.stderr,
                    flush=True,
                )
                break
            if args.pace_seconds and index < len(entries) and outcome.duration_seconds:
                time.sleep(args.pace_seconds)
    finally:
        api.close()

    metrics_by_variant = {
        variant: compute_metrics([o for o in outcomes if o.variant == variant])
        for variant in {o.variant for o in outcomes}
    }
    payload = {
        "api_url": args.api_url,
        "preflight": checks,
        "metrics_by_variant": metrics_by_variant,
        "metrics_overall": compute_metrics(outcomes),
        "cache": cache.summary(),
        "uploads_reused_for_identical_files": api.uploads_reused,
        "live_model_calls_performed": sum(
            1 for o in outcomes if "served from the evaluation cache" not in o.notes
        ),
        "run_stopped_early": stopped_early_status is not None,
        "stopped_early_http_status": stopped_early_status,
        "selected_scenarios": len(entries),
        "unattempted_scenarios": len(entries) - len(outcomes),
        "scenarios": [o.to_json() for o in outcomes],
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS_DIR / f"{args.output_prefix}.json"
    md_path = REPORTS_DIR / f"{args.output_prefix}.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(payload, args.title), encoding="utf-8")

    print()
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    overall = payload["metrics_overall"]
    print(
        f"Fully correct: {overall['scenarios_fully_correct']}/{overall['scenarios_attempted']}"
    )
    print(f"FALSE PASSES: {overall['compliance']['false_pass_count']}")
    return 2 if stopped_early_status is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
