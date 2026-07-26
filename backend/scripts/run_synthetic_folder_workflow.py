"""Run synthetic invoice/packing-list folders through the existing HTTP API.

This is a terminal client only. It does not import or modify backend workflow,
extraction, compliance, agent, or persistence logic.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class TerminalRunnerError(RuntimeError):
    """A readable terminal-runner failure."""


@dataclass(frozen=True)
class DocumentPair:
    key: str
    invoice_path: Path
    packing_list_path: Path


@dataclass(frozen=True)
class WorkflowDefaults:
    shipment_date: str | None = None
    letter_of_credit_date: str | None = None
    additional_uploaded_document_types: tuple[str, ...] = ()


_ROLE_TOKENS = {
    "synthetic",
    "commercial",
    "invoice",
    "packing",
    "list",
    "document",
    "documents",
    "doc",
}


def _document_role(path: Path) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "_", path.stem.casefold())
    if "packing_list" in normalized or normalized.startswith("packing"):
        return "packing_list"
    if "commercial_invoice" in normalized or "invoice" in normalized:
        return "commercial_invoice"
    return None


def _pair_key(root: Path, path: Path) -> str:
    tokens = re.findall(r"[a-z0-9]+", path.stem.casefold())
    variant_tokens = [token for token in tokens if token not in _ROLE_TOKENS]
    variant = "_".join(variant_tokens) or "default"
    relative_parent = path.parent.relative_to(root)
    parent = relative_parent.as_posix()
    return f"{parent}/{variant}" if parent != "." else variant


def discover_document_pairs(folder: Path) -> tuple[list[DocumentPair], list[Path]]:
    root = folder.expanduser().resolve()
    if not root.is_dir():
        raise TerminalRunnerError(f"Folder does not exist: {root}")

    grouped: dict[str, dict[str, Path]] = {}
    ignored: list[Path] = []
    for path in sorted(root.rglob("*.pdf")):
        role = _document_role(path)
        if role is None:
            ignored.append(path)
            continue
        key = _pair_key(root, path)
        role_paths = grouped.setdefault(key, {})
        if role in role_paths:
            raise TerminalRunnerError(
                f"More than one {role} PDF has pair key '{key}': "
                f"{role_paths[role]} and {path}"
            )
        role_paths[role] = path

    pairs: list[DocumentPair] = []
    incomplete: list[str] = []
    for key, role_paths in sorted(grouped.items()):
        invoice = role_paths.get("commercial_invoice")
        packing = role_paths.get("packing_list")
        if invoice is None or packing is None:
            missing = "commercial invoice" if invoice is None else "packing list"
            incomplete.append(f"{key} (missing {missing})")
            continue
        pairs.append(
            DocumentPair(
                key=key,
                invoice_path=invoice,
                packing_list_path=packing,
            )
        )

    if incomplete:
        raise TerminalRunnerError(
            "Incomplete document pair(s): " + ", ".join(incomplete)
        )
    if not pairs:
        raise TerminalRunnerError(
            f"No invoice/packing-list PDF pairs were found under {root}"
        )
    return pairs, ignored


def load_workflow_defaults(pair: DocumentPair) -> WorkflowDefaults:
    request_path = pair.invoice_path.parent / "multi_line_api_request.json"
    if not request_path.is_file():
        return WorkflowDefaults()
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TerminalRunnerError(
            f"Could not read request defaults from {request_path}: {exc}"
        ) from exc
    additional = payload.get("additional_uploaded_document_types") or []
    if not isinstance(additional, list) or not all(
        isinstance(value, str) for value in additional
    ):
        raise TerminalRunnerError(
            f"additional_uploaded_document_types must be a string list in {request_path}"
        )
    return WorkflowDefaults(
        shipment_date=payload.get("shipment_date"),
        letter_of_credit_date=payload.get("letter_of_credit_date"),
        additional_uploaded_document_types=tuple(additional),
    )


def build_review_payload(
    *,
    action: str,
    reviewer_reference: str,
    review_note: str | None = None,
    field_path: str | None = None,
    original_value: Any = None,
    corrected_value: Any = None,
    reason: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "field_path": field_path,
        "original_value": original_value,
        "corrected_value": corrected_value,
        "provided_document_ids": [],
        "reviewer_reference": reviewer_reference,
        "reason": reason,
        "review_note": review_note,
        "source": source,
    }


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return str(value)


def narrate_event(event: dict[str, Any]) -> list[str]:
    """Turn one persisted workflow event into plain-English line(s)."""
    event_type = event.get("event_type") or "unknown"
    payload = event.get("event_payload") or {}

    if event_type == "workflow_started":
        return ["Workflow started - both documents were loaded into the audit."]
    if event_type == "broker_report_created":
        violations = payload.get("violations") or []
        confidence = payload.get("report_confidence")
        detail = f"confidence {_pct(confidence)}" if confidence is not None else "no confidence score"
        found = "no violations" if not violations else f"{len(violations)} possible violation(s)"
        return [f"Broker agent read the shipment ({detail}) and flagged {found}."]
    if event_type == "deterministic_status_frozen":
        return [f"Rule checks finished. Deterministic status: {payload.get('overall_status', 'unknown').upper()}."]
    if event_type == "auditor_report_created":
        violations = payload.get("violations") or []
        action = payload.get("recommended_action", "unknown")
        evidence = payload.get("evidence_support", "unknown")
        found = "no violations" if not violations else f"{len(violations)} possible violation(s)"
        return [
            f"Auditor agent independently challenged the shipment: recommends '{action}', "
            f"evidence support '{evidence}', {found}."
        ]
    if event_type == "consensus_computed":
        reached = payload.get("consensus_reached")
        needs = payload.get("requires_human_review")
        agree = "agreed" if reached else "did NOT agree"
        review = "a human review is required" if needs else "no human review is needed"
        return [f"The two agents {agree}; {review}."]
    if event_type == "final_report_built":
        return [f"Final audit report was built (status: {payload.get('status', 'unknown').upper()})."]

    # Fallback: a readable label rather than raw JSON.
    friendly = event_type.replace("_", " ")
    return [f"{friendly.capitalize()}."]


class CustomsApiTerminalClient:
    def __init__(self, api_url: str, timeout_seconds: float):
        self.api_url = api_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.api_url,
            timeout=timeout_seconds,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def _json(self, response: httpx.Response, operation: str) -> Any:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text.strip()[:2_000]
            raise TerminalRunnerError(
                f"{operation} failed with HTTP {response.status_code}: {detail}"
            ) from exc
        try:
            return response.json()
        except ValueError as exc:
            raise TerminalRunnerError(
                f"{operation} returned a non-JSON response"
            ) from exc

    def health(self) -> dict[str, Any]:
        try:
            response = self._client.get("/health")
        except httpx.HTTPError as exc:
            raise TerminalRunnerError(
                f"Could not connect to {self.api_url}: {exc}"
            ) from exc
        return self._json(response, "API health check")

    def upload(self, path: Path) -> dict[str, Any]:
        with path.open("rb") as file_handle:
            response = self._client.post(
                "/documents/upload",
                files={
                    "file": (
                        path.name,
                        file_handle,
                        "application/pdf",
                    )
                },
            )
        return self._json(response, f"Upload {path.name}")

    def start_workflow(
        self,
        *,
        invoice_document_id: str,
        packing_list_document_id: str,
        defaults: WorkflowDefaults,
    ) -> dict[str, Any]:
        response = self._client.post(
            "/api/v1/customs-audit/workflows",
            json={
                "commercial_invoice_document_id": invoice_document_id,
                "packing_list_document_id": packing_list_document_id,
                "shipment_date": defaults.shipment_date,
                "letter_of_credit_date": defaults.letter_of_credit_date,
                "additional_uploaded_document_types": list(
                    defaults.additional_uploaded_document_types
                ),
            },
        )
        return self._json(response, "Start customs-audit workflow")

    def get_status(self, workflow_id: str) -> dict[str, Any]:
        response = self._client.get(
            f"/api/v1/customs-audit/workflows/{workflow_id}"
        )
        return self._json(response, "Get workflow status")

    def get_events(self, workflow_id: str) -> list[dict[str, Any]]:
        response = self._client.get(
            f"/api/v1/customs-audit/workflows/{workflow_id}/events"
        )
        payload = self._json(response, "Get workflow events")
        events = payload.get("events")
        return events if isinstance(events, list) else []

    def get_review(self, workflow_id: str) -> dict[str, Any]:
        response = self._client.get(
            f"/api/v1/customs-audit/workflows/{workflow_id}/review"
        )
        return self._json(response, "Get human-review task")

    def submit_review(
        self,
        workflow_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = self._client.post(
            f"/api/v1/customs-audit/workflows/{workflow_id}/review",
            json=payload,
        )
        return self._json(response, "Submit human review")


def _show_events(events: list[dict[str, Any]], start_index: int = 0) -> None:
    if not events[start_index:]:
        print("  No new audit steps.")
        return
    for index, event in enumerate(events[start_index:], start=start_index + 1):
        lines = narrate_event(event)
        print(f"  {index:>2}. {lines[0]}")
        for extra in lines[1:]:
            print(f"      {extra}")


_RESULT_ICON = {"PASSED": "✅", "FAILED": "✗", "NEEDS HUMAN REVIEW": "⚠"}

_PROBLEM_HEADINGS = [
    ("missing_documents", "Missing Documents"),
    ("missing_or_uncertain_fields", "Missing or Uncertain Fields"),
    ("document_mismatches", "Document Mismatches"),
    ("calculation_errors", "Calculation Errors"),
    ("regulatory_problems", "Regulatory Problems"),
    ("evidence_limitations", "Evidence Limitations"),
]


def _dash(value: Any) -> str:
    return "-" if value in (None, "", "None") else str(value)


def _user_report(status: dict[str, Any]) -> dict[str, Any]:
    return (status.get("final_report") or {}).get("user_report") or {}


def report_sections(status: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """Return the report as ordered (heading, lines) sections for any renderer."""
    report = _user_report(status)
    sections: list[tuple[str, list[str]]] = []

    result = report.get("overall_result") or str(status.get("status", "")).upper()
    icon = _RESULT_ICON.get(result, "")
    sections.append((
        "Overall Result",
        [f"{icon} {result}".strip(), report.get("overall_reason", "")],
    ))

    summary = report.get("shipment_summary") or {}
    sections.append((
        "Shipment Summary",
        [
            f"Exporter        : {_dash(summary.get('exporter'))}",
            f"Buyer           : {_dash(summary.get('buyer'))}",
            f"Invoice number  : {_dash(summary.get('invoice_number'))}",
            f"Invoice date    : {_dash(summary.get('invoice_date'))}",
            f"Destination     : {_dash(summary.get('destination'))}",
            f"Shipment date   : {_dash(summary.get('shipment_date'))}",
            f"Invoice value   : {_dash(summary.get('total_invoice_value'))} "
            f"{_dash(summary.get('currency'))}".strip(),
            f"Total packages  : {_dash(summary.get('total_packages'))}",
            f"Declared net wt : {_dash(summary.get('declared_net_weight'))}",
            f"Declared gross  : {_dash(summary.get('declared_gross_weight'))}",
        ],
    ))

    item_lines: list[str] = []
    for item in report.get("line_items") or []:
        item_lines.append(
            f"Line {_dash(item.get('line_number'))}: {_dash(item.get('product_name'))} "
            f"(PCT {_dash(item.get('pct_code'))})"
        )
        item_lines.append(
            f"    qty {_dash(item.get('quantity'))} {_dash(item.get('unit'))} "
            f"@ {_dash(item.get('unit_price'))} = {_dash(item.get('line_total'))}; "
            f"net {_dash(item.get('net_weight'))}, gross {_dash(item.get('gross_weight'))}"
        )
        item_lines.append(
            f"    invoice p.{_dash(item.get('invoice_source_page'))}, "
            f"packing p.{_dash(item.get('packing_list_source_page'))}, "
            f"match by {_dash(item.get('match_method'))}, "
            f"confidence {_dash(item.get('extraction_confidence'))}"
        )
    if item_lines:
        sections.append(("Line Items", item_lines))

    passed = report.get("checks_passed") or []
    if passed:
        sections.append(("Checks Passed", [f"- {name}" for name in passed]))

    problems = report.get("problems") or {}
    problem_lines: list[str] = []
    for key, heading in _PROBLEM_HEADINGS:
        entries = problems.get(key) or []
        if entries:
            problem_lines.append(f"{heading}:")
            problem_lines.extend(f"  - {entry}" for entry in entries)
    if problem_lines:
        sections.append(("Problems Found", problem_lines))

    actions = report.get("required_actions") or []
    if actions:
        sections.append(("Required Action", [f"- {action}" for action in actions]))

    evidence = report.get("compliance_evidence") or []
    if evidence:
        ev_lines: list[str] = []
        for item in evidence:
            ev_lines.append(f"- {_dash(item.get('rule_name'))}")
            ev_lines.append(
                f"    source: {_dash(item.get('source_document'))}; "
                f"SRO {_dash(item.get('sro_number'))}; page {_dash(item.get('page'))}; "
                f"authority {_dash(item.get('issuing_authority'))}; "
                f"status {_dash(item.get('validation_status'))}"
            )
        sections.append(("Compliance Evidence", ev_lines))

    workflow = report.get("workflow_summary") or []
    if workflow:
        sections.append(("Audit Workflow", [f"- {line}" for line in workflow]))

    return sections


def render_report(status: dict[str, Any], review: dict[str, Any] | None) -> list[str]:
    """Flatten the report sections into indented terminal lines."""
    lines: list[str] = []
    for heading, body in report_sections(status):
        lines.append(f"[{heading}]")
        for line in body:
            lines.append(f"  {line}" if line else "")
        lines.append("")
    return lines


def save_report(
    folder: Path,
    pair_key: str,
    status: dict[str, Any],
    review: dict[str, Any] | None,
) -> Path:
    reports_dir = folder / "audit_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^A-Za-z0-9._-]+", "_", pair_key)
    path = reports_dir / f"audit_report_{safe_key}.md"
    body = [f"# Customs Audit Report - {pair_key}", ""]
    for heading, section_lines in report_sections(status):
        body.append(f"## {heading}")
        body.append("")
        body.extend(section_lines)
        body.append("")
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def _prompt_for_review(
    review: dict[str, Any],
    reviewer_reference: str,
) -> dict[str, Any] | None:
    print("\n[HUMAN REVIEW REQUIRED]")
    print(f"Reason: {review.get('reason')}")
    disputed = review.get("disputed_fields") or []
    if disputed:
        print("Disputed or uncertain fields:")
        for value in disputed:
            print(f"  - {value}")
    print(
        "\nChoose an action:\n"
        "  1. Leave workflow pending\n"
        "  2. Accept manual review and finish\n"
        "  3. Correct one extracted value and rerun deterministic checks\n"
        "  4. Reject submission\n"
        "  5. Add a review note and finish"
    )
    while True:
        choice = input("Selection [1-5]: ").strip()
        if choice == "1":
            return None
        if choice == "2":
            note = input("Review note: ").strip()
            return build_review_payload(
                action="accept_manual_review",
                reviewer_reference=reviewer_reference,
                review_note=note or "Accepted from terminal review.",
                reason="Human reviewer accepted the manual-review result.",
            )
        if choice == "3":
            field_path = input(
                "Field path, for example invoice.line_items[1].quantity: "
            ).strip()
            original = input("Original extracted value: ")
            corrected = input("Corrected value: ")
            reason = input("Reason for correction: ").strip()
            source = input(
                "Source, for example commercial invoice page 1: "
            ).strip()
            return build_review_payload(
                action="correct_extracted_value",
                reviewer_reference=reviewer_reference,
                field_path=field_path,
                original_value=original,
                corrected_value=corrected,
                reason=reason or "Human correction from terminal review.",
                review_note="Corrected from the interactive terminal runner.",
                source=source or None,
            )
        if choice == "4":
            reason = input("Reason for rejection: ").strip()
            return build_review_payload(
                action="reject_submission",
                reviewer_reference=reviewer_reference,
                reason=reason or "Rejected from terminal review.",
                review_note="Submission rejected by the human reviewer.",
            )
        if choice == "5":
            note = input("Review note: ").strip()
            return build_review_payload(
                action="add_review_note",
                reviewer_reference=reviewer_reference,
                review_note=note or "Reviewed from the terminal.",
                reason="Human reviewer added a note.",
            )
        print("Please enter a number from 1 to 5.")


def _run_pair(
    api: CustomsApiTerminalClient,
    pair: DocumentPair,
    *,
    reviewer_reference: str,
    interactive_review: bool,
) -> dict[str, Any]:
    print(f"\n{'=' * 78}")
    print(f"CASE: {pair.key}")
    print(f"Invoice:      {pair.invoice_path}")
    print(f"Packing list: {pair.packing_list_path}")

    print("\n[1/6] Uploading commercial invoice...")
    invoice_upload = api.upload(pair.invoice_path)
    invoice_id = str(invoice_upload["document_id"])
    print(f"      Uploaded. Internal document ID: {invoice_id}")

    print("[2/6] Uploading packing list...")
    packing_upload = api.upload(pair.packing_list_path)
    packing_id = str(packing_upload["document_id"])
    print(f"      Uploaded. Internal document ID: {packing_id}")
    print("      IDs are carried automatically; you do not copy them.")

    defaults = load_workflow_defaults(pair)
    print("[3/6] Starting existing customs-audit workflow...")
    print(
        "      Server is running extraction → OCR when required → matching → "
        "deterministic compliance → Broker → Auditor → consensus."
    )
    workflow = api.start_workflow(
        invoice_document_id=invoice_id,
        packing_list_document_id=packing_id,
        defaults=defaults,
    )
    workflow_id = str(workflow["workflow_id"])
    print(f"      Workflow ID: {workflow_id} (carried automatically)")

    print("[4/6] Reading persisted workflow events...")
    events = api.get_events(workflow_id)
    _show_events(events)

    print("[5/6] Current result...")
    status = api.get_status(workflow_id)
    print(f"      Workflow status:      {status.get('status')}")
    print(f"      Deterministic status: {status.get('deterministic_status')}")

    review: dict[str, Any] | None = None
    if status.get("status") == "awaiting_human_review":
        review = api.get_review(workflow_id)
        if interactive_review:
            print("\n[6/6] A human decision is needed for this shipment.")
            review_payload = _prompt_for_review(review, reviewer_reference)
        else:
            review_payload = None
        if review_payload is not None:
            previous_event_count = len(events)
            print("\nSubmitting your decision and resuming the same workflow...")
            status = api.submit_review(workflow_id, review_payload)
            new_events = api.get_events(workflow_id)
            _show_events(new_events, start_index=previous_event_count)
        else:
            print("[6/6] No decision submitted; this shipment stays paused for a human.")
    else:
        print("[6/6] No human review needed - the audit finished on its own.")

    print(f"\n{'-' * 78}")
    print(f"AUDIT REPORT - {pair.key}")
    print(f"{'-' * 78}")
    for line in render_report(status, review):
        print(f"  {line}" if line else "")
    report_path = save_report(pair.invoice_path.parent, pair.key, status, review)
    print(f"\n  Full report saved to: {report_path}")
    return {
        "case": pair.key,
        "workflow_id": workflow_id,
        "status": status.get("status"),
        "deterministic_status": status.get("deterministic_status"),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Upload every synthetic invoice/packing-list pair in a folder and "
            "show the existing customs workflow in the terminal."
        )
    )
    parser.add_argument("folder", type=Path, help="Folder containing PDF pairs")
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="Running FastAPI base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--reviewer",
        default="terminal-reviewer",
        help="Reviewer reference stored in the audit trail",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300,
        help="HTTP timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the initial confirmation",
    )
    parser.add_argument(
        "--leave-review-pending",
        action="store_true",
        help="Print review tasks without prompting for a human decision",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        pairs, ignored = discover_document_pairs(args.folder)
        print(f"API: {args.api_url}")
        print(f"Folder: {args.folder.expanduser().resolve()}")
        print(f"Detected pairs: {len(pairs)}")
        for pair in pairs:
            print(
                f"  - {pair.key}: {pair.invoice_path.name} + "
                f"{pair.packing_list_path.name}"
            )
        if ignored:
            print("Ignored PDFs whose role was not identifiable:")
            for path in ignored:
                print(f"  - {path}")

        if not args.yes:
            answer = input("Run these cases through the API? [y/N]: ").strip()
            if answer.casefold() not in {"y", "yes"}:
                print("Cancelled. No files were uploaded.")
                return 0

        api = CustomsApiTerminalClient(args.api_url, args.timeout)
        try:
            health = api.health()
            print(f"API health: {health.get('status')}")
            summaries = [
                _run_pair(
                    api,
                    pair,
                    reviewer_reference=args.reviewer,
                    interactive_review=not args.leave_review_pending,
                )
                for pair in pairs
            ]
        finally:
            api.close()

        print(f"\n{'=' * 78}")
        print("ALL CASES - OVERVIEW")
        print(f"{'-' * 78}")
        print(f"  {'Case':<24}{'Workflow status':<24}{'Deterministic status'}")
        for summary in summaries:
            print(
                f"  {str(summary['case']):<24}"
                f"{str(summary['status']):<24}"
                f"{str(summary['deterministic_status'])}"
            )
        return 0
    except (TerminalRunnerError, OSError, httpx.HTTPError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
