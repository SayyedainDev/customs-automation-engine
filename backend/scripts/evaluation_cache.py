"""Content-addressed cache for live synthetic-factory evaluation runs.

Groq's free tier has a daily token budget, and a full dataset pass costs far
more than a single scenario. Re-running the whole matrix to check one fix
exhausts the budget on work whose answer has not changed. This cache makes a
rerun cost only what genuinely differs.

The key is the full set of inputs that can change an answer:

* scenario id and variant;
* the normalized manifest row, including every non-PDF request input, claimed
  document role/type and declared expectation;
* the SHA-256 of every PDF actually sent (invoice, packing list, each
  supporting document);
* the extraction model;
* the extraction prompt version, the response-schema version and the OCR
  settings, each derived from the live code rather than hand-maintained;
* deterministic rule/legal-data and response-shaping code versions;
* when enabled, the workflow code, agent models, review policy and checkpoint
  profile.

If any of those change the key changes, so a stale answer can never be served.

**Only successful, finished runs are stored.** A 429, a 503, malformed model
output, a technical failure or an unfinished workflow is a statement about the
provider or the run, not about the scenario - caching one would freeze a
transient failure into every later report. Human decisions from another run are
never cached either, because they are not a property of the input.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
DEFAULT_CACHE_DIR = BACKEND_ROOT / ".evaluation_cache"

from app.services.extraction.cache_fingerprint import (  # noqa: E402
    runtime_extraction_cache_capability,
)

CACHE_FORMAT_VERSION = "2"

# Modules whose behaviour changes what a correct response looks like. A change
# to any of them must invalidate every cached answer.
_CODE_FINGERPRINT_SOURCES = (
    "scripts/run_synthetic_factory_evaluation.py",
    "app/services/structured_extraction_service.py",
    "app/services/multi_line_shipment_service.py",
    "app/services/supporting_document_service.py",
    "app/services/extraction/document_bundle.py",
    "app/services/extraction/cache_fingerprint.py",
    "app/services/extraction/cache_lock.py",
    "app/services/extraction/ocr_extractor.py",
    "app/services/extraction/staged_multi_line.py",
    "app/services/multi_line/item_matching.py",
    "app/services/multi_line/line_item_checks.py",
    "app/services/compliance/arithmetic_checks.py",
    "app/services/compliance/document_checks.py",
    "app/services/compliance/executable_rule_checks.py",
    "app/services/compliance/executable_rule_loader.py",
    "app/services/compliance/executable_rule_models.py",
    "app/services/compliance/rule_engine.py",
    "app/services/compliance/general_checks.py",
    "app/services/compliance/product_checks.py",
    "app/services/compliance/raw_cotton_checks.py",
    "app/services/compliance/result_builder.py",
    "app/services/compliance/rule_loader.py",
    "app/services/compliance/rule_models.py",
    "app/schemas/multi_line_extraction.py",
    "app/schemas/supporting_documents.py",
)

_WORKFLOW_FINGERPRINT_SOURCES = (
    "app/api/routes/customs_audit.py",
    "app/models/customs_audit.py",
    "app/schemas/customs_audit.py",
    "app/services/customs_audit/agents.py",
    "app/services/customs_audit/checkpointer.py",
    "app/services/customs_audit/consensus.py",
    "app/services/customs_audit/deps.py",
    "app/services/customs_audit/factory.py",
    "app/services/customs_audit/graph.py",
    "app/services/customs_audit/nodes.py",
    "app/services/customs_audit/query.py",
    "app/services/customs_audit/report.py",
    "app/services/customs_audit/safety.py",
    "app/services/customs_audit/state.py",
    "app/services/customs_audit/workflow_service.py",
)

_LEGAL_DATA_FINGERPRINT_SOURCES = (
    "regulatory_data/config/legal_effective_dates.json",
    "regulatory_data/config/textile_mvp_pct_codes.json",
    "regulatory_data/processed/commerce/export_policy/current_export_policy_rules.json",
    "regulatory_data/processed/compliance/textile_mvp_executable_rules.json",
    "regulatory_data/raw/psw/textile_product_requirements/textile_product_requirements.json",
)

# Statuses that describe the provider or the run, never the scenario.
NON_CACHEABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def code_version() -> str:
    """Digest of the modules that shape an evaluated response."""
    digest = hashlib.sha256()
    for relative in _CODE_FINGERPRINT_SOURCES:
        path = BACKEND_ROOT / relative
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes() if path.exists() else b"<missing>")
    return digest.hexdigest()[:16]


def workflow_code_version() -> str:
    """Digest every module that can change a workflow observation."""
    digest = hashlib.sha256()
    for relative in _WORKFLOW_FINGERPRINT_SOURCES:
        path = BACKEND_ROOT / relative
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes() if path.exists() else b"<missing>")
    return digest.hexdigest()[:16]


def legal_data_version() -> str:
    """Digest the deterministic rule/legal data used to grade a scenario."""
    digest = hashlib.sha256()
    for relative in _LEGAL_DATA_FINGERPRINT_SOURCES:
        path = PROJECT_ROOT / relative
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes() if path.exists() else b"<missing>")
    return digest.hexdigest()[:16]


_EXTRACTION_PROFILES = (
    "commercial_invoice",
    "packing_list",
    "supporting_document",
)


def _runtime_profiles() -> dict[str, dict[str, Any]]:
    """Read the exact profile fingerprints used by the running application."""
    capability = runtime_extraction_cache_capability()
    raw_profiles = capability.get("profiles")
    if not isinstance(raw_profiles, dict):
        raise RuntimeError("Runtime extraction cache profiles are unavailable")
    profiles: dict[str, dict[str, Any]] = {}
    for name in _EXTRACTION_PROFILES:
        raw_profile = raw_profiles.get(name)
        if not isinstance(raw_profile, dict):
            raise RuntimeError(
                f"Runtime extraction cache profile {name!r} is unavailable"
            )
        profiles[name] = raw_profile
    return profiles


def _combined_profile_version(
    profiles: dict[str, dict[str, Any]],
    field: str,
) -> str:
    """Bind each named extraction profile without re-deriving its fingerprint."""
    values: dict[str, str] = {}
    for name in _EXTRACTION_PROFILES:
        value = profiles[name].get(field)
        if not isinstance(value, str) or not value:
            raise RuntimeError(
                f"Runtime extraction cache profile {name!r} has no {field}"
            )
        values[name] = value
    serialized = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def prompt_version(
    profiles: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Combine the application's complete per-profile prompt fingerprints."""
    return _combined_profile_version(
        profiles or _runtime_profiles(),
        "prompt_version",
    )


