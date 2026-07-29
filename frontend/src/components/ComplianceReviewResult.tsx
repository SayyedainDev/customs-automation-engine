import {
  AlertTriangle,
  CheckCircle2,
  FileCheck2,
  Files,
  ListChecks,
  ShieldCheck,
} from "lucide-react";
import type {
  ComplianceCheck,
  ComplianceStatus,
  CrossDocumentCheck,
  ExtractedField,
  MultiLineShipmentResponse,
} from "../api/types";
import { displayValue, labelize } from "../lib/format";
import { StatusBadge } from "./StatusBadge";

const invoiceFields = [
  ["exporter_name", "Exporter"],
  ["buyer_name", "Buyer"],
  ["invoice_number", "Invoice number"],
  ["invoice_date", "Invoice date"],
  ["destination_country", "Destination"],
  ["currency", "Currency"],
  ["invoice_total", "Invoice total"],
  ["declared_net_weight_total", "Declared net weight"],
  ["declared_gross_weight_total", "Declared gross weight"],
] as const;

const packingFields = [
  ["declared_net_weight_total", "Packing-list net weight"],
  ["declared_gross_weight_total", "Packing-list gross weight"],
  ["declared_package_count_total", "Packing-list total packages"],
] as const;

function extractedField(
  record: Record<string, ExtractedField | unknown[]>,
  name: string,
): ExtractedField | null {
  const candidate = record[name];
  if (
    candidate &&
    !Array.isArray(candidate) &&
    typeof candidate === "object" &&
    "value" in candidate
  ) {
    return candidate as ExtractedField;
  }
  return null;
}

function confidenceLabel(confidence: string | number | undefined): string {
  if (confidence === undefined) return "";
  const numeric = Number(confidence);
  return Number.isFinite(numeric) ? `${Math.round(numeric * 100)}% confidence` : "";
}

type FindingCheck = CrossDocumentCheck | ComplianceCheck;

interface RawFinding {
  check: FindingCheck;
  context: string;
}

interface FindingSource {
  label: string;
  url?: string;
}

interface ConsolidatedFinding {
  key: string;
  status: ComplianceStatus;
  title: string;
  explanation: string;
  currentState: string;
  action: string;
  contexts: string[];
  sources: FindingSource[];
}

const statusRank: Record<ComplianceStatus, number> = {
  not_applicable: 0,
  passed: 1,
  manual_review: 2,
  failed: 3,
};

const documentLabels: Record<string, string> = {
  form_e: "Form-E / PSW export declaration",
  certificate_of_origin: "Certificate of origin",
  bill_of_lading: "Bill of lading",
  export_contract: "Export contract",
  goods_declaration: "Goods declaration",
  licence: "Export licence",
  permit: "Export permit",
  product_approval: "Product approval",
  quality_certificate: "Quality certificate",
};

function normalizedDocumentKey(value: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");

  if (
    normalized === "form_e_or_psw_export_declaration" ||
    normalized === "psw_export_declaration"
  ) {
    return "form_e";
  }
  return normalized;
}

function requiredDocumentKey(check: FindingCheck): string | null {
  if ("required_document" in check && check.required_document) {
    return normalizedDocumentKey(check.required_document);
  }

  const text =
    `${check.check_id} ${check.check_name} ${check.message}`.toLowerCase();
  if (/\bform[\s_-]?e\b|psw export declaration/.test(text)) {
    return "form_e";
  }
  if (/certificate[\s-]of[\s-]origin|\bcoo\b/.test(text)) {
    return "certificate_of_origin";
  }
  return null;
}

function documentLabel(key: string): string {
  return documentLabels[key] ?? labelize(key);
}

function findingSpecificity(check: FindingCheck): number {
  const text = `${check.check_name} ${check.message}`.toLowerCase();
  let score = statusRank[check.status] * 100;
  if (/china|cpfta/.test(text)) score += 30;
  if (/destination/.test(text)) score += 5;
  if (/other destinations|conditional/.test(text)) score -= 20;
  return score;
}

function sourceFor(check: FindingCheck): FindingSource {
  if ("source_document" in check && check.source_document) {
    const parts = [check.source_document];
    if (check.sro_number) parts.push(check.sro_number);
    if (check.source_page) parts.push(`page ${check.source_page}`);
    return {
      label: parts.join(" · "),
      url: check.source_url ?? undefined,
    };
  }

  const pages: string[] = [];
  if ("invoice_source_page" in check && check.invoice_source_page) {
    pages.push(`invoice page ${check.invoice_source_page}`);
  }
  if ("packing_list_source_page" in check && check.packing_list_source_page) {
    pages.push(`packing-list page ${check.packing_list_source_page}`);
  }
  return {
    label: pages.length
      ? `Invoice and packing-list comparison · ${pages.join(" · ")}`
      : "Invoice and packing-list comparison",
  };
}

