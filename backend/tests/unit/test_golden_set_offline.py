"""Runs the offline slice of the golden-set regression gate as part of the
normal test suite - docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 14.3: scope
recall and injection detection are hard gates. See
``scripts/run_golden_set.py`` for what "offline" covers and does not.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.run_golden_set import evaluate

GOLDEN_SET = Path(__file__).resolve().parents[1] / "eval" / "golden_questions.yaml"


def test_golden_set_offline_hard_gate():
    entries = yaml.safe_load(GOLDEN_SET.read_text(encoding="utf-8"))
    report = evaluate(entries)

    assert report["fast_failures"] == [], (
        f"Stage A scope regression: {report['fast_failures']}"
    )
    assert report["injection_failures"] == [], (
        f"injection detection regression: {report['injection_failures']}"
    )
    # A floor, not a ceiling: this catches the golden set silently shrinking
    # to nothing (which would make the assertions above vacuously pass)
    # without pinning the exact count, which would need editing on every
    # addition.
    assert report["fast_checked"] >= 50
    assert report["injection_checked"] >= 5
