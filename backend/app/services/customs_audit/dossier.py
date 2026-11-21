"""Exportable Pre-Submission Audit Dossier builder and PyMuPDF PDF renderer."""

from __future__ import annotations

import html
import io
from typing import Any

import pymupdf

from app.services.customs_audit.state import utcnow_iso


def build_audit_dossier_payload(
    workflow_status: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble a complete JSON-serializable audit dossier for one workflow."""
    final_report = workflow_status.get("final_report") or {}
    user_report = final_report.get("user_report") or {}
    report = user_report if isinstance(user_report, dict) and user_report else final_report

    explanation = report.get("explanation") or final_report.get("explanation")
    explanation_source = (
        report.get("explanation_source") or final_report.get("explanation_source")
    )

    event_chain = [
        {
            "sequence": idx,
            "created_at": ev.get("created_at"),
            "actor_type": ev.get("actor_type"),
            "actor_reference": ev.get("actor_reference"),
            "node_name": ev.get("node_name"),
            "event_type": ev.get("event_type"),
            "previous_state_hash": ev.get("previous_state_hash"),
            "new_state_hash": ev.get("new_state_hash"),
        }
        for idx, ev in enumerate(events, start=1)
    ]

    return {
        "dossier_version": "1.0",
        "generated_at": utcnow_iso(),
        "workflow": {
            "workflow_id": workflow_status.get("workflow_id"),
            "thread_id": workflow_status.get("thread_id"),
            "status": workflow_status.get("status"),
            "deterministic_status": workflow_status.get("deterministic_status"),
            "rule_data_version": workflow_status.get("rule_data_version"),
            "vector_index_version": workflow_status.get("vector_index_version"),
            "created_at": workflow_status.get("created_at"),
            "completed_at": workflow_status.get("completed_at"),
        },
        "verdict": {
            "overall_result": report.get("overall_result")
            or workflow_status.get("deterministic_status"),
            "overall_reason": report.get("overall_reason"),
            "uploaded_document_result": report.get("uploaded_document_result"),
            "executive_summary": report.get("executive_summary"),
            "explanation": explanation,
            "explanation_source": explanation_source,
        },
        "shipment_summary": report.get("shipment_summary") or {},
        "line_items": report.get("line_items") or [],
        "duty_estimation_summary": report.get("duty_estimation_summary"),
        "documents_to_obtain": report.get("documents_to_obtain") or [],
        "supporting_documents": report.get("supporting_documents") or [],
        "checks_passed": report.get("checks_passed") or [],
        "problems": report.get("problems") or {},
        "required_actions": report.get("required_actions") or [],
        "regulatory_evidence": report.get("regulatory_evidence") or [],
        "audit_revision_history": report.get("audit_revision_history") or [],
        "reviewer_signoff": {
            "human_review_summary": report.get("human_review_summary") or [],
            "human_disposition": final_report.get("human_disposition"),
            "human_review_decision": final_report.get("human_review_decision"),
        },
        "audit_event_chain": event_chain,
    }


def _e(value: Any, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return html.escape(str(value))


def _render_kv_rows(items: list[tuple[str, Any]]) -> str:
    cells = "".join(
        f"<div class='kv-item'><span class='kv-k'>{_e(k)}</span>"
        f"<span class='kv-v'>{_e(v)}</span></div>"
        for k, v in items
    )
    return f"<div class='kv-grid'>{cells}</div>"


def render_audit_dossier_pdf(dossier: dict[str, Any]) -> bytes:
    """Render an exportable A4 Pre-Submission Audit Packet PDF from a dossier payload."""
    wf = dossier.get("workflow") or {}
    verdict = dossier.get("verdict") or {}
    shipment = dossier.get("shipment_summary") or {}
    line_items = dossier.get("line_items") or []
    duty_summary = dossier.get("duty_estimation_summary")
    docs_to_obtain = dossier.get("documents_to_obtain") or []
    supporting_docs = dossier.get("supporting_documents") or []
    reg_evidence = dossier.get("regulatory_evidence") or []
    revisions = dossier.get("audit_revision_history") or []
    signoff = dossier.get("reviewer_signoff") or {}
    human_rows = signoff.get("human_review_summary") or []
    chain = dossier.get("audit_event_chain") or []

    wf_id = str(wf.get("workflow_id") or "")
    short_id = wf_id[:8] if wf_id else "audit"
    overall_result = str(verdict.get("overall_result") or wf.get("status") or "UNKNOWN")

    line_rows_html = ""
    for row in line_items:
        est = row.get("duty_estimate") or {}
        bd = est.get("breakdown") or {}
        line_rows_html += (
            "<tr>"
            f"<td>{_e(row.get('line_number'))}</td>"
            f"<td>{_e(row.get('product_name'))}</td>"
            f"<td>{_e(row.get('pct_code'))}</td>"
            f"<td>{_e(row.get('quantity'))} {_e(row.get('unit'), '')}</td>"
            f"<td>{_e(row.get('line_total'))}</td>"
            f"<td>{_e(bd.get('customs_duty_pct'))}%</td>"
            f"<td>{_e(bd.get('total_duties_and_taxes'))}</td>"
            f"<td>{_e(bd.get('total_landed_cost'))}</td>"
            "</tr>"
        )
    if not line_rows_html:
        line_rows_html = "<tr><td colspan='8'>No matched line items recorded.</td></tr>"

    duty_section_html = ""
    if isinstance(duty_summary, dict):
        cur = str(duty_summary.get("currency") or "")
        suffix = f" {cur}" if cur else ""
        duty_section_html = (
            "<h2>4. Estimated Statutory Duties &amp; Landed Cost (2026–27)</h2>"
            + _render_kv_rows(
                [
                    ("Lines Estimated", f"{duty_summary.get('lines_estimated', 0)} / {duty_summary.get('total_lines', 0)}"),
                    ("Assessed Value (V)", f"{duty_summary.get('total_assessed_value')}{suffix}"),
                    ("Customs Duty (CD)", f"{duty_summary.get('total_customs_duty')}{suffix}"),
                    ("Additional CD (ACD)", f"{duty_summary.get('total_additional_customs_duty')}{suffix}"),
                    ("Sales Tax + VAT", f"{duty_summary.get('total_sales_tax')} / {duty_summary.get('total_value_addition_tax')}{suffix}"),
                    ("Advance Income Tax", f"{duty_summary.get('total_advance_income_tax')}{suffix}"),
                    ("Total Duties &amp; Taxes", f"{duty_summary.get('total_duties_and_taxes')}{suffix} ({duty_summary.get('effective_rate_pct')}%)"),
                    ("Total Landed Cost", f"{duty_summary.get('total_landed_cost')}{suffix}"),
                ]
            )
            + f"<p class='note'>{_e(duty_summary.get('basis_note'))}</p>"
        )
    else:
        duty_section_html = (
            "<h2>4. Estimated Statutory Duties &amp; Landed Cost</h2>"
            "<p class='note'>No confirmed line-item duty estimates available for this shipment.</p>"
        )

    docs_html = ""
    if docs_to_obtain:
        docs_html += "<ul>" + "".join(
            f"<li><strong>{_e(d.get('document'))}</strong> ({_e(d.get('requirement'))})</li>"
            for d in docs_to_obtain
        ) + "</ul>"
    else:
        docs_html += "<p class='note'>No outstanding supporting documents flagged.</p>"
    if supporting_docs:
        docs_html += (
            "<table><thead><tr><th>Document Type</th><th>Uploaded</th><th>Doc Number</th><th>Result</th></tr></thead><tbody>"
            + "".join(
                "<tr>"
                f"<td>{_e(sd.get('required_document_type'))}</td>"
                f"<td>{_e(sd.get('uploaded'))}</td>"
                f"<td>{_e(sd.get('document_number'))}</td>"
                f"<td>{_e(sd.get('content_result'))}</td>"
                "</tr>"
                for sd in supporting_docs
            )
            + "</tbody></table>"
        )

    reg_rows_html = ""
    for reg in reg_evidence:
        primary = reg.get("citations") or []
        c0 = primary[0] if primary and isinstance(primary[0], dict) else {}
        reg_rows_html += (
            "<tr>"
            f"<td>{_e(reg.get('requirement'))}</td>"
            f"<td>{_e(reg.get('status'))}</td>"
            f"<td>{_e(c0.get('source_document'))}</td>"
            f"<td>{_e(c0.get('sro_number') or c0.get('section'))}</td>"
            f"<td>{_e(c0.get('page_number'))}</td>"
            f"<td>{_e(reg.get('evidence_status'))}</td>"
            "</tr>"
        )
    if not reg_rows_html:
        reg_rows_html = "<tr><td colspan='6'>No regulatory citations recorded.</td></tr>"

    rev_html = ""
    if revisions:
        rev_html += "<ul>" + "".join(
            f"<li>Revision {_e(r.get('revision_number'))}: <strong>{_e(r.get('status_label'))}</strong>"
            + (f" ({_e(r.get('triggered_by'))})" if r.get("triggered_by") else "")
            + "</li>"
            for r in revisions
        ) + "</ul>"
    if human_rows:
        rev_html += "<ul>" + "".join(
            f"<li><strong>{_e(h.get('field_label'))}</strong>: "
            f"{_e(h.get('original_value'))} -&gt; <strong>{_e(h.get('corrected_value'))}</strong> "
            f"(Reviewer: {_e(h.get('reviewer_reference'))}; Reason: {_e(h.get('reason'))})</li>"
            for h in human_rows
        ) + "</ul>"
    elif signoff.get("human_disposition"):
        rev_html += f"<p class='note'>Disposition: {_e(signoff.get('human_disposition'))}</p>"
    if not rev_html:
        rev_html = "<p class='note'>No human revisions recorded.</p>"

    chain_rows_html = ""
    for ev in chain:
        state_hash = str(ev.get("new_state_hash") or "")
        short_hash = state_hash[:16] + ("..." if len(state_hash) > 16 else "")
        chain_rows_html += (
            "<tr>"
            f"<td>{_e(ev.get('sequence'))}</td>"
            f"<td>{_e(ev.get('created_at'))}</td>"
            f"<td>{_e(ev.get('actor_type'))}</td>"
            f"<td>{_e(ev.get('node_name'))}</td>"
            f"<td>{_e(ev.get('event_type'))}</td>"
            f"<td><code>{_e(short_hash)}</code></td>"
            "</tr>"
        )
    if not chain_rows_html:
        chain_rows_html = "<tr><td colspan='6'>No audit events recorded.</td></tr>"

    html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  body {{ font-family: Helvetica, Arial, sans-serif; font-size: 9.5pt; color: #1f2937; line-height: 1.4; }}
  .banner {{ background: #1e293b; color: #f8fafc; padding: 12px 14px; border-radius: 4px; margin-bottom: 12px; }}
  .banner h1 {{ margin: 0 0 4px 0; font-size: 14pt; color: #ffffff; }}
  .banner p {{ margin: 2px 0; font-size: 8.5pt; color: #cbd5e1; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 3px; background: #0f172a; color: #facc15; font-weight: bold; font-size: 9pt; }}
  h2 {{ font-size: 11pt; color: #0f172a; border-bottom: 1px solid #cbd5e1; padding-bottom: 3px; margin: 14px 0 6px 0; }}
  p.note {{ font-size: 8.5pt; color: #475569; margin: 4px 0 8px 0; }}
  .kv-grid {{ width: 100%; margin: 6px 0 10px 0; }}
  .kv-item {{ display: inline-block; width: 47%; padding: 4px 6px; margin: 2px 1% 2px 0; background: #f8fafc; border: 1px solid #e2e8f0; vertical-align: top; }}
  .kv-k {{ display: block; font-size: 7.5pt; color: #64748b; text-transform: uppercase; }}
  .kv-v {{ font-size: 9pt; font-weight: bold; color: #0f172a; }}
  table {{ width: 100%; border-collapse: collapse; margin: 6px 0 10px 0; font-size: 8.2pt; }}
  th, td {{ border: 1px solid #cbd5e1; padding: 4px 5px; text-align: left; vertical-align: top; }}
  th {{ background: #f1f5f9; font-weight: bold; color: #0f172a; }}
  code {{ font-family: Courier, monospace; font-size: 7.8pt; }}
</style>
</head>
<body>
  <div class="banner">
    <h1>CACE Pre-Submission Customs Audit Dossier</h1>
    <p><span class="badge">VERDICT: {_e(overall_result)}</span> &nbsp; Workflow: <code>{_e(wf_id)}</code></p>
    <p>Rule Data Version: {_e(wf.get('rule_data_version'))} &nbsp;|&nbsp; Vector Index: {_e(wf.get('vector_index_version'))} &nbsp;|&nbsp; Generated: {_e(dossier.get('generated_at'))}</p>
  </div>

  <h2>1. Frozen Deterministic Verdict &amp; Explanation</h2>
  <p><strong>Overall Result:</strong> {_e(overall_result)} — {_e(verdict.get('overall_reason'))}</p>
  <p><strong>Uploaded Document Soundness:</strong> {_e(verdict.get('uploaded_document_result'))}</p>
  <p>{_e(verdict.get('explanation'), 'No narrative explanation generated yet.')}</p>

  <h2>2. Shipment Summary</h2>
  {_render_kv_rows([
      ('Exporter', shipment.get('exporter')),
      ('Buyer', shipment.get('buyer')),
      ('Invoice Number', shipment.get('invoice_number')),
      ('Invoice Date', shipment.get('invoice_date')),
      ('Destination Country', shipment.get('destination')),
      ('Total Invoice Value', f"{_e(shipment.get('total_invoice_value'))} {_e(shipment.get('currency'), '')}".strip()),
      ('Net / Gross Weight', f"{_e(shipment.get('declared_net_weight'))} / {_e(shipment.get('declared_gross_weight'))}"),
      ('Total Packages', shipment.get('total_packages')),
  ])}

  <h2>3. Matched Line Items &amp; Per-Line Duty Estimates</h2>
  <table>
    <thead>
      <tr><th>Line</th><th>Product</th><th>PCT Code</th><th>Quantity</th><th>Line Value</th><th>CD %</th><th>Est. Duties</th><th>Est. Landed</th></tr>
    </thead>
    <tbody>{line_rows_html}</tbody>
  </table>

  {duty_section_html}

  <h2>5. Pre-Submission Documents &amp; Supporting Verification</h2>
  {docs_html}

  <h2>6. Regulatory SRO Citations</h2>
  <table>
    <thead>
      <tr><th>Requirement</th><th>Status</th><th>Source Document</th><th>SRO / Section</th><th>Page</th><th>Evidence</th></tr>
    </thead>
    <tbody>{reg_rows_html}</tbody>
  </table>

  <h2>7. Immutable Revisions &amp; Reviewer Sign-Off</h2>
  {rev_html}

  <h2>8. Cryptographic Audit Event Trail (SHA-256)</h2>
  <table>
    <thead>
      <tr><th>#</th><th>Timestamp</th><th>Actor</th><th>Node</th><th>Event</th><th>State Hash</th></tr>
    </thead>
    <tbody>{chain_rows_html}</tbody>
  </table>
</body>
</html>"""

    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    story = pymupdf.Story(html=html_doc)
    mediabox = pymupdf.paper_rect("a4")
    content_rect = mediabox + (36, 36, -36, -46)
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(content_rect)
        story.draw(device, None)
        writer.end_page()
    writer.close()

    stamped = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    try:
        total_pages = stamped.page_count
        for idx in range(total_pages):
            pg = stamped.load_page(idx)
            footer_rect = pymupdf.Rect(
                36,
                pg.rect.height - 30,
                pg.rect.width - 36,
                pg.rect.height - 12,
            )
            pg.insert_textbox(
                footer_rect,
                f"CACE Pre-Submission Audit Packet · Workflow {short_id} · Page {idx + 1} of {total_pages}",
                fontsize=8,
                color=(0.38, 0.42, 0.48),
                align=pymupdf.TEXT_ALIGN_RIGHT,
            )
        return bytes(stamped.tobytes())
    finally:
        stamped.close()
