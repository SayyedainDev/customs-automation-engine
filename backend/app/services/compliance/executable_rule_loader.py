"""Load and cache the validated executable rule set."""

from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from app.services.compliance.executable_rule_models import ExecutableRuleSet
from app.services.compliance.rule_loader import RuleSourceError


PROJECT_ROOT = Path(__file__).resolve().parents[4]
EXECUTABLE_RULES_PATH = (
    PROJECT_ROOT
    / "regulatory_data"
    / "processed"
    / "compliance"
    / "textile_mvp_executable_rules.json"
)


@lru_cache(maxsize=1)
def load_executable_rules() -> ExecutableRuleSet:
    """Load, validate and cache the immutable executable rule set."""
    try:
        raw_json = EXECUTABLE_RULES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuleSourceError(
            f"Executable rule source not found: {EXECUTABLE_RULES_PATH}"
        ) from exc
    try:
        return ExecutableRuleSet.model_validate_json(raw_json)
    except ValidationError as exc:
        raise RuleSourceError(
            f"Executable rule source does not match its schema: "
            f"{EXECUTABLE_RULES_PATH}: {exc}"
        ) from exc


def reload_executable_rules() -> ExecutableRuleSet:
    load_executable_rules.cache_clear()
    return load_executable_rules()
