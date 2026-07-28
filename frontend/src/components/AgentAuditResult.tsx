import { useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Clock3,
  FileSearch,
  FileText,
  History,
  UserCheck,
  Workflow,
  XCircle,
} from "lucide-react";
import type {
  AuditEvent,
  DisputedFieldDetail,
  ReviewTaskResponse,
  WorkflowStatusResponse,
} from "../api/types";
import { displayValue, formatDate, labelize } from "../lib/format";
import { StatusBadge } from "./StatusBadge";
import { AssistantPanel } from "./AssistantPanel";

type CorrectionAction = "confirm_extracted_value" | "correct_extracted_value";

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
  "what the system checked",
  "why this decision",
  "what this means",
  "regulatory evidence",
  "next steps",
  "what to do next",
  "limitations",
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

function evidenceStatusCopy(status: unknown): { label: string; tone: string } {
  switch (String(status)) {
    case "evidence_verified":
      return { label: "Evidence found", tone: "success" };
    case "evidence_partial":
      return { label: "Partial evidence", tone: "warning" };
    case "evidence_conflicting":
      return { label: "Evidence conflicting", tone: "warning" };
    default:
      return { label: "No evidence found", tone: "neutral" };
  }
}

function CitationCard({ citation }: { citation: Record<string, unknown> }) {
  const isCurated = citation.source_kind === "curated";
  return (
    <li className="citation-card">
      <div className="citation-card__head">
        <BookOpen aria-hidden="true" size={14} />
        <strong>{displayValue(citation.source_title)}</strong>
        {citation.page_number ? (
          <span className="muted">page {displayValue(citation.page_number)}</span>
        ) : null}
      </div>
      <div className="citation-card__provenance">
        <span className={`source-kind-chip source-kind-chip--${isCurated ? "curated" : "official"}`}>
          {isCurated ? "Curated rule record" : "Official government source"}
        </span>
        {isCurated ? (
          <span className="muted">derived from reviewed PSW/TIPP and Export Policy sources</span>
        ) : citation.issuing_authority ? (
          <span className="muted">{displayValue(citation.issuing_authority)}</span>
        ) : null}
      </div>
      {citation.section ? (
        <p className="citation-card__section">{displayValue(citation.section)}</p>
      ) : null}
      {citation.snippet ? (
        <p className="citation-card__snippet">“{displayValue(citation.snippet)}”</p>
      ) : null}
      <details className="citation-card__technical">
        <summary>Retrieval details</summary>
        <div className="citation-card__scores">
          {citation.retrieval_score !== undefined && citation.retrieval_score !== null ? (
            <span>retrieval {Number(citation.retrieval_score).toFixed(3)}</span>
          ) : null}
          {citation.rerank_score !== undefined && citation.rerank_score !== null ? (
            <span>rerank {Number(citation.rerank_score).toFixed(3)}</span>
          ) : null}
          {citation.validation_status ? (
            <span>source status: {displayValue(citation.validation_status)}</span>
          ) : null}
        </div>
      </details>
    </li>
  );
}

/**
 * One regulatory requirement with its retrieved citations, shown regardless
 * of whether the requirement passed or failed - a passed requirement is
 * backed by the same kind of citation as a failed one, and a requirement
 * with no retrievable evidence says so plainly instead of showing nothing.
 * At most two citations are shown expanded; any further matches the
 * retriever found stay available under a collapsed "additional sources"
 * detail rather than repeating a full card for every near-duplicate.
 */
function RegulatoryEvidenceCard({
  entry,
}: {
  entry: Record<string, unknown>;
}) {
  const citations = asList(entry.citations)
    .map(asRecord)
    .filter((citation): citation is Record<string, unknown> => citation !== null);
  const additionalCitations = asList(entry.additional_citations)
    .map(asRecord)
    .filter((citation): citation is Record<string, unknown> => citation !== null);
  const evidenceStatus = evidenceStatusCopy(entry.evidence_status);
  const checkStatus = String(entry.status ?? "");

  return (
    <details className="evidence-card">
      <summary>
        <span className="evidence-card__requirement">
          {displayValue(entry.requirement)}
        </span>
        <span className={`status-chip status-chip--${checkStatus.toLowerCase()}`}>
          {checkStatus}
        </span>
        <span className={`evidence-chip evidence-chip--${evidenceStatus.tone}`}>
          {evidenceStatus.label}
        </span>
      </summary>
      {citations.length ? (
        <>
          <ul className="citation-list">
            {citations.map((citation, index) => (
              <CitationCard key={index} citation={citation} />
            ))}
          </ul>
          {additionalCitations.length ? (
            <details className="evidence-card__additional">
              <summary>{additionalCitations.length} additional source(s) found</summary>
              <ul className="citation-list">
                {additionalCitations.map((citation, index) => (
                  <CitationCard key={index} citation={citation} />
                ))}
              </ul>
            </details>
          ) : null}
        </>
      ) : (
        <p className="evidence-empty">
          Relevant regulatory evidence was not available in the indexed corpus.
          Nothing was invented to fill the gap.
        </p>
      )}
    </details>
  );
}

