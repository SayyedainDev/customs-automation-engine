import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type {
  AuditEvent,
  DocumentExtractionResponse,
  DocumentUploadResponse,
  MultiLineShipmentRequest,
  MultiLineShipmentResponse,
  ReviewTaskResponse,
  WorkflowStatusResponse,
} from "../api/types";
import type { ProcessingStage } from "../components/ProcessingTimeline";
import { useSession } from "../state/SessionContext";

type ReviewPhase =
  | "idle"
  | "uploading_invoice"
  | "uploading_packing"
  | "extracting"
  | "checking"
  | "complete";

const pollingStatuses = new Set(["created", "running", "resuming"]);

function readableError(error: unknown): string {
  if (error instanceof ApiError && [429, 503].includes(error.status ?? 0)) {
    return (
      "The AI provider is temporarily unavailable or has reached its " +
      "free-tier limit. Try again later; uploaded documents remain available."
    );
  }
  return error instanceof Error
    ? error.message
    : "The request could not be completed.";
}

function stageState(
  complete: boolean,
  active: boolean,
): ProcessingStage["state"] {
  if (complete) return "complete";
  return active ? "active" : "waiting";
}

export function useDocumentReview() {
  const session = useSession();
  const sessionRef = useRef(session);
  sessionRef.current = session;

  const [invoiceFile, setInvoiceFile] = useState<File | null>(null);
  const [packingFile, setPackingFile] = useState<File | null>(null);
  const [shipmentDate, setShipmentDate] = useState("");
  const [letterOfCreditDate, setLetterOfCreditDate] = useState("");
  const [invoiceUpload, setInvoiceUpload] =
    useState<DocumentUploadResponse | null>(null);
  const [packingUpload, setPackingUpload] =
    useState<DocumentUploadResponse | null>(null);
  const [invoiceExtraction, setInvoiceExtraction] =
    useState<DocumentExtractionResponse | null>(null);
  const [packingExtraction, setPackingExtraction] =
    useState<DocumentExtractionResponse | null>(null);
  const [invoiceProgress, setInvoiceProgress] = useState(0);
  const [packingProgress, setPackingProgress] = useState(0);
  const [phase, setPhase] = useState<ReviewPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [compliance, setCompliance] =
    useState<MultiLineShipmentResponse | null>(null);
  const [requestPayload, setRequestPayload] =
    useState<MultiLineShipmentRequest | null>(null);
  const [workflow, setWorkflow] = useState<WorkflowStatusResponse | null>(null);
  const [reviewTask, setReviewTask] = useState<ReviewTaskResponse | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [agentBusy, setAgentBusy] = useState(false);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [agentError, setAgentError] = useState<string | null>(null);

  const reviewBusy = !["idle", "complete"].includes(phase);

  const clearWorkflow = useCallback(() => {
    setCompliance(null);
    setRequestPayload(null);
    setWorkflow(null);
    setReviewTask(null);
    setEvents([]);
    setAgentError(null);
    setPhase("idle");
  }, []);

  const chooseInvoice = useCallback(
    (file: File | null, validationError?: string) => {
      setInvoiceFile(file);
      setInvoiceUpload(null);
      setInvoiceExtraction(null);
      setInvoiceProgress(0);
      setError(validationError ?? null);
      clearWorkflow();
    },
    [clearWorkflow],
  );

  const choosePackingList = useCallback(
    (file: File | null, validationError?: string) => {
      setPackingFile(file);
      setPackingUpload(null);
      setPackingExtraction(null);
      setPackingProgress(0);
      setError(validationError ?? null);
      clearWorkflow();
    },
    [clearWorkflow],
  );

  const loadWorkflowAssets = useCallback(
    async (current: WorkflowStatusResponse) => {
      try {
        const eventResponse = await api.getEvents(current.workflow_id);
        setEvents(eventResponse.events);
      } catch (assetError) {
        setAgentError(readableError(assetError));
      }

      if (current.status === "awaiting_human_review") {
        try {
          setReviewTask(await api.getReview(current.workflow_id));
        } catch (assetError) {
          setAgentError(readableError(assetError));
        }
      } else {
        setReviewTask(null);
      }
    },
    [],
  );

  const workflowId = workflow?.workflow_id;
  const shouldPoll = Boolean(
    workflow && pollingStatuses.has(workflow.status),
  );

  useEffect(() => {
    if (!workflowId || !shouldPoll) return;

    let cancelled = false;
    let timeout: number | undefined;

    const poll = async () => {
      try {
        const next = await api.getWorkflow(workflowId);
        if (cancelled) return;
        setWorkflow(next);
        if (pollingStatuses.has(next.status)) {
          timeout = window.setTimeout(poll, 2_500);
        } else {
          await loadWorkflowAssets(next);
        }
      } catch (pollError) {
        if (!cancelled) setAgentError(readableError(pollError));
      }
    };

    timeout = window.setTimeout(poll, 2_500);
    return () => {
      cancelled = true;
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [loadWorkflowAssets, shouldPoll, workflowId]);

  async function runCompliance() {
    if (reviewBusy) return;
    if (!invoiceFile || !packingFile) {
      setError("Select both a commercial invoice and a packing-list PDF.");
      return;
    }

    setError(null);
    setAgentError(null);
    setCompliance(null);
    setWorkflow(null);
    setReviewTask(null);
    setEvents([]);

    try {
      let uploadedInvoice = invoiceUpload;
      if (!uploadedInvoice) {
        setPhase("uploading_invoice");
        uploadedInvoice = await api.uploadDocument(
          invoiceFile,
          setInvoiceProgress,
        );
        setInvoiceUpload(uploadedInvoice);
        setInvoiceProgress(100);
        sessionRef.current.addDocument({
          id: uploadedInvoice.document_id,
          name: uploadedInvoice.original_filename,
          role: "commercial_invoice",
          sizeBytes: uploadedInvoice.size_bytes,
          uploadedAt: new Date().toISOString(),
          status: uploadedInvoice.status,
        });
      }

      let uploadedPacking = packingUpload;
      if (!uploadedPacking) {
        setPhase("uploading_packing");
        uploadedPacking = await api.uploadDocument(
          packingFile,
          setPackingProgress,
        );
        setPackingUpload(uploadedPacking);
        setPackingProgress(100);
        sessionRef.current.addDocument({
          id: uploadedPacking.document_id,
          name: uploadedPacking.original_filename,
          role: "packing_list",
          sizeBytes: uploadedPacking.size_bytes,
          uploadedAt: new Date().toISOString(),
          status: uploadedPacking.status,
        });
      }

      setPhase("extracting");
      const [extractedInvoice, extractedPacking] = await Promise.all([
        invoiceExtraction ??
          api.extractDocument(uploadedInvoice.document_id),
        packingExtraction ??
          api.extractDocument(uploadedPacking.document_id),
      ]);
      setInvoiceExtraction(extractedInvoice);
      setPackingExtraction(extractedPacking);
      sessionRef.current.updateDocument(uploadedInvoice.document_id, {
        status: extractedInvoice.status,
        pageCount: extractedInvoice.page_count,
        characterCount: extractedInvoice.character_count,
      });
      sessionRef.current.updateDocument(uploadedPacking.document_id, {
        status: extractedPacking.status,
        pageCount: extractedPacking.page_count,
        characterCount: extractedPacking.character_count,
      });

      const payload: MultiLineShipmentRequest = {
        commercial_invoice_document_id: uploadedInvoice.document_id,
        packing_list_document_id: uploadedPacking.document_id,
        shipment_date: shipmentDate || null,
        letter_of_credit_date: letterOfCreditDate || null,
        additional_uploaded_document_types: [],
        supporting_documents: [],
      };

      setRequestPayload(payload);
      setPhase("checking");
      const result = await api.checkMultiLine(payload);
      setCompliance(result);
      sessionRef.current.markCompliance(
        [uploadedInvoice.document_id, uploadedPacking.document_id],
        result.overall_status,
      );
      setPhase("complete");
    } catch (requestError) {
      setError(readableError(requestError));
      setPhase("idle");
    }
  }

  async function startAgentAudit() {
    if (!requestPayload || agentBusy || workflow) return;
    setAgentBusy(true);
    setAgentError(null);
    try {
      const next = await api.startWorkflow(requestPayload);
      setWorkflow(next);
      if (!pollingStatuses.has(next.status)) {
        await loadWorkflowAssets(next);
      }
    } catch (requestError) {
      setAgentError(readableError(requestError));
    } finally {
      setAgentBusy(false);
    }
  }

  async function submitDecision(
    action: "accept_manual_review" | "reject_submission",
  ) {
    if (!workflow || decisionBusy) return;
    if (
      action === "reject_submission" &&
      !window.confirm(
        "Reject this submission? This decision will be recorded in the audit history.",
      )
    ) {
      return;
    }

    setDecisionBusy(true);
    setAgentError(null);
    try {
      const next = await api.submitReview(workflow.workflow_id, {
        action,
        reviewer_reference: "cace-class-demo-reviewer",
        reason:
          action === "accept_manual_review"
            ? "Reviewed the surfaced findings and accepted the manual-review outcome."
            : "Reviewed the surfaced findings and rejected the submission.",
        review_note:
          action === "accept_manual_review"
            ? "Manual review accepted in the operations console."
            : "Submission rejected in the operations console.",
        provided_document_ids: [],
      });
      setWorkflow(next);
      setReviewTask(null);
      if (!pollingStatuses.has(next.status)) {
        await loadWorkflowAssets(next);
      }
    } catch (requestError) {
      setAgentError(readableError(requestError));
    } finally {
      setDecisionBusy(false);
    }
  }

  function startOver() {
    setInvoiceFile(null);
    setPackingFile(null);
    setShipmentDate("");
    setLetterOfCreditDate("");
    setInvoiceUpload(null);
    setPackingUpload(null);
    setInvoiceExtraction(null);
    setPackingExtraction(null);
    setInvoiceProgress(0);
    setPackingProgress(0);
    setError(null);
    clearWorkflow();
  }

  const stages: ProcessingStage[] = [
    {
      label: "Uploaded",
      description:
        invoiceUpload && packingUpload
          ? "Both PDFs are stored with unique document IDs."
          : "Invoice and packing list are uploaded sequentially.",
      state: stageState(
        Boolean(invoiceUpload && packingUpload),
        ["uploading_invoice", "uploading_packing"].includes(phase),
      ),
    },
    {
      label: "Text extraction",
      description:
        invoiceExtraction && packingExtraction
          ? `${invoiceExtraction.page_count + packingExtraction.page_count} pages extracted across both documents.`
          : "The backend reads the PDF text and uses OCR when required.",
      state: stageState(
        Boolean(invoiceExtraction && packingExtraction),
        phase === "extracting",
      ),
    },
    {
      label: "Structured compliance check",
      description:
        "Document fields are matched and evaluated by deterministic rules.",
      state: stageState(Boolean(compliance), phase === "checking"),
    },
    {
      label: "Review ready",
      description: compliance
        ? "The decision and source-level findings are available below."
        : "Results appear after the compliance service responds.",
      state: stageState(Boolean(compliance), false),
    },
  ];

  return {
    invoiceFile,
    packingFile,
    shipmentDate,
    letterOfCreditDate,
    invoiceProgress,
    packingProgress,
    reviewBusy,
    error,
    compliance,
    workflow,
    reviewTask,
    events,
    agentBusy,
    decisionBusy,
    agentError,
    stages,
    chooseInvoice,
    choosePackingList,
    setShipmentDate,
    setLetterOfCreditDate,
    runCompliance,
    startAgentAudit,
    submitDecision,
    startOver,
    dismissError: () => setError(null),
    dismissAgentError: () => setAgentError(null),
  };
}

export type DocumentReviewController = ReturnType<typeof useDocumentReview>;
