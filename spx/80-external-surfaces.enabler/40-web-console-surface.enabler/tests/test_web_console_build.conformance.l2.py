"""Conformance evidence for the React and TypeScript web console production bundle."""

from __future__ import annotations

from cace_testing.harnesses.frontend_build import run_frontend_build


def test_frontend_production_bundle_builds_cleanly() -> None:
    observation = run_frontend_build()
    assert observation.returncode == 0, observation.stderr or observation.stdout
    assert observation.index_html_exists is True