function displayContext(context: string): string {
  if (context === "Shipment") return "Whole shipment";
  return labelize(context);
}

function correctiveAction(check: FindingCheck): string {
  const text = `${check.check_name} ${check.message}`.toLowerCase();
  if (/weight/.test(text)) {
    return "Reconcile the invoice and packing-list weights, correct the source document, and run the review again.";
  }
  if (/quantity|package/.test(text)) {
    return "Reconcile the quantities or package counts on both documents, correct them, and run the review again.";
  }
  if (/arithmetic|total|value|amount/.test(text)) {
    return "Check the calculation and declared values on the invoice, correct any error, and run the review again.";
  }
  if (/pct|hs code|classification/.test(text)) {
    return "Confirm the product classification with a qualified reviewer, correct the PCT code if needed, and run the review again.";
  }
  if (check.status === "manual_review") {
    return "Ask a compliance reviewer to confirm this requirement before the customs submission proceeds.";
  }
  return "Correct the source document or provide supporting evidence, then run the review again.";
}

function consolidateFindings(findings: RawFinding[]): ConsolidatedFinding[] {
  const groups = new Map<string, RawFinding[]>();

  for (const finding of findings) {
    const documentKey = requiredDocumentKey(finding.check);
    const key = documentKey
      ? `document:${documentKey}`
      : `check:${finding.check.check_id}`;
    groups.set(key, [...(groups.get(key) ?? []), finding]);
  }

  return [...groups.entries()]
    .map(([key, group]) => {
      const representative = [...group].sort(
        (left, right) =>
          findingSpecificity(right.check) - findingSpecificity(left.check),
      )[0];
      const requiredDocument = requiredDocumentKey(representative.check);
      const sources = [
        ...new Map(
          group.map(({ check }) => {
            const source = sourceFor(check);
            return [`${source.label}|${source.url ?? ""}`, source] as const;
          }),
        ).values(),
      ];
      const contexts = [
        ...new Set(group.map(({ context }) => displayContext(context))),
      ];

      if (requiredDocument) {
        const label = documentLabel(requiredDocument);
        const needsConfirmation =
          representative.check.status === "manual_review";
        return {
          key,
          status: representative.check.status,
          title: needsConfirmation
            ? `${label} requirement needs confirmation`
            : `${label} not provided`,
          explanation: representative.check.message,
          currentState: `${label} was not included in this review. The invoice and packing list were still uploaded, extracted, and compared successfully.`,
          action: needsConfirmation
            ? `Ask a compliance reviewer whether ${label.toLowerCase()} applies to this shipment. If it does, obtain it before customs submission.`
            : `Obtain the required ${label.toLowerCase()} and include it with the shipment documents before customs submission.`,
          contexts,
          sources,
        };
      }

      return {
        key,
        status: representative.check.status,
        title: representative.check.check_name,
        explanation: representative.check.message,
        currentState:
          representative.check.status === "manual_review"
            ? "The available document data was not enough for the rule engine to decide this check automatically."
            : "The available invoice and packing-list data did not satisfy this deterministic check.",
        action: correctiveAction(representative.check),
        contexts,
        sources,
      };
    })
    .sort(
      (left, right) => statusRank[right.status] - statusRank[left.status],
    );
}

function decisionCopy(status: ComplianceStatus): {
  title: string;
  description: string;
} {
  switch (status) {
    case "passed":
      return {
        title: "The uploaded documents are sound",
        description:
          "Extraction, invoice-to-packing-list matching, arithmetic, and product classification all passed on the two files you uploaded.",
      };
    case "manual_review":
      return {
        title: "The uploaded documents need a person to confirm a value",
        description:
          "Both files were read, but at least one value could not be determined safely enough for the rule engine to decide on its own.",
      };
    case "failed":
      return {
        title: "The uploaded documents contain a problem to correct",
        description:
          "Both files were read, but something in them did not hold up: the reasons and the corrections are listed below.",
      };
    default:
      return {
        title: "No document decision available",
        description: "The configured checks did not apply to this shipment.",
      };
  }
}

