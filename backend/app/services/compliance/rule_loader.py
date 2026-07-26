import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.services.compliance.rule_models import (
    ComplianceRuleSet,
    PctConfiguration,
    ProductRequirementsDocument,
)


PROJECT_ROOT = Path(__file__).resolve().parents[4]
REGULATORY_DATA_ROOT = PROJECT_ROOT / "regulatory_data"
PCT_CONFIG_PATH = REGULATORY_DATA_ROOT / "config" / "textile_mvp_pct_codes.json"
PRODUCT_REQUIREMENTS_PATH = (
    REGULATORY_DATA_ROOT
    / "raw"
    / "psw"
    / "textile_product_requirements"
    / "textile_product_requirements.json"
)

_PCT_PATTERN = re.compile(r"^\d{4}\.?\d{4}$")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class RuleSourceError(RuntimeError):
    """Raised when a structured regulatory rule source cannot be loaded safely."""


def normalize_pct_code(value: str) -> str:
    """Normalize 5201.0090 and 52010090 to the same eight-digit code."""

    cleaned = value.strip()
    if not _PCT_PATTERN.fullmatch(cleaned):
        raise ValueError("PCT code must contain eight digits, optionally with one dot.")
    return cleaned.replace(".", "")


def _load_model(path: Path, model_type: type[_ModelT]) -> _ModelT:
    try:
        raw_json = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuleSourceError(f"Rule source not found: {path}") from exc
    try:
        return model_type.model_validate_json(raw_json)
    except ValidationError as exc:
        raise RuleSourceError(
            f"Rule source does not match its regulatory schema: {path}: {exc}"
        ) from exc


def _combined_rule_checksum(paths: tuple[Path, ...]) -> str:
    """Hash source labels and exact bytes so one version identifies both files."""

    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        try:
            content = path.read_bytes()
        except FileNotFoundError as exc:
            raise RuleSourceError(f"Rule source not found: {path}") from exc
        relative_path = str(path.relative_to(PROJECT_ROOT)).encode("utf-8")
        digest.update(len(relative_path).to_bytes(4, "big"))
        digest.update(relative_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def _relative_source(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


@lru_cache(maxsize=1)
def load_compliance_rules() -> ComplianceRuleSet:
    """Load, validate and cache one immutable regulatory rule set."""

    pct_config = _load_model(PCT_CONFIG_PATH, PctConfiguration)
    requirement_data = _load_model(
        PRODUCT_REQUIREMENTS_PATH,
        ProductRequirementsDocument,
    )

    if not pct_config.pct_codes:
        raise RuleSourceError("The textile MVP PCT configuration is empty.")

    try:
        supported_codes = frozenset(
            normalize_pct_code(code) for code in pct_config.pct_codes
        )
        metadata_by_code = {
            normalize_pct_code(product.pct_code): product
            for product in pct_config.products
        }
        requirements_by_code = {
            normalize_pct_code(product.pct_code): product
            for product in requirement_data.products
        }
    except ValueError as exc:
        raise RuleSourceError(
            "A regulatory rule source contains an invalid PCT code."
        ) from exc

    missing_metadata = supported_codes.difference(metadata_by_code)
    missing_requirements = supported_codes.difference(requirements_by_code)
    if missing_metadata:
        raise RuleSourceError(
            "Missing tariff metadata for configured PCT code(s): "
            + ", ".join(sorted(missing_metadata))
        )
    if missing_requirements:
        raise RuleSourceError(
            "Missing product requirements for configured PCT code(s): "
            + ", ".join(sorted(missing_requirements))
        )

    return ComplianceRuleSet(
        supported_codes=supported_codes,
        product_metadata=metadata_by_code,
        product_requirements=requirements_by_code,
        common_export_clearance=requirement_data.common_export_clearance,
        conditional_certificate_of_origin=(
            requirement_data.conditional_certificate_of_origin
        ),
        pct_config_source=_relative_source(PCT_CONFIG_PATH),
        product_requirements_source=_relative_source(PRODUCT_REQUIREMENTS_PATH),
        rule_data_version=_combined_rule_checksum(
            (PCT_CONFIG_PATH, PRODUCT_REQUIREMENTS_PATH)
        ),
    )


def reload_compliance_rules() -> ComplianceRuleSet:
    """Clear the single cache and atomically load a fresh validated rule set."""

    load_compliance_rules.cache_clear()
    return load_compliance_rules()
