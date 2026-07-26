"""Deterministic shipment-summary text for historical semantic search.

Built only from ``CustomsAuditWorkflow.final_report["user_report"]`` - the
same business-readable report already computed by
``customs_audit/report.py`` and persisted for every finalized workflow.
Nothing here embeds raw document text or re-derives a finding; it only
formats data the deterministic pipeline already produced.
"""

from __future__ import annotations

from typing import Any

from app.models.customs_audit import CustomsAuditWorkflow


def _distinct(values: list[Any]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        if value:
            seen.setdefault(str(value), None)
    return list(seen)


def build_shipment_summary_text(workflow: CustomsAuditWorkflow) -> str:
    """A short, deterministic plain-text summary of one finalized shipment."""
    final_report = workflow.final_report or {}
    user_report = final_report.get("user_report") or {}
    shipment = user_report.get("shipment_summary") or {}
    line_items = user_report.get("line_items") or []
    problems = user_report.get("problems") or {}

    products = _distinct([item.get("product_name") for item in line_items])
    pct_codes = _distinct([item.get("pct_code") for item in line_items])

    mismatches = problems.get("document_mismatches") or []
    calculation_errors = problems.get("calculation_errors") or []
    weight_discrepancies = [m for m in mismatches if "weight" in m.lower()]
    quantity_discrepancies = [m for m in mismatches if "quantity" in m.lower()]
    failed_checks = _distinct(
        [
            *mismatches,
            *calculation_errors,
            *(problems.get("regulatory_problems") or []),
        ]
    )

    status = final_report.get("deterministic_compliance_status") or workflow.deterministic_status or "unknown"

    lines = [
        f"Shipment ID: {workflow.id}",
        f"Exporter: {shipment.get('exporter') or 'unknown'}",
        f"Destination: {shipment.get('destination') or 'unknown'}",
        f"Products: {', '.join(products) or 'none recorded'}",
        f"PCT codes: {', '.join(pct_codes) or 'none recorded'}",
        f"Compliance status: {status}",
        f"Failed checks: {'; '.join(failed_checks) or 'none'}",
        f"Weight discrepancies: {'; '.join(weight_discrepancies) or 'none'}",
        f"Quantity discrepancies: {'; '.join(quantity_discrepancies) or 'none'}",
        f"Human review: {'required' if workflow.requires_human_review else 'not required'}",
    ]
    return "\n".join(lines)
