import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  FileStack,
  ShieldCheck,
} from "lucide-react";
import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { formatBytes, formatDate, labelize } from "../lib/format";
import { useSession } from "../state/SessionContext";

export function OverviewPage() {
  const { documents } = useSession();
  const processing = documents.filter(
    (document) =>
      !["extracted", "completed"].includes(document.status) &&
      !document.complianceStatus,
  ).length;
  const needsReview = documents.filter(
    (document) => document.complianceStatus === "manual_review",
  ).length;
  const compliant = documents.filter(
    (document) => document.complianceStatus === "passed",
  ).length;

  return (
    <>
      <PageHeader
        eyebrow="Operations"
        title="Customs review workspace"
        description="Upload export documents, verify extracted shipment data, and run evidence-backed compliance checks."
        action={
          <Link className="button button--primary" to="/review">
            Start document review
            <ArrowRight aria-hidden="true" size={16} />
          </Link>
        }
      />

      <div className="notice notice--info overview-note">
        <ShieldCheck aria-hidden="true" size={18} />
        <div>
          <strong>Decision authority</strong>
          <p>
            Deterministic rules decide compliance. AI is limited to unresolved
            extraction and grounded explanation.
          </p>
        </div>
      </div>

      <section aria-labelledby="workspace-summary">
        <div className="section-heading">
          <div>
            <h2 id="workspace-summary">Browser workspace</h2>
            <p>
              Real documents submitted from this browser. This is not an
              organization-wide dashboard.
            </p>
          </div>
        </div>
        <div className="metric-grid">
          <article className="metric">
            <span className="metric__icon metric__icon--neutral">
              <FileStack size={18} />
            </span>
            <div>
              <span>Total documents</span>
              <strong>{documents.length}</strong>
            </div>
          </article>
          <article className="metric">
            <span className="metric__icon metric__icon--info">
              <Clock3 size={18} />
            </span>
            <div>
              <span>Processing</span>
              <strong>{processing}</strong>
            </div>
          </article>
          <article className="metric">
            <span className="metric__icon metric__icon--warning">
              <AlertTriangle size={18} />
            </span>
            <div>
              <span>Review required</span>
              <strong>{needsReview}</strong>
            </div>
          </article>
          <article className="metric">
            <span className="metric__icon metric__icon--success">
              <CheckCircle2 size={18} />
            </span>
            <div>
              <span>Compliant</span>
              <strong>{compliant}</strong>
            </div>
          </article>
        </div>
      </section>

      <section className="panel" aria-labelledby="recent-documents">
        <div className="panel__header">
          <div>
            <h2 id="recent-documents">Recent documents</h2>
            <p>Uploads retained in this browser for the class demonstration.</p>
          </div>
          {documents.length ? (
            <Link className="text-link" to="/review">
              New review <ArrowRight size={14} />
            </Link>
          ) : null}
        </div>

        {documents.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Type</th>
                  <th>Uploaded</th>
                  <th>Size</th>
                  <th>Processing</th>
                  <th>Compliance</th>
                </tr>
              </thead>
              <tbody>
                {documents.slice(0, 8).map((document) => (
                  <tr key={document.id}>
                    <td data-label="Document">
                      <div className="primary-cell">
                        <strong>{document.name}</strong>
                        <span>{document.id.slice(0, 8)}…</span>
                      </div>
                    </td>
                    <td data-label="Type">{labelize(document.role)}</td>
                    <td data-label="Uploaded">
                      {formatDate(document.uploadedAt)}
                    </td>
                    <td data-label="Size">{formatBytes(document.sizeBytes)}</td>
                    <td data-label="Processing">
                      <StatusBadge status={document.status} />
                    </td>
                    <td data-label="Compliance">
                      {document.complianceStatus ? (
                        <StatusBadge status={document.complianceStatus} />
                      ) : (
                        <span className="muted">Not checked</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="No documents in this workspace"
            description="Start a review to upload a commercial invoice and packing list."
            action={
              <Link className="button button--secondary" to="/review">
                Upload documents
              </Link>
            }
          />
        )}
      </section>
    </>
  );
}
