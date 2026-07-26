"""Turn a live supporting-document evaluation run into a readable report.

Reads only what an actual run produced. Every number here is counted from
``reports/<prefix>.json`` and the raw API responses beside it; nothing is
projected, estimated or filled in for entries that did not run.

Run:
    python -m scripts.build_supporting_document_live_report
    python -m scripts.build_supporting_document_live_report --prefix supporting_smoke
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
REPORTS_DIR = BACKEND_ROOT / "reports"
FACTORY_ROOT = PROJECT_ROOT / "synthetic_factory"
MANIFEST_PATH = FACTORY_ROOT / "scenario_manifest.json"

OUTPUT_JSON = REPORTS_DIR / "supporting_document_live_validation.json"
OUTPUT_MD = REPORTS_DIR / "supporting_document_live_validation.md"


def _load(prefixes: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge every run, keeping completed and provider-blocked entries apart.

    A blocked entry is not a result and must never be averaged in with one, but
    it must not vanish either - silently dropping it would make a run that
    achieved nothing look like a run that was never attempted. A later
    *completed* result supersedes an earlier blocked one; a later *blocked*
    attempt never overwrites an earlier completed result.
    """
    completed: dict[tuple[str, str], dict[str, Any]] = {}
    blocked: dict[tuple[str, str], dict[str, Any]] = {}
    for prefix in prefixes:
        path = REPORTS_DIR / f"{prefix}.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("scenarios", []):
            key = (row["scenario_id"], row["variant"])
            if row.get("technical_failure"):
                if key not in completed:
                    blocked[key] = row
            elif row.get("supporting_documents"):
                completed[key] = row
                blocked.pop(key, None)
    order = lambda row: (row["scenario_id"], row["variant"])  # noqa: E731
    return sorted(completed.values(), key=order), sorted(blocked.values(), key=order)


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    planned = manifest.get("supporting_document_scenarios", [])

    documents = [
        document
        for row in rows
        for document in row["supporting_documents"]["documents"]
        if document.get("claimed_document_type") != "<claimed-only leak>"
    ]
    uploaded = [d for d in documents if d.get("uploaded_actual")]
    claimed_only = [d for d in documents if d.get("uploaded_expected") is False]

    uuid_matched = [
        d
        for d in uploaded
        if d.get("document_id_sent")
        and str(d.get("document_id_processed")) == str(d.get("document_id_sent"))
    ]
    type_correct = [
        d
        for d in uploaded
        if (
            d.get("detected_type_canonical_actual")
            == d.get("detected_type_expected")
            if "detected_type_expected" in d
            else (
                d.get("state_actual") not in {"type_mismatch", None}
                and d.get("detected_type_actual")
            )
        )
    ]
    status_correct = [
        d
        for d in documents
        if d.get("content_status_actual") == d.get("content_status_expected")
    ]
    authenticity_ok = [
        d for d in uploaded if d.get("authenticity_actual") == "not_externally_verified"
    ]
    claimed_only_correct = [
        d
        for d in claimed_only
        if d.get("uploaded_actual") is False
        and d.get("content_status_actual") == "failed"
    ]
    leaks = [
        row["scenario_id"]
        for row in rows
        if row["supporting_documents"].get("claimed_only_satisfied_a_requirement")
    ]
    false_passes = [row["scenario_id"] for row in rows if row.get("false_pass")]
    fields = [
        field
        for document in documents
        for field in document.get("fields", [])
    ]
    fields_correct = [field for field in fields if field.get("correct")]
    presence_graded = [
        document
        for document in documents
        if "counts_as_present_expected" in document
    ]
    presence_correct = [
        document
        for document in presence_graded
        if document.get("counts_as_present_actual")
        == document.get("counts_as_present_expected")
    ]
    actions_graded = [
        document
        for document in documents
        if "required_action_expected" in document
    ]
    actions_correct = [
        document
        for document in actions_graded
        if document.get("required_action_actual")
        == document.get("required_action_expected")
    ]

    def ratio(part: list[Any], whole: list[Any]) -> str:
        if not whole:
            return "n/a (0 observed)"
        return f"{len(part)}/{len(whole)} ({len(part) / len(whole) * 100:.1f}%)"

    return {
        "scenarios_planned": len(planned),
        "scenarios_completed": len(rows),
        "scenarios_by_variant": {
            variant: sum(1 for row in rows if row["variant"] == variant)
            for variant in sorted({row["variant"] for row in rows})
        },
        "scenarios_fully_correct": sum(1 for row in rows if row.get("ok")),
        "documents_observed": len(documents),
        "documents_uploaded": len(uploaded),
        "documents_claimed_only": len(claimed_only),
        "uuid_actually_processed": ratio(uuid_matched, uploaded),
        "type_classification_correct": ratio(type_correct, uploaded),
        "field_extraction_correct": ratio(fields_correct, fields),
        "content_status_correct": ratio(status_correct, documents),
        "presence_credit_correct": ratio(presence_correct, presence_graded),
        "required_action_correct": ratio(actions_correct, actions_graded),
        "external_authenticity_always_unverified": ratio(authenticity_ok, uploaded),
        "claimed_only_correctly_earned_nothing": ratio(claimed_only_correct, claimed_only),
        "claimed_only_leaks": leaks,
        "false_legal_passes": false_passes,
        "false_legal_pass_count": len(false_passes),
        "workflow_ids": [row.get("workflow_id") for row in rows if row.get("workflow_id")],
    }


