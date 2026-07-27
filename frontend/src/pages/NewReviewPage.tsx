import {
  FileCheck2,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { AgentAuditResult } from "../components/AgentAuditResult";
import { ComplianceReviewResult } from "../components/ComplianceReviewResult";
import { DocumentReviewForm } from "../components/DocumentReviewForm";
import { ErrorNotice } from "../components/ErrorNotice";
import { PageHeader } from "../components/PageHeader";
import { useDocumentReview } from "../hooks/useDocumentReview";

export function NewReviewPage() {
  const review = useDocumentReview();

  return (
    <>
      <PageHeader
        eyebrow="New review"
        title="Review export documents"
        description="Upload one commercial invoice and its packing list. The system extracts, matches, and checks the shipment before any optional agent audit."
        action={
          review.compliance ? (
            <button
              className="button button--secondary"
              type="button"
              onClick={review.startOver}
              disabled={review.agentBusy || review.decisionBusy}
            >
              <RefreshCw aria-hidden="true" size={16} />
              Start another review
            </button>
          ) : null
        }
      />

      {review.error ? (
        <ErrorNotice
          message={review.error}
          onDismiss={review.dismissError}
        />
      ) : null}

      <DocumentReviewForm review={review} />

      {review.compliance ? (
        <ComplianceReviewResult result={review.compliance} />
      ) : null}

      {review.compliance && !review.workflow ? (
        <section className="panel" aria-labelledby="agent-offer-heading">
          <div className="panel__header">
            <div>
              <h2 id="agent-offer-heading">Optional Broker and Auditor audit</h2>
              <p>
                Run the resumable agent workflow to challenge the deterministic
                result, retrieve regulatory evidence, and produce an
                explanation. This action may use your Groq free-tier quota, so
                it is never started automatically.
              </p>
            </div>
            <ShieldCheck aria-hidden="true" size={20} />
          </div>
          <div className="panel__body">
            <div className="agent-callout">
              <div>
                <h3>Quota-aware action</h3>
                <p>
                  The deterministic compliance result above is already
                  complete. Run this only when you want the agent explanation
                  and audit trail.
                </p>
              </div>
              <button
                className="button button--secondary"
                type="button"
                onClick={() => void review.startAgentAudit()}
                disabled={review.agentBusy}
              >
                {review.agentBusy ? (
                  <LoaderCircle
                    className="spinner"
                    aria-hidden="true"
                    size={16}
                  />
                ) : (
                  <FileCheck2 aria-hidden="true" size={16} />
                )}
                {review.agentBusy
                  ? "Running agent audit…"
                  : "Run agent audit"}
              </button>
            </div>
          </div>
        </section>
      ) : null}

      {review.agentError ? (
        <ErrorNotice
          message={review.agentError}
          onDismiss={review.dismissAgentError}
        />
      ) : null}

      {review.workflow ? (
        <AgentAuditResult
          workflow={review.workflow}
          reviewTask={review.reviewTask}
          events={review.events}
          busy={review.decisionBusy}
          onDecision={(action) => void review.submitDecision(action)}
        />
      ) : null}
    </>
  );
}
