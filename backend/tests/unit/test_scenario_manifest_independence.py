"""The independent manifest must stay independent of the code it grades.

If the expected statuses were produced by the deterministic engine, the live
evaluation would only be checking that the engine agrees with itself. These
tests enforce the independence contract structurally rather than by convention.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent
MANIFEST_BUILDER = BACKEND_ROOT / "scripts" / "build_scenario_manifest.py"
MANIFEST_PATH = PROJECT_ROOT / "synthetic_factory" / "scenario_manifest.json"

# Any import from these packages would mean the manifest is derived from the
# software under test.
FORBIDDEN_IMPORT_PREFIXES = (
    "app.services.compliance",
    "app.services.multi_line",
    "app.services.multi_line_shipment_service",
    "app.services.customs_audit",
    "app.services.regulatory",
)

requires_manifest = pytest.mark.skipif(
    not MANIFEST_PATH.exists(), reason="scenario manifest not generated"
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_manifest_builder_never_imports_the_compliance_engine() -> None:
    modules = _imported_modules(MANIFEST_BUILDER)
    offending = sorted(
        module
        for module in modules
        if module.startswith(FORBIDDEN_IMPORT_PREFIXES)
    )
    assert offending == [], (
        "build_scenario_manifest.py imports the software it is meant to grade: "
        f"{offending}"
    )


def test_manifest_builder_declares_statuses_as_literals() -> None:
    """Expected statuses must be hand-written constants, not computed."""
    source = MANIFEST_BUILDER.read_text(encoding="utf-8")
    assert "BEHAVIOUR" in source
    assert "expected_primary_status" in source
    # A call into an engine would look like one of these.
    for forbidden in (
        "DeterministicComplianceRuleEngine",
        "evaluate_executable_rules",
        "match_line_items",
        "shipment_level_checks",
    ):
        assert forbidden not in source, (
            f"Manifest builder references {forbidden}; expectations must be "
            "declared, not computed by the engine under test."
        )


@requires_manifest
def test_manifest_covers_every_scenario_in_both_variants() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = manifest["scenarios"]
    by_scenario: dict[str, set[str]] = {}
    for entry in entries:
        by_scenario.setdefault(entry["scenario_id"], set()).add(entry["variant"])
    assert by_scenario, "manifest is empty"
    for scenario_id, variants in by_scenario.items():
        assert variants == {"text", "scanned"}, (
            f"{scenario_id} is missing a variant: {variants}"
        )


@requires_manifest
def test_every_entry_declares_a_gradeable_expectation() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    valid_statuses = {"passed", "failed", "manual_review"}
    for entry in manifest["scenarios"]:
        assert entry["expected_primary_status"] in valid_statuses
        assert isinstance(entry["expected_failed_checks"], list)
        assert entry["expected_line_item_count"] >= 1
        assert entry["outcome_explanation"], entry["scenario_id"]
        assert entry["status_authority"] == "deterministic_python_engine_only"


@requires_manifest
def test_error_scenarios_expect_a_definite_failure_not_uncertainty() -> None:
    """An injected defect must be graded as a hard failure, never review."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for entry in manifest["scenarios"]:
        if entry["scenario_id"].startswith("error_"):
            assert entry["expected_primary_status"] == "failed", entry["scenario_id"]
            assert entry["expected_failed_checks"], entry["scenario_id"]
            assert entry["injected_defect"], entry["scenario_id"]


@requires_manifest
def test_clean_scenarios_declare_no_injected_defect() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for entry in manifest["scenarios"]:
        if entry["scenario_id"].startswith("clean_"):
            assert entry["injected_defect"] is None
            assert entry["expected_primary_status"] == "passed"
            assert entry["expected_failed_checks"] == []