def render(rows: list[dict[str, Any]], metrics: dict[str, Any]) -> str:
    lines = [
        "# Supporting-Document Live Validation",
        "",
        "Every figure below is counted from an actual completed run against the "
        "real HTTP API (real Groq, real Tesseract, real PostgreSQL). Scenarios "
        "that did not run are reported as not run, never estimated.",
        "",
        "## Coverage",
        "",
        f"- Supporting-document entries in the manifest: **{metrics['scenarios_planned']}**",
        f"- Entries completed live: **{metrics['scenarios_completed']}**"
        f" ({', '.join(f'{k}: {v}' for k, v in metrics['scenarios_by_variant'].items()) or 'none'})",
        f"- Entries fully correct: **{metrics['scenarios_fully_correct']}"
        f"/{metrics['scenarios_completed']}**",
        f"- Entries provider-blocked (never assessed): "
        f"**{metrics.get('scenarios_provider_blocked', 0)}**",
        "",
        "## The claim this run exists to test",
        "",
        "| Property | Result |",
        "| --- | --- |",
        f"| Uploaded UUID is the UUID actually processed | {metrics['uuid_actually_processed']} |",
        f"| Document type read from the page, correct | {metrics['type_classification_correct']} |",
        f"| Supporting-document fields match the manifest | {metrics['field_extraction_correct']} |",
        f"| Deterministic content status matches the manifest | {metrics['content_status_correct']} |",
        f"| Presence credit matches the manifest | {metrics['presence_credit_correct']} |",
        f"| Required action matches the manifest | {metrics['required_action_correct']} |",
        f"| Claimed-only type earned nothing | {metrics['claimed_only_correctly_earned_nothing']} |",
        f"| External authenticity always `not_externally_verified` | {metrics['external_authenticity_always_unverified']} |",
        f"| **False legal passes** | **{metrics['false_legal_pass_count']}** |",
        "",
    ]
    if metrics["claimed_only_leaks"]:
        lines += [
            "> **A claimed-only document type satisfied a requirement in: "
            + ", ".join(metrics["claimed_only_leaks"])
            + "**. This is a critical defect.",
            "",
        ]
    else:
        lines += [
            "No claimed-only document type satisfied any requirement in any "
            "completed run.",
            "",
        ]

    lines += ["## Per scenario", ""]
    for row in rows:
        supporting = row["supporting_documents"]
        lines += [
            f"### `{row['scenario_id']}` [{row['variant']}]",
            "",
            f"- Deterministic status: **{row.get('deterministic_status')}** "
            f"(expected `{row.get('expected_status')}`)",
            f"- Fully correct: **{'yes' if row.get('ok') else 'no'}**",
        ]
        if supporting.get("claimed_only_types_sent"):
            lines.append(
                "- Claimed as bare strings (no UUID): "
                + ", ".join(f"`{t}`" for t in supporting["claimed_only_types_sent"])
            )
        lines += [
            "",
            "| Document | Uploaded | Detected | State | Result | Expected | Authenticity |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for document in supporting["documents"]:
            if document.get("claimed_document_type") == "<claimed-only leak>":
                continue
            lines.append(
                "| `{claimed}` | {uploaded} | {detected} | {state} | {actual} | "
                "{expected} | {auth} |".format(
                    claimed=document.get("claimed_document_type"),
                    uploaded="Yes" if document.get("uploaded_actual") else "No",
                    detected=document.get("detected_type_actual") or "-",
                    state=document.get("state_actual") or "-",
                    actual=document.get("content_status_actual") or "-",
                    expected=document.get("content_status_expected") or "-",
                    auth=document.get("authenticity_actual") or "-",
                )
            )
        errors = [e for d in supporting["documents"] for e in d.get("errors", [])]
        if errors:
            lines += ["", "Disagreements with the manifest:", ""]
            lines += [f"- {error}" for error in errors]
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        action="append",
        default=None,
        help="Report prefix to read (may be repeated). Later runs win.",
    )
    args = parser.parse_args()
    prefixes = args.prefix or [
        "supporting_smoke",
        "supporting_def010",
        "supporting_document_live_validation_run",
    ]

    rows, blocked = _load(prefixes)
    metrics = summarise(rows)
    metrics["scenarios_provider_blocked"] = len(blocked)
    metrics["provider_blocked_scenarios"] = [
        {"scenario_id": row["scenario_id"], "variant": row["variant"],
         "reason": row.get("technical_failure")}
        for row in blocked
    ]
    payload = {
        "source_reports": prefixes,
        "metrics": metrics,
        "scenarios": rows,
        "note": (
            "Counted from completed live runs only. Entries not present were "
            "not run; no value here is projected from a partial run."
        ),
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    OUTPUT_MD.write_text(render(rows, metrics), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")
    print(
        f"{metrics['scenarios_completed']}/{metrics['scenarios_planned']} entries "
        f"completed, {metrics.get('scenarios_provider_blocked', 0)} provider-blocked; "
        f"false legal passes: {metrics['false_legal_pass_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