/** A system-scope check ("PCT 61091000 is supported by this CACE
 * prototype") is a statement about the software's own configured coverage,
 * never a regulatory citation - shown plainly, with no source card. */
function SystemScopeRow({ entry }: { entry: Record<string, unknown> }) {
  const checkStatus = String(entry.status ?? "");
  return (
    <li className="system-scope-row">
      <div className="system-scope-row__head">
        <strong>{displayValue(entry.requirement)}</strong>
        <span className={`status-chip status-chip--${checkStatus.toLowerCase()}`}>
          {checkStatus}
        </span>
      </div>
      <p className="muted">{displayValue(entry.statement)}</p>
    </li>
  );
}

function DocumentEvidenceRow({ entry }: { entry: Record<string, unknown> }) {
  const values = asList(entry.evidence)
    .map(asRecord)
    .filter((value): value is Record<string, unknown> => value !== null);
  return (
    <li className="document-evidence-row">
      <div className="document-evidence-row__head">
        <FileText aria-hidden="true" size={14} />
        <strong>{displayValue(entry.check_name)}</strong>
        <span className={`status-chip status-chip--${String(entry.status ?? "").toLowerCase()}`}>
          {displayValue(entry.status)}
        </span>
      </div>
      <div className="document-evidence-row__values">
        {values.map((value, index) => (
          <span key={index} className="document-evidence-value">
            {displayValue(value.document_type)}
            {value.page_number ? ` p.${displayValue(value.page_number)}` : ""}:{" "}
            <strong>{displayValue(value.extracted_value)}</strong>
            {value.extraction_method === "human_review" ? (
              <span className="human-confirmed-badge">Human-confirmed</span>
            ) : null}
          </span>
        ))}
      </div>
    </li>
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

//: Plain labels for a rerun check - never a raw check_id in the main UI
//: (only inside the collapsed technical details below).
const CHECK_LABELS: Record<string, string> = {
  item_quantity_match: "Quantity",
  item_net_weight_match: "Net weight",
  item_gross_weight_match: "Gross weight",
  item_pct_code_match: "PCT code",
  positive_quantity: "Quantity",
  positive_unit_price: "Unit price",
  item_line_calculation: "Line total calculation",
  invoice_line_calculation: "Line total calculation",
  sum_line_totals_match_invoice_total: "Invoice total",
  invoice_total_consistency: "Invoice total",
  invoice_net_weight_total: "Invoice net weight total",
  invoice_gross_weight_total: "Invoice gross weight total",
  packing_net_weight_total: "Packing list net weight total",
  packing_gross_weight_total: "Packing list gross weight total",
  weight_consistency: "Weight consistency",
  mvp_pct_support: "Supported product code",
};

function checkLabel(checkId: string): string {
  if (CHECK_LABELS[checkId]) return CHECK_LABELS[checkId];
  if (checkId.startsWith("xr_")) return "Regulatory requirement";
  return labelize(checkId);
}

/**
 * Lets a reviewer confirm or correct exactly one of the disputed values the
 * backend already surfaced on the open review task - never a free-text
 * field path. Submitting always targets one of `disputed_field_details`
 * (or, for a custom value, one of those same fields), so the backend's own
 * field-to-check dependency map is always what decides what gets rerun.
 */
function CorrectionPanel({
  reviewTask,
  busy,
  onCorrection,
}: {
  reviewTask: ReviewTaskResponse;
  busy: boolean;
  onCorrection: (
    action: CorrectionAction,
    fieldPath: string,
    originalValue: unknown,
    correctedValue: unknown,
    reason: string,
  ) => void;
}) {
  const details: DisputedFieldDetail[] = reviewTask.disputed_field_details;
  const [selectedValue, setSelectedValue] = useState<string>(
    details.length ? String(details[0].value) : "",
  );
  const [useCustom, setUseCustom] = useState(false);
  const [customTargetIndex, setCustomTargetIndex] = useState(0);
  const [customValue, setCustomValue] = useState("");
  const [reason, setReason] = useState("");

  if (!details.length) return null;

  const canSubmit =
    reason.trim().length > 0 && (!useCustom || customValue.trim().length > 0);

  function handleSubmit() {
    const reasonText = reason.trim();
    if (!reasonText) return;
    if (useCustom) {
      const target = details[customTargetIndex];
      const value = customValue.trim();
      if (!value) return;
      const action: CorrectionAction =
        String(target.value) === value
          ? "confirm_extracted_value"
          : "correct_extracted_value";
      onCorrection(action, target.field_path, target.value, value, reasonText);
      return;
    }
    const mismatched = details.find((d) => String(d.value) !== selectedValue);
    if (mismatched) {
      onCorrection(
        "correct_extracted_value",
        mismatched.field_path,
        mismatched.value,
        selectedValue,
        reasonText,
      );
    } else if (details.length === 1) {
      onCorrection(
        "confirm_extracted_value",
        details[0].field_path,
        details[0].value,
        selectedValue,
        reasonText,
      );
    }
  }

  return (
    <div className="correction-panel">
      {reviewTask.plain_language_question ? (
        <p className="correction-panel__question">
          {reviewTask.plain_language_question}
        </p>
      ) : null}

      <div className="correction-panel__options">
        {details.map((detail, index) => (
          <label className="correction-panel__option" key={index}>
            <input
              type="radio"
              name="correction-value"
              checked={!useCustom && selectedValue === String(detail.value)}
              onChange={() => {
                setUseCustom(false);
                setSelectedValue(String(detail.value));
              }}
            />
            <span>
              Use the {labelize(detail.document_type)} value:{" "}
              <strong>{displayValue(detail.value)}</strong>
              {detail.page ? ` (page ${detail.page})` : ""}
            </span>
          </label>
        ))}
        <label className="correction-panel__option">
          <input
            type="radio"
            name="correction-value"
            checked={useCustom}
            onChange={() => setUseCustom(true)}
          />
          <span>Enter a different value</span>
        </label>
        {useCustom ? (
          <div className="correction-panel__custom">
            <select
              value={customTargetIndex}
              onChange={(event) => setCustomTargetIndex(Number(event.target.value))}
            >
              {details.map((detail, index) => (
                <option key={index} value={index}>
                  Correct the {labelize(detail.document_type)} value
                </option>
              ))}
            </select>
            <input
              type="text"
              value={customValue}
              onChange={(event) => setCustomValue(event.target.value)}
              placeholder="Corrected value"
            />
          </div>
        ) : null}
      </div>

      <label className="correction-panel__reason">
        Reason
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="e.g. Confirmed against the corrected packing list"
          rows={2}
        />
      </label>

      {reviewTask.affected_check_ids.length ? (
        <p className="muted">
          This will re-check:{" "}
          {Array.from(new Set(reviewTask.affected_check_ids.map(checkLabel))).join(
            ", ",
          )}
          .
        </p>
      ) : null}

      <button
        className="button button--primary"
        type="button"
        disabled={busy || !canSubmit}
        onClick={handleSubmit}
      >
        <CheckCircle2 aria-hidden="true" size={16} />
        {busy ? "Submitting…" : "Submit"}
      </button>

      <details className="technical-details">
        <summary>Technical details</summary>
        <ul className="plain-list">
          {details.map((detail, index) => (
            <li key={index}>
              {detail.field_path} · confidence{" "}
              {detail.confidence != null
                ? `${Math.round(detail.confidence * 100)}%`
                : "n/a"}{" "}
              · {detail.extraction_method ?? "unknown method"}
            </li>
          ))}
          <li>Affected check IDs: {reviewTask.affected_check_ids.join(", ")}</li>
        </ul>
      </details>
    </div>
  );
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
  const regulatoryEvidence = asList(report.regulatory_evidence)
    .map(asRecord)
    .filter((entry): entry is Record<string, unknown> => entry !== null);
  const documentEvidence = asList(report.document_evidence)
    .map(asRecord)
    .filter((entry): entry is Record<string, unknown> => entry !== null);
  const systemScope = asList(report.system_scope)
    .map(asRecord)
    .filter((entry): entry is Record<string, unknown> => entry !== null);
  const evidenceSearchExplanation = report.evidence_search_explanation
    ? displayValue(report.evidence_search_explanation)
    : null;
  const humanReviewSummary = asList(report.human_review_summary)
    .map(asRecord)
    .filter((entry): entry is Record<string, unknown> => entry !== null);
  const auditRevisionHistory = asList(report.audit_revision_history)
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

      {systemScope.length ? (
        <section className="report-section">
          <h3>Prototype scope</h3>
          <p className="section-intro">
            What this software is configured to check, not a government
            requirement.
          </p>
          <ul className="system-scope-list">
            {systemScope.map((entry, index) => (
              <SystemScopeRow key={index} entry={entry} />
            ))}
          </ul>
        </section>
      ) : null}

      {regulatoryEvidence.length ? (
        <section className="report-section">
          <h3>Regulatory evidence</h3>
          <p className="section-intro">
            Every requirement the rule engine cited a government source for,
            passed or failed alike, with the citation retrieval actually
            found. Expand a requirement to see its source, page, and matching
            passage.
          </p>
          {evidenceSearchExplanation ? (
            <details className="evidence-search-explanation">
              <summary>How the evidence search worked</summary>
              <p>{evidenceSearchExplanation}</p>
            </details>
          ) : null}
          <div className="evidence-card-list">
            {regulatoryEvidence.map((entry, index) => (
              <RegulatoryEvidenceCard key={index} entry={entry} />
            ))}
          </div>
        </section>
      ) : null}

      {documentEvidence.length ? (
        <section className="report-section">
          <h3>Document evidence</h3>
          <p className="section-intro">
            The exact invoice and packing-list values each comparison check
            was decided on.
          </p>
          <ul className="document-evidence-list">
            {documentEvidence.map((entry, index) => (
              <DocumentEvidenceRow key={index} entry={entry} />
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

      {humanReviewSummary.length ? (
        <section className="report-section">
          <h3>Human review</h3>
          <ul className="plain-list human-review-list">
            {humanReviewSummary.map((entry, index) => (
              <li key={index}>
                <strong>{displayValue(entry.field_label)}</strong>
                {entry.was_confirmation ? (
                  <p>
                    Confirmed as <strong>{displayValue(entry.corrected_value)}</strong>.
                  </p>
                ) : (
                  <p>
                    Changed from {displayValue(entry.original_value)} to{" "}
                    <strong>{displayValue(entry.corrected_value)}</strong>.
                  </p>
                )}
                {entry.reason ? (
                  <p className="muted">Reason: {displayValue(entry.reason)}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {auditRevisionHistory.length > 1 ? (
        <section className="report-section">
          <h3>
            <History aria-hidden="true" size={16} /> Audit history
          </h3>
          <p className="section-intro">
            Revision 1 is never changed - a correction only ever adds a new,
            separately frozen revision below it.
          </p>
          <ol className="revision-list">
            {auditRevisionHistory.map((revision, index) => (
              <li key={index}>
                Revision {displayValue(revision.revision_number)}:{" "}
                <strong>{displayValue(revision.status_label)}</strong>
                {revision.triggered_by === "human_correction" ? (
                  <span className="muted"> · after a human correction</span>
                ) : null}
              </li>
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
  shipmentId,
  workflow,
  reviewTask,
  events,
  busy,
  onDecision,
  onCorrection,
}: {
  shipmentId: string;
  workflow: WorkflowStatusResponse;
  reviewTask: ReviewTaskResponse | null;
  events: AuditEvent[];
  busy: boolean;
  onDecision: (action: "accept_manual_review" | "reject_submission") => void;
  onCorrection?: (
    action: CorrectionAction,
    fieldPath: string,
    originalValue: unknown,
    correctedValue: unknown,
    reason: string,
  ) => void;
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
              <strong>{reviewTask.title || "Human decision required"}</strong>
              <p>{reviewTask.reason}</p>

              {onCorrection && reviewTask.disputed_field_details.length ? (
                <CorrectionPanel
                  reviewTask={reviewTask}
                  busy={busy}
                  onCorrection={onCorrection}
                />
              ) : reviewTask.disputed_fields.length ? (
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

        <AssistantPanel shipmentId={shipmentId} />
      </div>
    </section>
  );
}
