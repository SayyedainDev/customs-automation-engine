"""Offline scope check against the golden evaluation set.

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 14.3 describes a CI-gated
regression run with scope recall and ungrounded-number count as hard gates.
This script is the slice of that which needs no live infrastructure at
all - no Groq key, no database - and can run as-is right now: Layer 1
injection detection and Layer 2 Stage A (the deterministic vocabulary
router). Every question in the golden set marked ``resolved_by: fast`` is
checked against exactly what Stage A alone decides; questions marked
``resolved_by: llm`` need a real Stage B (Groq) call to verify and are
reported separately rather than silently skipped or wrongly asserted
against.

A full run (also exercising retrieval, generation, and the output guard)
needs a live database and Groq key and is a separate follow-up, not this
script.

Usage::

    python -m scripts.run_golden_set
    python -m scripts.run_golden_set --file tests/eval/golden_questions.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.customs_chat.guards.input_guard import check_input  # noqa: E402
from app.services.customs_chat.guards.scope_router import deterministic_scope  # noqa: E402

DEFAULT_FILE = BACKEND_ROOT / "tests" / "eval" / "golden_questions.yaml"

_EXPECTED_IN_SCOPE = {"grounded", "general"}
_EXPECTED_OUT_OF_SCOPE = {"out_of_scope", "adversarial_scope", "injection"}


def evaluate(entries: list[dict[str, Any]]) -> dict[str, Any]:
    fast_checked = 0
    fast_correct = 0
    fast_failures: list[tuple[str, str]] = []
    llm_only: list[str] = []
    injection_checked = 0
    injection_correct = 0
    injection_failures: list[str] = []

    for entry in entries:
        question = entry["question"]
        category = entry["category"]
        resolved_by = entry.get("resolved_by", "fast")

        if category == "injection":
            injection_checked += 1
            verdict = check_input(question)
            if not verdict.ok and verdict.injection_detected:
                injection_correct += 1
            else:
                injection_failures.append(entry["id"])
            continue

        if resolved_by != "fast":
            llm_only.append(entry["id"])
            continue

        fast_checked += 1
        decided = deterministic_scope(question)
        expected_in_scope = category in _EXPECTED_IN_SCOPE
        if decided is None:
            fast_failures.append((entry["id"], "Stage A did not decide (escalated to Stage B)"))
            continue
        if decided.in_scope == expected_in_scope:
            fast_correct += 1
        else:
            fast_failures.append(
                (entry["id"], f"expected in_scope={expected_in_scope}, got {decided.in_scope}")
            )

    return {
        "fast_checked": fast_checked,
        "fast_correct": fast_correct,
        "fast_failures": fast_failures,
        "injection_checked": injection_checked,
        "injection_correct": injection_correct,
        "injection_failures": injection_failures,
        "llm_only_skipped": llm_only,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    args = parser.parse_args()

    entries = yaml.safe_load(args.file.read_text(encoding="utf-8"))
    report = evaluate(entries)

    print(f"Golden set: {args.file}")
    print(f"Stage A (deterministic) scope check: {report['fast_correct']}/{report['fast_checked']} correct")
    for entry_id, reason in report["fast_failures"]:
        print(f"    FAIL {entry_id}: {reason}")
    print(f"Injection detection: {report['injection_correct']}/{report['injection_checked']} correct")
    for entry_id in report["injection_failures"]:
        print(f"    FAIL {entry_id}")
    if report["llm_only_skipped"]:
        print(
            f"Skipped ({len(report['llm_only_skipped'])}, need a live Stage B / Groq run): "
            + ", ".join(report["llm_only_skipped"])
        )

    hard_gate_ok = not report["fast_failures"] and not report["injection_failures"]
    print()
    print("HARD GATE:", "PASS" if hard_gate_ok else "FAIL")
    return 0 if hard_gate_ok else 1


if __name__ == "__main__":
    sys.exit(main())
