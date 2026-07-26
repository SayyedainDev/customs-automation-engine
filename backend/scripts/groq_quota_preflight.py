"""Fail-closed Groq daily-token preflight for live evaluations.

Groq's ordinary rate-limit headers describe TPM, not TPD. With no fresh
operator-supplied console snapshot, the live runner sends one tiny-prompt
request whose maximum completion is deliberately larger than the account's
known daily limit. A parseable TPD 429 exposes Limit / Used / Requested without
generation and can gate the measured run plan. Groq does not guarantee whether
daily quota is checked before request validation, so a 400, non-TPD 429, or
successful response fails closed and never authorizes a run.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from scripts.evaluation_cache import file_digest
from app.services.extraction.staged_multi_line import MAX_DISCOVERABLE_LINES


DEFAULT_TOKENS_PER_EXTRACTION = 4_100
DEFAULT_TOKENS_PER_AGENT_NARRATION = 4_100
ADMISSION_RESERVE_PERCENT = 20
# Extraction and narration clients explicitly set max_retries=0 so a provider
# refusal stops at the first transport attempt.
DEFAULT_EXTRACTION_SDK_ATTEMPTS = 1
# GroqNarrator explicitly sets max_retries=0.
DEFAULT_AGENT_SDK_ATTEMPTS = 1
DEFAULT_QUOTA_SNAPSHOT_MAX_AGE_SECONDS = 300
DEFAULT_QUOTA_SNAPSHOT_FUTURE_SKEW_SECONDS = 30

# One structured call can use the primary strict-schema transport and then the
# JSON-object transport if Groq rejects that schema.
DIRECT_LOGICAL_CALL_UPPER_BOUND = 2
SUPPORTING_LOGICAL_CALL_UPPER_BOUND = DIRECT_LOGICAL_CALL_UPPER_BOUND

# Invoice/packing extraction can consume the direct transports and then fall
# back to header + row discovery + one extraction per discovered row. Every
# staged call has the same strict-schema/JSON-object pair. Row discovery is
# hard-capped in the production extractor, whose constant is imported above so
# the estimator cannot silently drift from it.
MULTI_LINE_LOGICAL_CALL_UPPER_BOUND = (
    DIRECT_LOGICAL_CALL_UPPER_BOUND
    + DIRECT_LOGICAL_CALL_UPPER_BOUND * (2 + MAX_DISCOVERABLE_LINES)
)

# Groq enforces a hard per-request ceiling on max_tokens/max_completion_tokens
# that is independent of (and smaller than) this model's context window:
#
#   {"error": {"message": "`max_tokens` must be less than or equal to 65536,
#    the maximum value for `max_tokens` is less than the `context_window` for
#    this model", "type": "invalid_request_error", "param": "max_tokens"}}
#
# DEF-013 (found live): the probe previously requested 1_000_000, which Groq
# rejects at request validation (HTTP 400) before the daily-quota check ever
# runs. parse_tpd_quota_error correctly fails closed on that response, so the
# preflight never authorized a run on bad data - but it also never actually
# observed TPD status, silently degrading "reliably measures daily headroom"
# to "always refuses". A too-small probe is unsafe (may fail to trigger TPD
# and could consume real generation tokens on a habitually low-usage account);
# a too-large one is merely useless. This is the largest value Groq currently
# accepts for max_completion_tokens, confirmed live to trigger a parseable TPD
# 429 when daily headroom is below it.
GROQ_MAX_COMPLETION_TOKENS_CEILING = 65_536

# This is intentionally a maximum-completion declaration, not a generated
# prompt. The prompt itself stays tiny. Groq may reject the value during request
# validation instead of applying the TPD check; that outcome fails closed.
DEFAULT_QUOTA_PROBE_TOKENS = GROQ_MAX_COMPLETION_TOKENS_CEILING

_TPD_VALUES = re.compile(
    r"tokens\s+per\s+day\s*\(TPD\).*?"
    r"Limit\s+([\d,]+)\s*,\s*"
    r"Used\s+([\d,]+)\s*,\s*"
    r"Requested\s+([\d,]+)",
    re.IGNORECASE | re.DOTALL,
)


class QuotaPreflightError(RuntimeError):
    """The runner could not prove sufficient daily provider capacity."""


@dataclass(frozen=True)
class DailyTokenQuota:
    limit: int
    used: int
    requested: int | None

    @property
    def headroom(self) -> int:
        return max(self.limit - self.used, 0)


@dataclass(frozen=True)
class TokenEstimate:
    selected_scenarios: int
    document_references: int
    unique_extractions: int
    planned_same_run_reuses: int
    persisted_cache_hits: int
    tokens_per_extraction: int
    agent_narrations: int
    tokens_per_agent_narration: int
    profile_counts: dict[str, int]
    stress_path_extraction_attempts: int
    agent_transport_attempts: int
    sdk_attempts_per_logical_call: int
    agent_sdk_attempts_per_narration: int
    admission_reserve_percent: int
    planning_estimate_tokens_by_model: dict[str, int]
    admission_estimate_tokens_by_model: dict[str, int]
    stress_path_tokens_by_model: dict[str, int]
    planning_estimate_tokens: int
    admission_estimate_tokens: int
    stress_path_tokens: int

    def to_json(self) -> dict[str, Any]:
        return {
            "selected_scenarios": self.selected_scenarios,
            "document_references": self.document_references,
            "unique_extractions": self.unique_extractions,
            "planned_same_run_reuses": self.planned_same_run_reuses,
            "persisted_cache_hits": self.persisted_cache_hits,
            "tokens_per_extraction": self.tokens_per_extraction,
            "agent_narrations": self.agent_narrations,
            "tokens_per_agent_narration": self.tokens_per_agent_narration,
            "profile_counts": self.profile_counts,
            "stress_path_extraction_attempts": (
                self.stress_path_extraction_attempts
            ),
            "agent_transport_attempts": self.agent_transport_attempts,
            "sdk_attempts_per_logical_call": self.sdk_attempts_per_logical_call,
            "agent_sdk_attempts_per_narration": (
                self.agent_sdk_attempts_per_narration
            ),
            "admission_reserve_percent": self.admission_reserve_percent,
            "planning_estimate_tokens_by_model": (
                self.planning_estimate_tokens_by_model
            ),
            "admission_estimate_tokens_by_model": (
                self.admission_estimate_tokens_by_model
            ),
            "stress_path_tokens_by_model": self.stress_path_tokens_by_model,
            "planning_estimate_tokens": self.planning_estimate_tokens,
            "admission_estimate_tokens": self.admission_estimate_tokens,
            "stress_path_tokens": self.stress_path_tokens,
        }


class _AdmissionEstimateLike(Protocol):
    @property
    def admission_estimate_tokens(self) -> int: ...


@dataclass(frozen=True)
class QuotaCapacity:
    limit: int
    used: int
    headroom: int
    admission_estimate_tokens: int
    can_run: bool
    shortfall: int

    def to_json(self) -> dict[str, int | bool]:
        return {
            "limit": self.limit,
            "used": self.used,
            "headroom": self.headroom,
            "admission_estimate_tokens": self.admission_estimate_tokens,
            "can_run": self.can_run,
            "shortfall": self.shortfall,
        }


def _provider_error_message(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    message = error.get("message")
    return message if isinstance(message, str) else None


def _integer(value: str) -> int:
    return int(value.replace(",", ""))


def parse_tpd_quota_error(exc: Exception) -> DailyTokenQuota:
    """Parse only a verified TPD 429, without retaining provider text."""
    status_code = getattr(exc, "status_code", None)
    if status_code != 429:
        response_class = (
            f"HTTP {status_code}"
            if isinstance(status_code, int)
            else "an unknown response"
        )
        raise QuotaPreflightError(
            f"Groq quota diagnostic returned {response_class}; daily-token "
            "capacity could not be verified, so the live evaluation will not "
            "start."
        )
    message = _provider_error_message(exc)
    match = _TPD_VALUES.search(message or "")
    if match is None:
        raise QuotaPreflightError(
            "Groq quota diagnostic returned HTTP 429 without a parseable TPD "
            "limit; daily-token capacity could not be verified, so the live "
            "evaluation will not start."
        )
    limit, used, requested = (_integer(value) for value in match.groups())
    if limit <= 0 or used < 0 or requested <= 0:
        raise QuotaPreflightError(
            "Groq returned invalid daily-token capacity values; refusing to "
            "start the live evaluation."
        )
    return DailyTokenQuota(limit=limit, used=used, requested=requested)


def probe_daily_token_quota(
    *,
    client: Any,
    model: str,
    requested_tokens: int = DEFAULT_QUOTA_PROBE_TOKENS,
) -> DailyTokenQuota:
    """Send one oversized zero-generation diagnostic and require a TPD 429.

    No provider validation order is assumed. A successful response, request
    validation error, or non-TPD refusal reveals no usable daily headroom and
    therefore fails closed.
    """
    if requested_tokens <= 0:
        raise ValueError("requested_tokens must be positive")
    try:
        client.chat.completions.create(
            model=model,
            temperature=0,
            reasoning_effort="low",
            max_completion_tokens=requested_tokens,
            messages=[
                {
                    "role": "user",
                    "content": "Reply with OK.",
                }
            ],
        )
    except Exception as exc:
        return parse_tpd_quota_error(exc)
    raise QuotaPreflightError(
        "Groq TPD headroom is unobservable because the oversized diagnostic "
        "request unexpectedly succeeded and may have consumed tokens; "
        "refusing to infer daily capacity or start the live evaluation."
    )


def _snapshot_integer(value: Any, *, field: str, model: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise QuotaPreflightError(
            f"Groq console snapshot {field} for model {model} must be an integer."
        )
    return value


def load_operator_quota_snapshot(
    path: Path,
    *,
    required_models: set[str],
    max_age_seconds: int = DEFAULT_QUOTA_SNAPSHOT_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, DailyTokenQuota]:
    """Load a fresh operator transcription from Groq's console.

    The file is an explicit trust boundary: the runner does not claim to have
    queried an account-usage API. It only validates a timestamped Limit/Used
    snapshot supplied by the operator who can see the actual Groq console.
    """
    if not required_models:
        return {}
    if max_age_seconds <= 0:
        raise QuotaPreflightError(
            "Groq console snapshot max age must be positive."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QuotaPreflightError(
            f"Groq console snapshot {path} could not be read as JSON."
        ) from exc
    if not isinstance(payload, dict) or payload.get("source") != "groq_console":
        raise QuotaPreflightError(
            "Groq quota snapshot must declare source='groq_console'."
        )
    observed_raw = payload.get("observed_at")
    if not isinstance(observed_raw, str):
        raise QuotaPreflightError(
            "Groq console snapshot must include a timezone-aware observed_at."
        )
    try:
        observed_at = datetime.fromisoformat(observed_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QuotaPreflightError(
            "Groq console snapshot observed_at is not valid ISO-8601."
        ) from exc
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise QuotaPreflightError(
            "Groq console snapshot observed_at must include a timezone."
        )
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    age_seconds = (
        current.astimezone(timezone.utc)
        - observed_at.astimezone(timezone.utc)
    ).total_seconds()
    if age_seconds > max_age_seconds:
        raise QuotaPreflightError(
            f"Groq console snapshot is stale ({int(age_seconds)} seconds old; "
            f"maximum {max_age_seconds})."
        )
    if age_seconds < -DEFAULT_QUOTA_SNAPSHOT_FUTURE_SKEW_SECONDS:
        raise QuotaPreflightError(
            "Groq console snapshot observed_at is implausibly in the future."
        )

    models = payload.get("models")
    if not isinstance(models, dict):
        raise QuotaPreflightError(
            "Groq console snapshot must contain a models object."
        )
    missing = sorted(required_models - set(models))
    if missing:
        raise QuotaPreflightError(
            "Groq console snapshot is missing selected model(s): "
            + ", ".join(missing)
        )

    quotas: dict[str, DailyTokenQuota] = {}
    for model in sorted(required_models):
        row = models.get(model)
        if not isinstance(row, dict):
            raise QuotaPreflightError(
                f"Groq console snapshot row for model {model} must be an object."
            )
        limit = _snapshot_integer(row.get("limit"), field="limit", model=model)
        used = _snapshot_integer(row.get("used"), field="used", model=model)
        if limit <= 0 or used < 0:
            raise QuotaPreflightError(
                f"Groq console snapshot has invalid Limit/Used for model {model}."
            )
        quotas[model] = DailyTokenQuota(
            limit=limit,
            used=used,
            requested=None,
        )
    return quotas


def operator_cli_quota(
    *,
    limit: int,
    used: int,
    required_models: set[str],
) -> dict[str, DailyTokenQuota]:
    """Bind one operator-entered Limit/Used pair to one selected model."""
    if len(required_models) != 1:
        raise QuotaPreflightError(
            "CLI Limit/Used can cover exactly one model; multiple models require "
            "a timestamped --groq-tpd-snapshot file."
        )
    model = next(iter(required_models))
    if limit <= 0 or used < 0:
        raise QuotaPreflightError(
            "Operator-supplied Groq TPD Limit must be positive and Used "
            "must be non-negative."
        )
    return {
        model: DailyTokenQuota(limit=limit, used=used, requested=None)
    }


def _scenario_extraction_inputs(
    entry: dict[str, Any],
    *,
    factory_root: Path,
) -> list[tuple[str, Path]]:
    variant = str(entry["variant"])
    if entry.get("kind") == "supporting_documents":
        base = entry["base_scenario_documents"]
        inputs = [
            (
                "commercial_invoice",
                factory_root / str(base[f"commercial_invoice_{variant}"]),
            ),
            (
                "packing_list",
                factory_root / str(base[f"packing_list_{variant}"]),
            ),
        ]
        inputs.extend(
            ("supporting_document", factory_root / str(document["pdf"]))
            for document in entry["expected_documents"]
            if document.get("uploaded") and document.get("pdf")
        )
        return inputs

    scenario = factory_root / str(entry["scenario_id"])
    return [
        (
            "commercial_invoice",
            scenario / f"synthetic_commercial_invoice_{variant}.pdf",
        ),
        (
            "packing_list",
            scenario / f"synthetic_packing_list_{variant}.pdf",
        ),
    ]


def estimate_selected_scenarios(
    entries: list[dict[str, Any]],
    *,
    factory_root: Path,
    persisted_cache_hits: int = 0,
    tokens_per_extraction: int = DEFAULT_TOKENS_PER_EXTRACTION,
    workflow_enabled: bool = False,
    live_agents_enabled: bool = False,
    tokens_per_agent_narration: int = DEFAULT_TOKENS_PER_AGENT_NARRATION,
    extraction_model: str = "configured_extraction_model",
    broker_model: str | None = None,
    auditor_model: str | None = None,
    extraction_sdk_attempts: int = DEFAULT_EXTRACTION_SDK_ATTEMPTS,
    agent_sdk_attempts: int = DEFAULT_AGENT_SDK_ATTEMPTS,
) -> TokenEstimate:
    """Report planning, admission, and stress costs after planned UUID reuse.

    Identical bytes are reusable only inside the same extraction profile:
    invoice, packing-list, and supporting-document prompts/schemas are distinct.
    Claimed-only type strings create no extraction input.

    ``planning_estimate_tokens`` charges one measured extraction per unique
    document/profile and one call per enabled live-agent narration.
    ``admission_estimate_tokens`` applies a transparent 20% reserve per provider
    model and is the only figure used to gate the run.
    ``stress_path_tokens`` shows the reachable strict-schema and staged-row call
    topology as a diagnostic. It applies the measured per-extraction cost to
    every reachable call and is deliberately not represented as a hard bound.
    """
    if persisted_cache_hits < 0:
        raise ValueError("persisted_cache_hits must be non-negative")
    if tokens_per_extraction <= 0:
        raise ValueError("tokens_per_extraction must be positive")
    if tokens_per_agent_narration <= 0:
        raise ValueError("tokens_per_agent_narration must be positive")
    if extraction_sdk_attempts <= 0:
        raise ValueError("extraction_sdk_attempts must be positive")
    if agent_sdk_attempts <= 0:
        raise ValueError("agent_sdk_attempts must be positive")
    if not extraction_model.strip():
        raise ValueError("extraction_model must be non-empty")
    references: list[tuple[str, Path]] = []
    for entry in entries:
        references.extend(
            _scenario_extraction_inputs(entry, factory_root=factory_root)
        )

    unique: set[tuple[str, str]] = set()
    for profile, path in references:
        if not path.is_file():
            raise QuotaPreflightError(
                f"Cannot estimate live extraction cost because {path} is missing."
            )
        unique.add((profile, file_digest(path)))

    unique_count = len(unique)
    profile_counts = {
        profile: sum(1 for unique_profile, _digest in unique if unique_profile == profile)
        for profile in (
            "commercial_invoice",
            "packing_list",
            "supporting_document",
        )
    }
    multi_line_count = (
        profile_counts["commercial_invoice"] + profile_counts["packing_list"]
    )
    extraction_logical_calls = (
        multi_line_count * MULTI_LINE_LOGICAL_CALL_UPPER_BOUND
        + profile_counts["supporting_document"]
        * SUPPORTING_LOGICAL_CALL_UPPER_BOUND
    )
    stress_path_extraction_attempts = (
        extraction_logical_calls * extraction_sdk_attempts
    )
    agent_narrations = (
        2 * len(entries) if workflow_enabled and live_agents_enabled else 0
    )
    agent_transport_attempts = agent_narrations * agent_sdk_attempts
    stress_path_tokens_by_model: dict[str, int] = {
        extraction_model: (
            stress_path_extraction_attempts * tokens_per_extraction
        )
    }
    planning_estimate_tokens_by_model: dict[str, int] = {
        extraction_model: unique_count * tokens_per_extraction
    }
    if agent_narrations:
        resolved_broker_model = (broker_model or extraction_model).strip()
        resolved_auditor_model = (auditor_model or extraction_model).strip()
        if not resolved_broker_model or not resolved_auditor_model:
            raise ValueError("live-agent models must be non-empty")
        per_agent_role = (
            len(entries) * agent_sdk_attempts * tokens_per_agent_narration
        )
        stress_path_tokens_by_model[resolved_broker_model] = (
            stress_path_tokens_by_model.get(resolved_broker_model, 0)
            + per_agent_role
        )
        stress_path_tokens_by_model[resolved_auditor_model] = (
            stress_path_tokens_by_model.get(resolved_auditor_model, 0)
            + per_agent_role
        )
        measured_per_agent_role = len(entries) * tokens_per_agent_narration
        planning_estimate_tokens_by_model[resolved_broker_model] = (
            planning_estimate_tokens_by_model.get(resolved_broker_model, 0)
            + measured_per_agent_role
        )
        planning_estimate_tokens_by_model[resolved_auditor_model] = (
            planning_estimate_tokens_by_model.get(resolved_auditor_model, 0)
            + measured_per_agent_role
        )
    # Do not retain a zero-cost extraction-model group for an all-cache-hit
    # selection. This lets callers skip all provider diagnostics cleanly.
    stress_path_tokens_by_model = {
        model: tokens
        for model, tokens in stress_path_tokens_by_model.items()
        if tokens > 0
    }
    planning_estimate_tokens_by_model = {
        model: tokens
        for model, tokens in planning_estimate_tokens_by_model.items()
        if tokens > 0
    }
    admission_estimate_tokens_by_model = {
        model: (
            tokens * (100 + ADMISSION_RESERVE_PERCENT) + 99
        )
        // 100
        for model, tokens in planning_estimate_tokens_by_model.items()
    }
    stress_path_tokens = sum(stress_path_tokens_by_model.values())
    planning_estimate_tokens = sum(planning_estimate_tokens_by_model.values())
    admission_estimate_tokens = sum(
        admission_estimate_tokens_by_model.values()
    )
    return TokenEstimate(
        selected_scenarios=len(entries),
        document_references=len(references),
        unique_extractions=unique_count,
        planned_same_run_reuses=len(references) - unique_count,
        persisted_cache_hits=persisted_cache_hits,
        tokens_per_extraction=tokens_per_extraction,
        agent_narrations=agent_narrations,
        tokens_per_agent_narration=tokens_per_agent_narration,
        profile_counts=profile_counts,
        stress_path_extraction_attempts=stress_path_extraction_attempts,
        agent_transport_attempts=agent_transport_attempts,
        sdk_attempts_per_logical_call=extraction_sdk_attempts,
        agent_sdk_attempts_per_narration=agent_sdk_attempts,
        admission_reserve_percent=ADMISSION_RESERVE_PERCENT,
        planning_estimate_tokens_by_model=planning_estimate_tokens_by_model,
        admission_estimate_tokens_by_model=(
            admission_estimate_tokens_by_model
        ),
        stress_path_tokens_by_model=stress_path_tokens_by_model,
        planning_estimate_tokens=planning_estimate_tokens,
        admission_estimate_tokens=admission_estimate_tokens,
        stress_path_tokens=stress_path_tokens,
    )


def assess_quota_capacity(
    quota: DailyTokenQuota,
    estimate: _AdmissionEstimateLike | int,
) -> QuotaCapacity:
    admission_estimate = (
        estimate
        if isinstance(estimate, int)
        else estimate.admission_estimate_tokens
    )
    if admission_estimate < 0:
        raise ValueError("admission estimate tokens must be non-negative")
    shortfall = max(admission_estimate - quota.headroom, 0)
    return QuotaCapacity(
        limit=quota.limit,
        used=quota.used,
        headroom=quota.headroom,
        admission_estimate_tokens=admission_estimate,
        can_run=shortfall == 0,
        shortfall=shortfall,
    )
