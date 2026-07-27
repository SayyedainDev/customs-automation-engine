import {
  CheckCircle2,
  Clock3,
  FileSearch,
  UserCheck,
  Workflow,
  XCircle,
} from "lucide-react";
import type {
  AuditEvent,
  ReviewTaskResponse,
  WorkflowStatusResponse,
} from "../api/types";
import { displayValue, formatDate, labelize } from "../lib/format";
import { StatusBadge } from "./StatusBadge";

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function Report({
  finalReport,
}: {
  finalReport: Record<string, unknown>;
}) {
  const userReport = asRecord(finalReport.user_report);
  const report = userReport ?? finalReport;
  const shipment = asRecord(report.shipment_summary);
  const explanation = report.explanation ?? finalReport.explanation;
  const requiredActions = asList(report.required_actions);
  const workflowSummary = asList(report.workflow_summary);
  const problems = asRecord(report.problems);
  const problemEntries = problems
    ? Object.entries(problems).flatMap(([category, values]) =>
        asList(values).map((value) => ({ category, value })),
      )
    : [];

  return (
    <div className="agent-report stack-lg">
      <div className="notice notice--info">
        <FileSearch aria-hidden="true" size={18} />
        <div>
          <strong>
            {displayValue(
              report.overall_result ??
                finalReport.deterministic_compliance_status,
            )}
          </strong>
          <p>{displayValue(report.overall_reason)}</p>
        </div>
      </div>

      {explanation ? (
        <section className="report-section">
          <h3>Explanation</h3>
          <p>{displayValue(explanation)}</p>
          {report.explanation_source ? (
            <small>Source: {labelize(String(report.explanation_source))}</small>
          ) : null}
        </section>
      ) : null}

      {shipment ? (
        <section className="report-section">
          <h3>Shipment summary</h3>
          <dl className="metadata-grid metadata-grid--compact">
            {Object.entries(shipment)
              .filter(([, value]) => value !== null && value !== undefined)
              .map(([key, value]) => (
                <div className="metadata-item" key={key}>
                  <dt>{labelize(key)}</dt>
                  <dd>{displayValue(value)}</dd>
                </div>
              ))}
          </dl>
        </section>
      ) : null}

      {problemEntries.length ? (
        <section className="report-section">
          <h3>Problems identified</h3>
          <ul className="plain-list">
            {problemEntries.map(({ category, value }, index) => (
              <li key={`${category}-${index}`}>
                <strong>{labelize(category)}:</strong> {displayValue(value)}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {requiredActions.length ? (
        <section className="report-section">
          <h3>Required actions</h3>
          <ol className="plain-list">
            {requiredActions.map((action, index) => (
              <li key={index}>{displayValue(action)}</li>
            ))}
          </ol>
        </section>
      ) : null}

      {workflowSummary.length ? (
        <section className="report-section">
          <h3>Workflow summary</h3>
          <ul className="plain-list">
            {workflowSummary.map((entry, index) => (
              <li key={index}>{displayValue(entry)}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

export function AgentAuditResult({
  workflow,
  reviewTask,
  events,
  busy,
  onDecision,
}: {
  workflow: WorkflowStatusResponse;
  reviewTask: ReviewTaskResponse | null;
  events: AuditEvent[];
  busy: boolean;
  onDecision: (action: "accept_manual_review" | "reject_submission") => void;
}) {
  return (
    <section className="panel agent-audit" aria-labelledby="agent-audit-heading">
      <div className="panel__header">
        <div>
          <p className="eyebrow">Broker and auditor workflow</p>
          <h2 id="agent-audit-heading">Agent audit</h2>
          <p>
            Workflow {workflow.workflow_id.slice(0, 8)}… ·{" "}
            {workflow.current_node
              ? labelize(workflow.current_node)
              : "No active node"}
          </p>
        </div>
        <StatusBadge status={workflow.status} />
      </div>

      <div className="panel__body stack-lg">
        {["created", "running", "resuming"].includes(workflow.status) ? (
          <div className="notice notice--info" role="status">
            <Clock3 aria-hidden="true" size={18} />
            <div>
              <strong>Audit is running</strong>
              <p>The page will refresh this workflow automatically.</p>
            </div>
          </div>
        ) : null}

        {reviewTask ? (
          <div className="notice notice--warning">
            <UserCheck aria-hidden="true" size={19} />
            <div>
              <strong>Human decision required</strong>
              <p>{reviewTask.reason}</p>

              {reviewTask.disputed_fields.length ? (
                <div>
                  <strong>Disputed fields</strong>
                  <ul className="plain-list">
                    {reviewTask.disputed_fields.map((field, index) => (
                      <li key={index}>{displayValue(field)}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="action-row">
                <button
                  className="button button--primary"
                  type="button"
                  disabled={busy}
                  onClick={() => onDecision("accept_manual_review")}
                >
                  <CheckCircle2 aria-hidden="true" size={16} />
                  {busy ? "Submitting…" : "Accept review and continue"}
                </button>
                <button
                  className="button button--danger"
                  type="button"
                  disabled={busy}
                  onClick={() => onDecision("reject_submission")}
                >
                  <XCircle aria-hidden="true" size={16} />
                  Reject submission
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {workflow.final_report ? (
          <Report finalReport={workflow.final_report} />
        ) : null}

        {events.length ? (
          <section aria-labelledby="audit-events-heading">
            <div className="section-heading">
              <div>
                <h3 id="audit-events-heading">Audit history</h3>
                <p>System, Broker, Auditor, and human actions in time order.</p>
              </div>
            </div>
            <ol className="audit-list">
              {events.map((event, index) => (
                <li
                  className="audit-event"
                  key={`${event.created_at ?? "event"}-${index}`}
                >
                  <time>{formatDate(event.created_at)}</time>
                  <div>
                    <h3>
                      {event.actor_type === "human" ? (
                        <UserCheck aria-hidden="true" size={14} />
                      ) : (
                        <Workflow aria-hidden="true" size={14} />
                      )}{" "}
                      {labelize(event.event_type)}
                    </h3>
                    <p>
                      {labelize(event.actor_type)}
                      {event.node_name ? ` · ${labelize(event.node_name)}` : ""}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </section>
        ) : null}
      </div>
    </section>
  );
}
