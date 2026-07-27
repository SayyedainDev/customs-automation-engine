import { AlertTriangle, FileCheck2, ShieldCheck } from "lucide-react";
import type {
  ComplianceCheck,
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

function Finding({
  finding,
  context,
}: {
  finding: CrossDocumentCheck | ComplianceCheck;
  context?: string;
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
          <h3>{finding.check_name}</h3>
          {context ? <span className="muted">{context}</span> : null}
        </div>
        <p>{finding.message}</p>
        {"source_document" in finding && finding.source_document ? (
          <small>
            Source: {finding.source_document}
            {finding.sro_number ? ` · ${finding.sro_number}` : ""}
            {finding.source_page ? ` · page ${finding.source_page}` : ""}
          </small>
        ) : null}
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

  const actionableFindings = [
    ...result.shipment_level_checks.map((check) => ({
      check,
      context: "Shipment",
    })),
    ...allItemChecks,
  ].filter(
    ({ check }) =>
      check.status !== "passed" && check.status !== "not_applicable",
  );

  return (
    <div className="review-result">
      <section className="panel" aria-labelledby="result-heading">
        <div className="panel__header">
          <div>
            <p className="eyebrow">Deterministic decision</p>
            <h2 id="result-heading">Compliance review result</h2>
            <p>
              This status comes from the five-PCT rule engine and document
              comparison, not from an AI opinion.
            </p>
          </div>
          <StatusBadge status={result.overall_status} />
        </div>
        <div className="summary-grid">
          <div className="summary-item">
            <span>Rule data</span>
            <strong>{result.rule_data_version}</strong>
          </div>
          <div className="summary-item">
            <span>Matched lines</span>
            <strong>
              {result.items.filter((item) => item.match_status === "matched").length}{" "}
              of {result.items.length}
            </strong>
          </div>
          <div className="summary-item">
            <span>Manual fields</span>
            <strong>{result.fields_requiring_manual_review.length}</strong>
          </div>
        </div>
      </section>

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

      <section className="panel" aria-labelledby="findings-heading">
        <div className="panel__header">
          <div>
            <h2 id="findings-heading">Compliance findings</h2>
            <p>Failed and review-required checks that need attention.</p>
          </div>
          <ShieldCheck aria-hidden="true" size={19} />
        </div>
        <div className="panel__body">
          {actionableFindings.length ? (
            <ul className="finding-list">
              {actionableFindings.map(({ check, context }, index) => (
                <Finding
                  key={`${check.check_id}-${context}-${index}`}
                  finding={check}
                  context={context}
                />
              ))}
            </ul>
          ) : (
            <div className="notice notice--success">
              <ShieldCheck aria-hidden="true" size={18} />
              <div>
                <strong>No action required</strong>
                <p>No failed or uncertain checks were returned.</p>
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
