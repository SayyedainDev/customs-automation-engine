import {
  FileCheck2,
  LoaderCircle,
  MessageSquare,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AgentAuditResult } from "../components/AgentAuditResult";
import { ComplianceReviewResult } from "../components/ComplianceReviewResult";
import { DocumentReviewForm } from "../components/DocumentReviewForm";
import { ErrorNotice } from "../components/ErrorNotice";
import { PageHeader } from "../components/PageHeader";
import { useDocumentReview } from "../hooks/useDocumentReview";

export function NewReviewPage() {
  const review = useDocumentReview();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryWorkflowId =
    searchParams.get("workflowId") ?? searchParams.get("workflow_id");
  const querySampleKey = searchParams.get("sample");
  const autoLoadedSampleRef = useRef<string | null>(null);

  useEffect(() => {
    if (
      queryWorkflowId &&
      review.workflow?.workflow_id !== queryWorkflowId &&
      !review.agentBusy
    ) {
      void review.loadExistingWorkflow(queryWorkflowId);
    }
  }, [queryWorkflowId, review]);

  useEffect(() => {
    if (
      querySampleKey &&
      autoLoadedSampleRef.current !== querySampleKey &&
      !review.reviewBusy &&
      !review.compliance
    ) {
      autoLoadedSampleRef.current = querySampleKey;
      void review.loadDemoSample(querySampleKey);
    }
  }, [querySampleKey, review]);

  const activeShipmentChatId =
    review.workflow?.workflow_id ??
    review.compliance?.commercial_invoice_document_id;

  return (
    <>
      <PageHeader
        eyebrow="New review"
        title="Review export documents"
        description="Upload one commercial invoice and its packing list. The system extracts, matches, and checks the shipment before any optional agent audit."
        action={
          review.compliance || review.workflow ? (
            <div className="button-row">
              {activeShipmentChatId ? (
                <Link
                  className="button button--secondary"
                  to={`/ask?shipment_id=${activeShipmentChatId}`}
                >
                  <MessageSquare aria-hidden="true" size={16} />
                  Ask assistant about this shipment
                </Link>
              ) : null}
              <button
                className="button button--secondary"
                type="button"
                onClick={() => {
                  setSearchParams({});
                  review.startOver();
                }}
                disabled={review.agentBusy || review.decisionBusy}
              >
                <RefreshCw aria-hidden="true" size={16} />
                Start another review
              </button>
            </div>
          ) : null
        }
      />

      {review.error ? (
        <ErrorNotice
          message={review.error}
          onDismiss={review.dismissError}
        />
      ) : null}

      {!review.compliance && !review.workflow ? (
        <section className="panel" aria-labelledby="sample-shipments-heading">
          <div className="panel__header">
            <div>
              <p className="eyebrow">Quick demo</p>
              <h2 id="sample-shipments-heading">Load a built-in sample shipment</h2>
              <p>
                Explore PDF provenance highlighting, cascading FY2026–27 duty math,
                and the LangGraph audit workflow immediately with curated export
                PDFs.
              </p>
            </div>
            <Sparkles aria-hidden="true" size={20} />
          </div>
          <div className="panel__body">
            <div className="button-row">
              <button
                className="button button--primary"
                type="button"
                disabled={review.reviewBusy}
                onClick={() => void review.loadDemoSample("clean_cotton_tshirts")}
              >
                Clean Textile Export (PASS)
              </button>
              <button
                className="button button--secondary"
                type="button"
                disabled={review.reviewBusy}
                onClick={() => void review.loadDemoSample("error_quantity_mismatch")}
              >
                Quantity Mismatch (HITL Audit)
              </button>
            </div>
          </div>
        </section>
      ) : null}

      <DocumentReviewForm review={review} />

      {review.compliance ? (
        <ComplianceReviewResult result={review.compliance} />
      ) : null}

      {review.compliance && !review.workflowIsCurrent ? (
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

      {review.compliance && review.workflow && review.workflowIsCurrent ? (
        <AgentAuditResult
          workflow={review.workflow}
          reviewTask={review.reviewTask}
          events={review.events}
          busy={review.decisionBusy}
          uploadProgress={review.missingDocUploadProgress}
          invoiceDocumentId={review.compliance.commercial_invoice_document_id}
          packingListDocumentId={review.compliance.packing_list_document_id}
          onDecision={(action) => void review.submitDecision(action)}
          onCorrection={(action, fieldPath, originalValue, correctedValue, reason) =>
            void review.submitCorrection(
              action,
              fieldPath,
              originalValue,
              correctedValue,
              reason,
            )
          }
          onProvideMissingDocument={(file, documentType, note) =>
            void review.submitMissingDocument(file, documentType, note)
          }
          onReprocess={(reason) => void review.submitReprocess(reason)}
        />
      ) : !review.compliance && review.workflow ? (
        <AgentAuditResult
          workflow={review.workflow}
          reviewTask={review.reviewTask}
          events={review.events}
          busy={review.decisionBusy}
          uploadProgress={review.missingDocUploadProgress}
          onDecision={(action) => void review.submitDecision(action)}
          onCorrection={(action, fieldPath, originalValue, correctedValue, reason) =>
            void review.submitCorrection(
              action,
              fieldPath,
              originalValue,
              correctedValue,
              reason,
            )
          }
          onProvideMissingDocument={(file, documentType, note) =>
            void review.submitMissingDocument(file, documentType, note)
          }
          onReprocess={(reason) => void review.submitReprocess(reason)}
        />
      ) : null}
    </>
  );
}
