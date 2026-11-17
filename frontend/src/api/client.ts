import type {
  AuditEvent,
  ChatCoverageResponse,
  ChatConversationHistory,
  ChatMessageRequest,
  ChatMessageResponse,
  ChatStreamEvent,
  DocumentExtractionResponse,
  DocumentMetadata,
  DocumentUploadResponse,
  DutyCalculationRequest,
  DutyCalculationResponse,
  EvidenceSearchResponse,
  MultiLineShipmentRequest,
  MultiLineShipmentResponse,
  ReviewTaskResponse,
  ShipmentSearchResponse,
  WorkflowListResponse,
  WorkflowStatusResponse,
  GuidanceRequest,
  GuidanceResponse,
  RegulatoryChatRequest,
  RegulatoryChatResponse,
  ConversationResponse,
  SupportedProductsResponse,
} from "./types";

const configuredBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "").replace(
  /\/+$/,
  "",
);

function apiUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${configuredBaseUrl}${normalizedPath}`;
}

function detailMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return String(item);
      })
      .join("; ");
  }
  return "The server could not complete the request.";
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly detail?: unknown,
    /** Seconds the server asked us to wait, from the Retry-After header. */
    public readonly retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 300_000,
): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(apiUrl(path), {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init.body instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
        ...init.headers,
      },
    });

    const isJson = response.headers
      .get("content-type")
      ?.includes("application/json");
    const payload = isJson ? await response.json() : await response.text();

    if (!response.ok) {
      const detail =
        payload && typeof payload === "object" && "detail" in payload
          ? (payload as { detail: unknown }).detail
          : payload;
      const retryAfterHeader = response.headers.get("retry-after");
      const retryAfterSeconds =
        retryAfterHeader && Number.isFinite(Number(retryAfterHeader))
          ? Number(retryAfterHeader)
          : undefined;
      throw new ApiError(
        detailMessage(detail),
        response.status,
        detail,
        retryAfterSeconds,
      );
    }

    return payload as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(
        "The request timed out. The document may still be processing.",
      );
    }
    throw new ApiError(
      error instanceof Error
        ? error.message
        : "The backend could not be reached.",
    );
  } finally {
    window.clearTimeout(timer);
  }
}

function uploadWithProgress(
  file: File,
  onProgress?: (progress: number) => void,
): Promise<DocumentUploadResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file, file.name);

    xhr.open("POST", apiUrl("/documents/upload"));
    xhr.responseType = "json";
    xhr.timeout = 300_000;
    xhr.setRequestHeader("Accept", "application/json");

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress?.(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response as DocumentUploadResponse);
        return;
      }
      const detail = xhr.response?.detail;
      reject(new ApiError(detailMessage(detail), xhr.status, detail));
    };
    xhr.onerror = () =>
      reject(new ApiError("The upload could not reach the backend."));
    xhr.ontimeout = () =>
      reject(new ApiError("The document upload timed out."));
    xhr.send(form);
  });
}

/**
 * Reads the SSE stream from POST /api/v1/customs-chat/messages.
 *
 * `EventSource` cannot be used here - it only supports GET requests, and
 * this endpoint needs a JSON body. This reads the response body as a raw
 * byte stream and parses "event: ...\ndata: ...\n\n" frames by hand instead.
 */
async function streamCustomsChat(
  payload: ChatMessageRequest,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(apiUrl("/api/v1/customs-chat/messages"), {
    method: "POST",
    signal,
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ ...payload, stream: true }),
  });

  if (!response.ok || !response.body) {
    let detail: unknown;
    try {
      detail = await response.json();
    } catch {
      detail = undefined;
    }
    throw new ApiError(
      detailMessage(
        detail && typeof detail === "object" && "detail" in detail
          ? (detail as { detail: unknown }).detail
          : detail,
      ),
      response.status,
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let frameEnd = buffer.indexOf("\n\n");
    while (frameEnd !== -1) {
      const frame = buffer.slice(0, frameEnd);
      buffer = buffer.slice(frameEnd + 2);

      let eventName = "message";
      const dataParts: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataParts.push(line.slice(5).trim());
        }
      }
      const dataText = dataParts.join("\n");
      if (dataText) {
        try {
          onEvent({
            event: eventName as ChatStreamEvent["event"],
            data: JSON.parse(dataText),
          });
        } catch {
          // A malformed frame is dropped rather than aborting the whole
          // stream - the "done" event still carries the full answer if an
          // earlier "token" frame failed to parse for some reason.
        }
      }
      frameEnd = buffer.indexOf("\n\n");
    }
  }
}

export const api = {
  health: () =>
    request<{ status: string; message: string }>("/health", {}, 20_000),

  databaseHealth: () =>
    request<{ status: string; database: string }>(
      "/health/database",
      {},
      20_000,
    ),

  uploadDocument: uploadWithProgress,

  extractDocument: (documentId: string) =>
    request<DocumentExtractionResponse>(
      `/documents/uploads/${documentId}/extract`,
      { method: "POST" },
    ),

  getDocument: (documentId: string) =>
    request<DocumentMetadata>(`/documents/uploads/${documentId}`),

  checkMultiLine: (payload: MultiLineShipmentRequest) =>
    request<MultiLineShipmentResponse>(
      "/api/v1/compliance/check-documents/multi-line",
      { method: "POST", body: JSON.stringify(payload) },
    ),

  calculateDuties: (payload: DutyCalculationRequest) =>
    request<DutyCalculationResponse>(
      "/api/v1/compliance/calculate-duties",
      { method: "POST", body: JSON.stringify(payload) },
    ),

  startWorkflow: (payload: MultiLineShipmentRequest) =>
    request<WorkflowStatusResponse>("/api/v1/customs-audit/workflows", {
      method: "POST",
      body: JSON.stringify({
        ...payload,
        additional_document_ids: [],
      }),
    }),

  getWorkflow: (workflowId: string) =>
    request<WorkflowStatusResponse>(
      `/api/v1/customs-audit/workflows/${workflowId}`,
    ),

  listWorkflows: (limit = 10) =>
    request<WorkflowListResponse>(
      `/api/v1/customs-audit/workflows?limit=${limit}`,
    ),

  getReview: (workflowId: string) =>
    request<ReviewTaskResponse>(
      `/api/v1/customs-audit/workflows/${workflowId}/review`,
    ),

  submitReview: (
    workflowId: string,
    payload: Record<string, unknown>,
  ) =>
    request<WorkflowStatusResponse>(
      `/api/v1/customs-audit/workflows/${workflowId}/review`,
      { method: "POST", body: JSON.stringify(payload) },
    ),

  getEvents: (workflowId: string) =>
    request<{ workflow_id: string; events: AuditEvent[] }>(
      `/api/v1/customs-audit/workflows/${workflowId}/events`,
    ),

  searchEvidence: (payload: {
    query: string;
    pct_code?: string;
    top_k: number;
    verified_only: boolean;
  }) =>
    request<EvidenceSearchResponse>(
      "/api/v1/regulatory-evidence/search",
      { method: "POST", body: JSON.stringify(payload) },
      180_000,
    ),

  searchShipments: (query: string) =>
    request<ShipmentSearchResponse>("/api/v1/shipment-search/search", {
      method: "POST",
      body: JSON.stringify({ query, top_k: 5 }),
    }),

  getGuidance: (payload: GuidanceRequest) =>
    request<GuidanceResponse>("/api/v1/assistant/guidance", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getSupportedProducts: () =>
    request<SupportedProductsResponse>("/api/v1/assistant/supported-products"),

  getConversation: (conversationId: string) =>
    request<ConversationResponse>(
      `/api/v1/assistant/conversations/${conversationId}`,
    ),

  askRegulatory: (payload: RegulatoryChatRequest) =>
    request<RegulatoryChatResponse>("/api/v1/assistant/regulatory/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  askCustomsChat: (payload: ChatMessageRequest) =>
    request<ChatMessageResponse>(
      "/api/v1/customs-chat/messages",
      { method: "POST", body: JSON.stringify({ ...payload, stream: false }) },
      120_000,
    ),

  streamCustomsChat,

  getCustomsChatConversation: (conversationId: string) =>
    request<ChatConversationHistory>(
      `/api/v1/customs-chat/conversations/${conversationId}`,
    ),

  getCustomsChatSuggestions: () =>
    request<{ suggestions: string[] }>("/api/v1/customs-chat/suggestions"),

  postCustomsChatFeedback: (payload: {
    message_id: string;
    rating: "up" | "down";
    comment?: string;
  }) =>
    request<void>("/api/v1/customs-chat/feedback", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getCustomsChatCoverage: () =>
    request<ChatCoverageResponse>("/api/v1/customs-chat/coverage"),
};
