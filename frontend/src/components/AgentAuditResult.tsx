import {
  AlertTriangle,
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

function uniqueText(values: unknown[]): unknown[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const key = displayValue(value).trim().toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

interface ExplanationBlock {
  heading: string | null;
  paragraphs: string[];
  bullets: string[];
}

const explanationHeadings = new Set([
  "decision",
  "what was checked",
  "why this decision",
  "what this means",
  "next steps",
  "human review",
  "status",
]);

function isExplanationHeading(line: string): boolean {
  const normalized = line.replace(/[:*#]/g, "").trim().toLowerCase();
  return normalized.length <= 40 && explanationHeadings.has(normalized);
}

/**
 * Split the explanation into readable blocks.
 *
 * Both the deterministic template and the narrator emit short headings,
 * numbered steps, and bullet lines separated by newlines. Rendering the raw
 * string in one paragraph collapses all of that into an unreadable run-on, so
 * the structure is reconstructed here instead.
 */
function parseExplanation(text: string): ExplanationBlock[] {
  const blocks: ExplanationBlock[] = [];
  let current: ExplanationBlock | null = null;

  const push = () => {
    if (current && (current.paragraphs.length || current.bullets.length)) {
      blocks.push(current);
    }
    current = null;
  };

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;

    if (isExplanationHeading(line)) {
      push();
      current = {
        heading: line.replace(/[:*#]+$/, "").trim(),
        paragraphs: [],
        bullets: [],
      };
      continue;
    }

    if (!current) {
      current = { heading: null, paragraphs: [], bullets: [] };
    }

    const bullet = line.match(/^(?:[-•*]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      current.bullets.push(bullet[1].trim());
    } else {
      current.paragraphs.push(line);
    }
  }

  push();
  return blocks;
}

function ExplanationBody({ text }: { text: string }) {
  const blocks = parseExplanation(text);
  if (!blocks.length) {
    return <p>{text}</p>;
  }

  return (
    <div className="explanation-body">
      {blocks.map((block, index) => (
        <div className="explanation-block" key={`${block.heading ?? ""}-${index}`}>
          {block.heading ? <h4>{block.heading}</h4> : null}
          {block.paragraphs.map((paragraph, paragraphIndex) => (
            <p key={paragraphIndex}>{paragraph}</p>
          ))}
          {block.bullets.length ? (
            <ul className="explanation-bullets">
              {block.bullets.map((bullet, bulletIndex) => (
                <li key={bulletIndex}>{bullet}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function auditDecision(status: unknown): {
  title: string;
  tone: "success" | "warning" | "danger" | "info";
} {
  switch (String(status).toLowerCase()) {
    case "passed":
      return {
        title: "Ready based on the configured checks",
        tone: "success",
      };
    case "failed":
    case "rejected":
      return {
        title: "Not ready for customs submission",
        tone: "danger",
      };
    case "manual_review":
    case "awaiting_human_review":
      return {
        title: "Human review required before submission",
        tone: "warning",
      };
    default:
      return {
        title: "Audit result available",
        tone: "info",
      };
  }
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
  const checksPassed = uniqueText(asList(report.checks_passed));
  const requiredActions = uniqueText(asList(report.required_actions));
  const workflowSummary = uniqueText(asList(report.workflow_summary));
  const documentsToObtain = asList(report.documents_to_obtain)
    .map(asRecord)
    .filter((entry): entry is Record<string, unknown> => entry !== null);
  const uploadedResult = String(report.uploaded_document_result ?? "");
  const problems = asRecord(report.problems);
  const problemEntries = problems
    ? Object.entries(problems)
        // Documents still to be obtained get their own checklist below; they
        // are not defects in the invoice or packing list that were uploaded.
        .filter(
          ([category]) =>
            !(category === "missing_documents" && documentsToObtain.length),
        )
        .flatMap(([category, values]) =>
          uniqueText(asList(values)).map((value) => ({ category, value })),
        )
    : [];
  const status =
    report.overall_result ?? finalReport.deterministic_compliance_status;
  const decision = auditDecision(status);
  const explanationSource = String(
    report.explanation_source ?? finalReport.explanation_source ?? "",
  );
  const explanationLabel =
    explanationSource === "llm"
      ? "AI-assisted wording"
      : explanationSource
        ? "Deterministic template wording"
        : "Supplementary wording";

  return (
    <div className="agent-report stack-lg">
      <div className={`notice notice--${decision.tone}`}>
        <FileSearch aria-hidden="true" size={18} />
        <div>
          <strong>{decision.title}</strong>
          <p>
            {displayValue(report.overall_reason)} The invoice and packing list
            were processed; this status describes customs readiness.
          </p>
        </div>
      </div>

      {uploadedResult ? (
        <div
          className={`notice notice--${
            uploadedResult === "PASSED" ? "success" : "warning"
          }`}
        >
          <CheckCircle2 aria-hidden="true" size={18} />
          <div>
            <strong>
              {uploadedResult === "PASSED"
                ? "The uploaded invoice and packing list are sound"
                : "The uploaded documents need attention"}
            </strong>
            <p>
              {uploadedResult === "PASSED"
                ? "Every check that can be made on the two files you uploaded passed. Any status above that is not a pass is driven by customs paperwork that is still outstanding."
                : "At least one check on the two uploaded files did not hold up. The findings are listed below."}
            </p>
          </div>
        </div>
      ) : null}

      {documentsToObtain.length ? (
        <section className="report-section">
          <h3>Documents to obtain before submission</h3>
          <p className="section-intro">
            These are issued by outside bodies. They cannot be produced from the
            invoice or the packing list, so their absence is not a defect in the
            files that were uploaded.
          </p>
          <ul className="document-checklist">
            {documentsToObtain.map((document, index) => (
              <li key={index}>
                <div className="document-checklist__head">
                  <strong>{displayValue(document.document)}</strong>
                  <span
                    className={`requirement-tag requirement-tag--${
                      String(document.requirement ?? "")
                        .toLowerCase()
                        .startsWith("required")
                        ? "required"
                        : "conditional"
                    }`}
                  >
                    {displayValue(document.requirement)}
                  </span>
                </div>
                <ul className="plain-list">
                  {uniqueText(asList(document.reasons)).map((reason, reasonIndex) => (
                    <li key={reasonIndex}>{displayValue(reason)}</li>
                  ))}
                </ul>
                {asList(document.sources).length ? (
                  <small>
                    Source:{" "}
                    {asList(document.sources)
                      .map((source) => displayValue(source))
                      .join(" · ")}
                  </small>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {shipment ? (
        <section className="report-section">
          <h3>What was checked</h3>
          <p className="section-intro">
            These shipment details were extracted and supplied to the
            deterministic rule engine.
          </p>
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
          {checksPassed.length ? (
            <div className="verified-checks">
              <strong>Checks confirmed by the rule engine</strong>
              <ul className="plain-list">
                {checksPassed.slice(0, 8).map((check, index) => (
                  <li key={index}>
                    <CheckCircle2 aria-hidden="true" size={14} />
                    {displayValue(check)}
                  </li>
                ))}
              </ul>
              {checksPassed.length > 8 ? (
                <small>
                  {checksPassed.length - 8} additional passed checks are retained
                  in the audit record.
                </small>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {problemEntries.length ? (
        <section className="report-section">
          <h3>Why this decision was reached</h3>
          <ul className="decision-list decision-list--problems">
            {problemEntries.map(({ category, value }, index) => (
              <li key={`${category}-${index}`}>
                <AlertTriangle aria-hidden="true" size={16} />
                <div>
                  <strong>{labelize(category)}</strong>
                  <p>{displayValue(value)}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {requiredActions.length ? (
        <section className="report-section">
          <h3>What to do next</h3>
          <ol className="action-list">
            {requiredActions.map((action, index) => (
              <li key={index}>{displayValue(action)}</li>
            ))}
          </ol>
        </section>
      ) : null}

      {explanation ? (
        <section className="report-section explanation-section">
          <div className="explanation-section__heading">
            <div>
              <h3>Plain-language explanation</h3>
              <p>
                A full walkthrough of the result you can read out as-is. It
                explains the wording above but does not change the
                deterministic decision.
              </p>
            </div>
            <span className="explanation-label">{explanationLabel}</span>
          </div>
          <ExplanationBody text={displayValue(explanation)} />
        </section>
      ) : null}

      {workflowSummary.length ? (
        <details className="technical-details">
          <summary>Audit workflow details</summary>
          <ul className="plain-list">
            {workflowSummary.map((entry, index) => (
              <li key={index}>{displayValue(entry)}</li>
            ))}
          </ul>
        </details>
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