/** Plain wording for the strict customs verdict, shown beside the checklist. */
function submissionCopy(
  status: ComplianceStatus,
  outstandingCount: number,
): string {
  if (outstandingCount > 0) {
    return status === "passed"
      ? "Confirm the documents listed below before submission."
      : `Not ready for submission: ${outstandingCount} customs ${
          outstandingCount === 1 ? "document is" : "documents are"
        } still outstanding.`;
  }
  switch (status) {
    case "passed":
      return "Ready for the next submission step under the configured rules.";
    case "manual_review":
      return "A person must review the flagged points before submission.";
    default:
      return "Not ready for submission until the findings below are resolved.";
  }
}

function Finding({
  finding,
}: {
  finding: ConsolidatedFinding;
}) {
  const tone =
    finding.status === "failed"
      ? "danger"
      : finding.status === "manual_review"
        ? "warning"
        : "success";
  return (
    <li className={`finding finding--${tone}`}>
      <span className="finding__status">
        <StatusBadge status={finding.status} />
      </span>
      <div className="finding__content">
        <div className="finding__title-row">
          <h3>{finding.title}</h3>
          <span className="muted">{finding.contexts.join(", ")}</span>
        </div>
        <dl className="finding__details">
          <div>
            <dt>Explanation</dt>
            <dd>{finding.explanation}</dd>
          </div>
          <div>
            <dt>Current state</dt>
            <dd>{finding.currentState}</dd>
          </div>
          <div>
            <dt>What to do</dt>
            <dd>{finding.action}</dd>
          </div>
        </dl>
        <div className="finding__sources">
          <strong>Sources</strong>
          <ul>
            {finding.sources.map((source) => (
              <li key={`${source.label}-${source.url ?? ""}`}>
                {source.url ? (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-link"
                  >
                    {source.label}
                  </a>
                ) : (
                  source.label
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </li>
  );
}

export function ComplianceReviewResult({
  result,
}: {
  result: MultiLineShipmentResponse;
}) {
  const allItemChecks = result.items.flatMap((item) => [
    ...item.item_checks.map((check) => ({
      check,
      context: item.item_reference,
    })),
    ...(item.compliance?.checks ?? []).map((check) => ({
      check,
      context: item.item_reference,
    })),
    ...(item.compliance?.executable_rule_checks ?? []).map((check) => ({
      check,
      context: item.item_reference,
    })),
  ]);

  const rawFindings: RawFinding[] = [
    ...result.shipment_level_checks.map((check) => ({
      check,
      context: "Shipment",
    })),
    ...allItemChecks,
  ].filter(
    ({ check }) =>
      check.status !== "passed" && check.status !== "not_applicable",
  );

  // Paperwork the exporter has to obtain elsewhere is shown as its own
  // checklist, so it is removed from the findings about the uploaded files.
  const outstanding = result.outstanding_documents ?? [];
  const outstandingKeys = new Set(
    outstanding.map((document) => normalizedDocumentKey(document.document_type)),
  );
  const actionableFindings = consolidateFindings(rawFindings).filter(
    (finding) => !outstandingKeys.has(finding.key.replace(/^document:/, "")),
  );
  const matchedLineCount = result.items.filter(
    (item) => item.match_status === "matched",
  ).length;
  const documentStatus = result.document_review_status ?? result.overall_status;
  const supportingExtractionStatuses = result.supporting_documents
    .map((document) => {
      const documentType = String(
        document.canonical_document_type ??
          document.claimed_document_type ??
          "supporting_document",
      );
      const summary =
        typeof document.extraction_summary === "string"
          ? document.extraction_summary
          : null;
      return summary
        ? {
            documentType,
            label: documentLabel(normalizedDocumentKey(documentType)),
            summary,
          }
        : null;
    })
    .filter(
      (
        item,
      ): item is { documentType: string; label: string; summary: string } =>
        item !== null,
    );
  const decision = decisionCopy(documentStatus);
  const nextActions = [
    ...new Set([
      ...outstanding.map((document) =>
        document.requirement === "required"
          ? `Obtain the ${document.display_name.toLowerCase()} from the body that issues it and file it with the shipment documents.`
          : `Confirm with a compliance reviewer whether the ${document.display_name.toLowerCase()} applies to this shipment.`,
      ),
      ...actionableFindings.map((finding) => finding.action),
      ...(result.fields_requiring_manual_review.length
        ? [
            `Confirm the flagged fields (${result.fields_requiring_manual_review
              .map((field) => labelize(field))
              .join(", ")}) before submission.`,
          ]
        : []),
    ]),
  ];

  return (
    <div className="review-result">
      <section className="panel" aria-labelledby="result-heading">
        <div className="panel__header">
          <div>
            <p className="eyebrow">Deterministic decision · uploaded documents</p>
            <h2 id="result-heading">{decision.title}</h2>
            <p>{decision.description}</p>
          </div>
          <StatusBadge status={documentStatus} />
        </div>
        <div className="panel__body stack">
          <div className="notice notice--success processing-success">
            <CheckCircle2 aria-hidden="true" size={18} />
            <div>
              <strong>Invoice and packing list processed successfully</strong>
              <p>
                Both files were uploaded, their text and fields were extracted,
                and the shipment comparison finished. A failed compliance
                decision does not mean document processing failed.
              </p>
            </div>
          </div>

          <div className="submission-readiness">
            <div>
              <span className="eyebrow">Customs submission readiness</span>
              <p>{submissionCopy(result.overall_status, outstanding.length)}</p>
            </div>
            <StatusBadge status={result.overall_status} />
          </div>

          <div className="summary-grid">
            <div className="summary-item">
              <span>Input documents processed</span>
              <strong>2 of 2</strong>
            </div>
            <div className="summary-item">
              <span>Documents still to obtain</span>
              <strong>{outstanding.length}</strong>
            </div>
            <div className="summary-item">
              <span>Shipment lines matched</span>
              <strong>
                {matchedLineCount} of {result.items.length}
              </strong>
            </div>
            <div className="summary-item">
              <span>Fields needing confirmation</span>
              <strong>{result.fields_requiring_manual_review.length}</strong>
            </div>
          </div>

          <details className="technical-details">
            <summary>Technical details</summary>
            <dl>
              <div>
                <dt>Rule-data version</dt>
                <dd>
                  <code>{result.rule_data_version}</code>
                </dd>
              </div>
            </dl>
          </details>
        </div>
      </section>

      {supportingExtractionStatuses.length ? (
        <section className="panel" aria-labelledby="supporting-extraction-heading">
          <div className="panel__header">
            <div>
              <h2 id="supporting-extraction-heading">
                Supporting-document extraction
              </h2>
              <p>
                AI assistance is reported only for the fields that required it.
              </p>
            </div>
            <FileCheck2 aria-hidden="true" size={19} />
          </div>
          <div className="panel__body">
            <ul className="document-checklist">
              {supportingExtractionStatuses.map((document) => (
                <li key={document.documentType}>
                  <div className="document-checklist__head">
                    <strong>{document.label}</strong>
                    <span>{document.summary}</span>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </section>
      ) : null}

      {result.fields_requiring_manual_review.length ? (
        <div className="notice notice--warning" role="status">
          <AlertTriangle aria-hidden="true" size={18} />
          <div>
            <strong>Values require confirmation</strong>
            <p>
              {result.fields_requiring_manual_review
                .map((field) => labelize(field))
                .join(", ")}
            </p>
          </div>
        </div>
      ) : null}

      <section className="panel" aria-labelledby="checked-heading">
        <div className="panel__header">
          <div>
            <h2 id="checked-heading">What was checked</h2>
            <p>The result combines document matching and configured customs rules.</p>
          </div>
          <ListChecks aria-hidden="true" size={19} />
        </div>
        <div className="panel__body">
          <ul className="decision-checklist">
            <li>
              <Files aria-hidden="true" size={18} />
              <div>
                <strong>Document intake and extraction</strong>
                <p>
                  The commercial invoice and its packing list were read and
                  converted into structured shipment fields.
                </p>
              </div>
            </li>
            <li>
              <CheckCircle2 aria-hidden="true" size={18} />
              <div>
                <strong>Cross-document consistency</strong>
                <p>
                  Product lines, quantities, packages, values, and weights were
                  compared where the documents supplied those values.
                </p>
              </div>
            </li>
            <li>
              <ShieldCheck aria-hidden="true" size={18} />
              <div>
                <strong>Configured regulatory requirements</strong>
                <p>
                  PCT classification, destination rules, and required
                  supporting documents were evaluated for this capstone’s
                  five-product scope.
                </p>
              </div>
            </li>
          </ul>
        </div>
      </section>

      {outstanding.length ? (
        <section className="panel" aria-labelledby="outstanding-heading">
          <div className="panel__header">
            <div>
              <h2 id="outstanding-heading">Required before submission</h2>
              <p>
                Customs documents the rules require for this shipment. They are
                issued by outside bodies and cannot be produced from the invoice
                or the packing list, so their absence is not a defect in the two
                files you uploaded.
              </p>
            </div>
            <FileCheck2 aria-hidden="true" size={19} />
          </div>
          <div className="panel__body">
            <ul className="document-checklist">
              {outstanding.map((document) => (
                <li key={document.document_type}>
                  <div className="document-checklist__head">
                    <strong>{document.display_name}</strong>
                    <span
                      className={`requirement-tag requirement-tag--${document.requirement}`}
                    >
                      {document.requirement === "required"
                        ? "Required"
                        : "Confirm whether it applies"}
                    </span>
                  </div>
                  {document.reasons.length ? (
                    <ul className="plain-list">
                      {document.reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  ) : null}
                  {document.sources.length ? (
                    <small>Source: {document.sources.join(" · ")}</small>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        </section>
      ) : null}

      <section className="panel" aria-labelledby="findings-heading">
        <div className="panel__header">
          <div>
            <h2 id="findings-heading">Findings in the uploaded documents</h2>
            <p>
              Problems in the invoice or packing list themselves. Missing
              customs paperwork is listed separately above.
            </p>
          </div>
          <ShieldCheck aria-hidden="true" size={19} />
        </div>
        <div className="panel__body">
          {actionableFindings.length ? (
            <ul className="finding-list">
              {actionableFindings.map((finding) => (
                <Finding key={finding.key} finding={finding} />
              ))}
            </ul>
          ) : (
            <div className="notice notice--success">
              <ShieldCheck aria-hidden="true" size={18} />
              <div>
                <strong>Nothing wrong with the uploaded documents</strong>
                <p>
                  The invoice and packing list passed every check that can be
                  made on them: extraction, line matching, arithmetic, weights,
                  and product classification.
                </p>
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="panel" aria-labelledby="next-steps-heading">
        <div className="panel__header">
          <div>
            <h2 id="next-steps-heading">What to do next</h2>
            <p>Complete these steps before treating the shipment as ready.</p>
          </div>
        </div>
        <div className="panel__body">
          {nextActions.length ? (
            <ol className="action-list">
              {nextActions.map((action) => (
                <li key={action}>{action}</li>
              ))}
            </ol>
          ) : (
            <div className="notice notice--success">
              <CheckCircle2 aria-hidden="true" size={18} />
              <div>
                <strong>No corrective action identified</strong>
                <p>
                  Keep the review result with the shipment records and follow
                  the normal submission process.
                </p>
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="panel" aria-labelledby="extracted-fields-heading">
        <div className="panel__header">
          <div>
            <h2 id="extracted-fields-heading">Key extracted fields</h2>
            <p>Values retain their page and confidence provenance.</p>
          </div>
          <FileCheck2 aria-hidden="true" size={19} />
        </div>
        <div className="panel__body">
          <dl className="metadata-grid">
            {invoiceFields.map(([name, label]) => {
              const field = extractedField(result.invoice, name);
              return (
                <div key={name} className="metadata-item">
                  <dt>{label}</dt>
                  <dd>{displayValue(field?.value)}</dd>
                  <small>
                    {field?.source_page ? `Page ${field.source_page}` : "No page"}
                    {field?.confidence !== undefined
                      ? ` · ${confidenceLabel(field.confidence)}`
                      : ""}
                  </small>
                </div>
              );
            })}
            {packingFields.map(([name, label]) => {
              const field = extractedField(result.packing_list, name);
              return (
                <div key={`packing-${name}`} className="metadata-item">
                  <dt>{label}</dt>
                  <dd>{displayValue(field?.value)}</dd>
                  <small>
                    {field?.source_page ? `Page ${field.source_page}` : "No page"}
                    {field?.confidence !== undefined
                      ? ` · ${confidenceLabel(field.confidence)}`
                      : ""}
                  </small>
                </div>
              );
            })}
          </dl>
        </div>
      </section>

      <section className="panel" aria-labelledby="line-items-heading">
        <div className="panel__header">
          <div>
            <h2 id="line-items-heading">Matched shipment lines</h2>
            <p>Invoice lines matched against the packing list.</p>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Reference</th>
                <th>Product</th>
                <th>PCT code</th>
                <th>Match</th>
                <th>Method</th>
                <th>Compliance</th>
              </tr>
            </thead>
            <tbody>
              {result.items.map((item) => (
                <tr key={item.item_reference}>
                  <td data-label="Reference">{item.item_reference}</td>
                  <td data-label="Product">
                    {item.product_name ?? "Not extracted"}
                  </td>
                  <td data-label="PCT code">{item.pct_code ?? "Not extracted"}</td>
                  <td data-label="Match">
                    <StatusBadge status={item.match_status} />
                  </td>
                  <td data-label="Method">{labelize(item.match_strategy)}</td>
                  <td data-label="Compliance">
                    <StatusBadge status={item.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