def schema_version(
    profiles: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Combine the application's complete per-profile schema fingerprints."""
    return _combined_profile_version(
        profiles or _runtime_profiles(),
        "schema_version",
    )


def ocr_settings(
    profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved_profiles = profiles or _runtime_profiles()
    values = [resolved_profiles[name].get("ocr_settings") for name in _EXTRACTION_PROFILES]
    first = values[0]
    if not isinstance(first, dict) or any(value != first for value in values[1:]):
        raise RuntimeError(
            "Runtime extraction cache profiles disagree on OCR settings"
        )
    return dict(first)


def extraction_model(
    profiles: dict[str, dict[str, Any]] | None = None,
) -> str:
    resolved_profiles = profiles or _runtime_profiles()
    values = {
        resolved_profiles[name].get("extraction_model")
        for name in _EXTRACTION_PROFILES
    }
    if len(values) != 1:
        raise RuntimeError(
            "Runtime extraction cache profiles disagree on extraction model"
        )
    model = values.pop()
    if not isinstance(model, str) or not model:
        raise RuntimeError("Runtime extraction cache model is unavailable")
    return model


@dataclass(frozen=True)
class CacheFingerprint:
    """The parts of the key that are the same for every scenario in a run."""

    extraction_model: str
    prompt_version: str
    schema_version: str
    ocr_settings: dict[str, Any]
    code_version: str
    legal_data_version: str

    @classmethod
    def current(cls) -> CacheFingerprint:
        profiles = _runtime_profiles()
        return cls(
            extraction_model=extraction_model(profiles),
            prompt_version=prompt_version(profiles),
            schema_version=schema_version(profiles),
            ocr_settings=ocr_settings(profiles),
            code_version=code_version(),
            legal_data_version=legal_data_version(),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "extraction_model": self.extraction_model,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "ocr_settings": self.ocr_settings,
            "code_version": self.code_version,
            "legal_data_version": self.legal_data_version,
            "cache_format_version": CACHE_FORMAT_VERSION,
        }


class EvaluationCache:
    def __init__(
        self,
        directory: Path | None = None,
        *,
        fingerprint: CacheFingerprint | None = None,
        enabled: bool = True,
    ) -> None:
        self.directory = directory or DEFAULT_CACHE_DIR
        self.fingerprint = fingerprint or CacheFingerprint.current()
        self.enabled = enabled
        self.hits = 0
        self.misses = 0
        self.stores = 0
        self.rejected: list[str] = []
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)

    # ---- key ------------------------------------------------------------- #
    def key(
        self,
        *,
        scenario_id: str,
        variant: str,
        document_paths: list[Path],
        scenario_inputs: dict[str, Any],
        workflow_profile: dict[str, Any],
        workflow_enabled: bool = False,
    ) -> str:
        if bool(workflow_profile.get("enabled")) != workflow_enabled:
            raise ValueError(
                "workflow_profile.enabled must match workflow_enabled"
            )
        normalized_workflow_profile = dict(workflow_profile)
        if workflow_enabled:
            normalized_workflow_profile["code_version"] = workflow_code_version()
        payload = {
            "scenario_id": scenario_id,
            "variant": variant,
            "workflow_enabled": workflow_enabled,
            # The complete manifest row binds every non-PDF request input and
            # every declared expectation. This prevents a changed shipment date,
            # claimed document role/type or expected legal result from reusing a
            # scenario outcome merely because the PDF bytes stayed the same.
            "scenario_inputs": scenario_inputs,
            "workflow_profile": normalized_workflow_profile,
            "documents": sorted(
                f"{path.name}:{file_digest(path)}" for path in document_paths
            ),
            **self.fingerprint.to_json(),
        }
        try:
            serialized = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "evaluation cache inputs must be normalized JSON values"
            ) from exc
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    # ---- read / write ---------------------------------------------------- #
    def peek(self, key: str) -> dict[str, Any] | None:
        """Read an entry for planning without changing cache metrics or files."""
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return entry if isinstance(entry, dict) else None

    def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A truncated cache file is not evidence of anything; drop it.
            path.unlink(missing_ok=True)
            self.misses += 1
            return None
        self.hits += 1
        return entry

    def is_cacheable(
        self,
        *,
        http_status: int | None,
        technical_failure: str | None,
        workflow_finished: bool,
    ) -> tuple[bool, str | None]:
        """Whether this outcome is a property of the input, not of the run."""
        if technical_failure is not None:
            return False, f"technical failure: {technical_failure}"
        if http_status is None:
            return False, "no HTTP status recorded"
        if http_status in NON_CACHEABLE_HTTP_STATUSES:
            return False, f"transient provider/HTTP status {http_status}"
        if http_status != 200:
            return False, f"non-success HTTP status {http_status}"
        if not workflow_finished:
            return False, "workflow did not reach a terminal state"
        return True, None

    def put(
        self,
        key: str,
        payload: dict[str, Any],
        *,
        http_status: int | None,
        technical_failure: str | None,
        workflow_finished: bool = True,
    ) -> bool:
        if not self.enabled:
            return False
        cacheable, reason = self.is_cacheable(
            http_status=http_status,
            technical_failure=technical_failure,
            workflow_finished=workflow_finished,
        )
        if not cacheable:
            if reason:
                self.rejected.append(reason)
            return False
        self._path(key).write_text(
            json.dumps(
                {"fingerprint": self.fingerprint.to_json(), "payload": payload},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        self.stores += 1
        return True

    # ---- reporting ------------------------------------------------------- #
    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "directory": str(self.directory),
            "cached_scenarios_reused": self.hits,
            "cache_misses": self.misses,
            "responses_stored": self.stores,
            "results_not_cached": len(self.rejected),
            "not_cached_reasons": sorted(set(self.rejected)),
            "fingerprint": self.fingerprint.to_json(),
        }
