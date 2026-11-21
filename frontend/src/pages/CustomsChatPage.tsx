import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  Loader2,
  Send,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { api } from "../api/client";
import type {
  ChatChecklistTool,
  ChatConfidence,
  ChatCoverageResponse,
  ChatRing,
  ChatSourceSchema,
  ChatStreamEvent,
  ChatTariffLookup,
  EvidenceBand,
} from "../api/types";
import { PageHeader } from "../components/PageHeader";

interface ChatStageItem {
  stage: string;
  label: string;
}

const STAGE_LABELS: Record<string, string> = {
  input_guard: "Checking question safety",
  scope_router: "Confirming customs scope",
  query_rewriter: "Interpreting question",
  shipment_context_lookup: "Loading shipment records",
  shipment_evidence_retrieval: "Checking shipment evidence",
  checklist_tool: "Running compliance checklist",
  tariff_lookup: "Looking up tariff schedule",
  hybrid_retrieval: "Searching regulatory sources",
  answer_generation: "Drafting answer",
  output_guard: "Verifying numbers and claims",
  groundedness_verifier: "Double-checking source citations",
};

function stageLabel(stage: string, label?: string): string {
  if (label && label.trim()) return label;
  const mapped = STAGE_LABELS[stage];
  if (mapped) return mapped;
  const words = stage.replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Processing";
}

interface ChatTurnUI {
  id: string;
  role: "user" | "assistant";
  text: string;
  mode?: ChatRing;
  evidenceBand?: EvidenceBand;
  confidence?: ChatConfidence;
  sources?: ChatSourceSchema[];
  followUps?: string[];
  disclaimer?: string | null;
  checklistTool?: ChatChecklistTool | null;
  tariffLookup?: ChatTariffLookup | null;
  feedback?: "up" | "down" | null;
  streaming?: boolean;
  failed?: boolean;
  stages?: ChatStageItem[];
}

const MODE_LABEL: Record<ChatRing, string> = {
  GROUNDED: "Official sources",
  GENERAL: "General explanation",
  OUT_OF_SCOPE: "Not covered",
  REFUSAL: "Not covered",
};

const MODE_CLASS: Record<ChatRing, string> = {
  GROUNDED: "mode-badge--grounded",
  GENERAL: "mode-badge--general",
  OUT_OF_SCOPE: "mode-badge--refusal",
  REFUSAL: "mode-badge--refusal",
};

function ModeBadge({ mode }: { mode?: ChatRing }) {
  if (!mode) return null;
  return (
    <span className={`mode-badge ${MODE_CLASS[mode]}`}>{MODE_LABEL[mode]}</span>
  );
}

function ChatSourceCard({ source }: { source: ChatSourceSchema }) {
  return (
    <li className="citation-card">
      <div className="citation-card__head">
        <BookOpen size={14} aria-hidden="true" />
        <strong>{source.document}</strong>
      </div>
      <div className="citation-card__provenance">
        <span
          className={`source-kind-chip source-kind-chip--${
            source.is_official ? "official" : "curated"
          }`}
        >
          {source.kind_label}
        </span>
        {source.reference ? (
          <span className="citation-card__section">{source.reference}</span>
        ) : null}
        {source.currency_status && source.currency_status !== "current" ? (
          <span className="citation-card__section">{source.currency_status}</span>
        ) : null}
      </div>
      {source.url ? (
        <p className="citation-card__section">
          <a href={source.url} target="_blank" rel="noreferrer">
            {source.url}
          </a>
        </p>
      ) : null}
      <details className="citation-card__technical">
        <summary>Excerpt</summary>
        <p className="citation-card__snippet">{source.excerpt}</p>
      </details>
    </li>
  );
}

function ChecklistToolCard({ tool }: { tool: ChatChecklistTool }) {
  return (
    <div className="checklist">
      <p className="chat-tool-card__title">
        {tool.product} &middot; PCT {tool.pct_code} &middot; to {tool.destination}
      </p>
      {tool.required_documents.length > 0 ? (
        <section className="checklist__group">
          <h4>Required documents</h4>
          <ul className="plain-list">
            {tool.required_documents.map((doc) => (
              <li key={doc.display_name}>{doc.display_name}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {tool.conditional_documents.length > 0 ? (
        <section className="checklist__group">
          <h4>Conditional documents</h4>
          <ul className="plain-list">
            {tool.conditional_documents.map((doc) => (
              <li key={doc.display_name}>
                {doc.display_name}
                {doc.condition ? (
                  <span className="muted"> — {doc.condition}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function TariffLookupCard({ lookup }: { lookup: ChatTariffLookup }) {
  if (!lookup.matched) {
    return (
      <div className="chat-tool-card">
        <p>No tariff entry found for {lookup.query_code}.</p>
      </div>
    );
  }
  if (!lookup.exact) {
    return (
      <div className="chat-tool-card">
        <p className="chat-tool-card__title">Possible matches for {lookup.query_code}</p>
        <ul className="plain-list">
          {lookup.candidates.map((candidate) => (
            <li key={candidate}>{candidate}</li>
          ))}
        </ul>
      </div>
    );
  }
  return (
    <div className="chat-tool-card">
      <p className="chat-tool-card__title">HS code {lookup.hs_code}</p>
      <p>{lookup.description}</p>
      {lookup.needs_review ? (
        <p className="muted">
          The duty rate for this line could not be confirmed from the indexed
          tariff - check it directly before relying on it.
        </p>
      ) : (
        <>
          <p>
            Customs duty: <strong>{lookup.customs_duty_pct}%</strong>
            {lookup.source_page ? ` (tariff page ${lookup.source_page})` : null}
          </p>
          {lookup.cascading_tax_breakdown ? (
            <details className="citation-card__technical">
              <summary>
                2026–27 Cascading Tax Breakdown (effective{" "}
                {lookup.cascading_tax_breakdown.effective_rate_pct}%)
              </summary>
              <ul className="plain-list">
                <li>
                  Assessed Value (V): {lookup.cascading_tax_breakdown.assessed_value}
                </li>
                <li>
                  Customs Duty ({lookup.cascading_tax_breakdown.customs_duty_pct}%):{" "}
                  {lookup.cascading_tax_breakdown.customs_duty}
                </li>
                <li>
                  Additional Customs Duty (
                  {lookup.cascading_tax_breakdown.additional_customs_duty_pct}%, S.R.O.
                  1063(I)/2026): {lookup.cascading_tax_breakdown.additional_customs_duty}
                </li>
                <li>
                  Regulatory Duty ({lookup.cascading_tax_breakdown.regulatory_duty_pct}%):{" "}
                  {lookup.cascading_tax_breakdown.regulatory_duty}
                </li>
                <li>
                  Sales Tax ({lookup.cascading_tax_breakdown.sales_tax_pct}% on{" "}
                  {lookup.cascading_tax_breakdown.sales_tax_base}):{" "}
                  {lookup.cascading_tax_breakdown.sales_tax}
                </li>
                <li>
                  Value Addition Tax (
                  {lookup.cascading_tax_breakdown.value_addition_tax_pct}%):{" "}
                  {lookup.cascading_tax_breakdown.value_addition_tax}
                </li>
                <li>
                  Advance Income Tax Sec. 148 (
                  {lookup.cascading_tax_breakdown.advance_income_tax_pct}% on{" "}
                  {lookup.cascading_tax_breakdown.income_tax_base}):{" "}
                  {lookup.cascading_tax_breakdown.advance_income_tax}
                </li>
                <li>
                  <strong>
                    Total Duties &amp; Taxes:{" "}
                    {lookup.cascading_tax_breakdown.total_duties_and_taxes} (Landed
                    Cost: {lookup.cascading_tax_breakdown.total_landed_cost})
                  </strong>
                </li>
              </ul>
            </details>
          ) : null}
        </>
      )}
    </div>
  );
}

export function CustomsChatPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeShipmentId = searchParams.get("shipment_id");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatTurnUI[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [coverage, setCoverage] = useState<ChatCoverageResponse | null>(null);
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [coverageLoading, setCoverageLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollAnchorRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setConversationId(null);
    setMessages([]);
  }, [activeShipmentId]);

  useEffect(() => {
    let active = true;
    api
      .getCustomsChatSuggestions()
      .then((response) => active && setSuggestions(response.suggestions))
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  const toggleCoverage = () => {
    const next = !coverageOpen;
    setCoverageOpen(next);
    if (next && !coverage && !coverageLoading) {
      setCoverageLoading(true);
      api
        .getCustomsChatCoverage()
        .then(setCoverage)
        .catch(() => undefined)
        .finally(() => setCoverageLoading(false));
    }
  };

  const ask = async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed || busy) return;

    setError(null);
    setInput("");
    setBusy(true);

    const userTurn: ChatTurnUI = {
      id: crypto.randomUUID(),
      role: "user",
      text: trimmed,
    };
    const assistantTurnId = crypto.randomUUID();
    const assistantTurn: ChatTurnUI = {
      id: assistantTurnId,
      role: "assistant",
      text: "",
      streaming: true,
    };
    setMessages((prev) => [...prev, userTurn, assistantTurn]);

    const controller = new AbortController();
    abortRef.current = controller;

    const patchAssistant = (patch: Partial<ChatTurnUI>) => {
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantTurnId ? { ...message, ...patch } : message,
        ),
      );
    };

    let resolvedMessageId: string = assistantTurnId;

    const onEvent = (event: ChatStreamEvent) => {
      switch (event.event) {
        case "stage": {
          const data = event.data as { stage: string; label?: string };
          const nextStage: ChatStageItem = {
            stage: data.stage,
            label: stageLabel(data.stage, data.label),
          };
          setMessages((prev) =>
            prev.map((message) => {
              if (message.id !== assistantTurnId) return message;
              const existing = message.stages ?? [];
              if (existing.some((item) => item.stage === nextStage.stage)) {
                return message;
              }
              return {
                ...message,
                stages: [...existing, nextStage],
              };
            }),
          );
          break;
        }
        case "meta": {
          const data = event.data as {
            conversation_id: string;
            message_id: string;
            mode: ChatRing;
            evidence_band: EvidenceBand;
          };
          setConversationId(data.conversation_id);
          resolvedMessageId = data.message_id;
          patchAssistant({ mode: data.mode, evidenceBand: data.evidence_band });
          break;
        }
        case "token": {
          const data = event.data as { text: string };
          setMessages((prev) =>
            prev.map((message) =>
              message.id === assistantTurnId
                ? { ...message, text: message.text + data.text }
                : message,
            ),
          );
          break;
        }
        case "sources": {
          const data = event.data as { sources: ChatSourceSchema[] };
          patchAssistant({ sources: data.sources });
          break;
        }
        case "tool_result": {
          const data = event.data as {
            checklist_tool: ChatChecklistTool | null;
            tariff_lookup: ChatTariffLookup | null;
          };
          patchAssistant({
            checklistTool: data.checklist_tool,
            tariffLookup: data.tariff_lookup,
          });
          break;
        }
        case "done": {
          const data = event.data as {
            confidence: ChatConfidence;
            follow_ups: string[];
            disclaimer: string | null;
          };
          patchAssistant({
            confidence: data.confidence,
            followUps: data.follow_ups,
            disclaimer: data.disclaimer,
            streaming: false,
          });
          break;
        }
        case "error": {
          const data = event.data as { detail?: string };
          patchAssistant({ streaming: false, failed: true });
          setError(data.detail || "The assistant could not complete this request.");
          break;
        }
      }
    };

    try {
      await api.streamCustomsChat(
        {
          message: trimmed,
          conversation_id: conversationId,
          shipment_id: activeShipmentId,
        },
        onEvent,
        controller.signal,
      );
    } catch (err) {
      patchAssistant({ streaming: false, failed: true });
      setError(
        err instanceof Error ? err.message : "The assistant could not be reached.",
      );
    } finally {
      // Swap the client-generated placeholder id for the server's real
      // message_id (from the "meta" event), so feedback later references
      // the id the backend actually persisted.
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantTurnId ? { ...message, id: resolvedMessageId } : message,
        ),
      );
      setBusy(false);
    }
  };

  const sendFeedback = (turnId: string, rating: "up" | "down") => {
    setMessages((prev) =>
      prev.map((message) =>
        message.id === turnId ? { ...message, feedback: rating } : message,
      ),
    );
    void api.postCustomsChatFeedback({ message_id: turnId, rating }).catch(() => {
      // Feedback is best-effort; failing to record it should not disrupt
      // the conversation the user is having.
    });
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask(input);
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="Ask CACE"
        title="Customs Assistant"
        description="Ask about Pakistan customs - importing, exporting, duties, documents and procedures - in plain English. Answers are grounded in official sources where possible, and clearly labelled when they are not."
      />

      {activeShipmentId ? (
        <div className="notice notice--info overview-note">
          <BookOpen size={18} aria-hidden="true" />
          <div>
            <strong>Shipment-scoped assistant ({activeShipmentId.slice(0, 8)}…)</strong>
            <p>
              Questions in this session are answered using this shipment&apos;s
              extracted documents and frozen audit findings.{" "}
              <button
                type="button"
                className="text-link"
                onClick={() => setSearchParams({})}
              >
                Switch to general customs chat
              </button>
            </p>
          </div>
        </div>
      ) : null}

      <div className="layout-split">
        <div className="layout-main">
          <section className="panel">
            <div className="panel__body">
              {messages.length === 0 ? (
                <div className="chat-welcome">
                  <p className="muted">
                    Ask a question about Pakistan customs - imports, exports,
                    duties, documents, or what to do if a shipment is held.
                  </p>
                  {suggestions.length > 0 ? (
                    <div className="chat-welcome__grid">
                      {suggestions.map((question) => (
                        <button
                          key={question}
                          type="button"
                          className="chat-starter-card"
                          onClick={() => void ask(question)}
                        >
                          {question}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}

              {messages.map((message) => (
                <article
                  key={message.id}
                  className={`chat-message chat-message--${message.role}`}
                >
                  <header className="chat-message__head">
                    <strong>{message.role === "user" ? "You" : "Customs Assistant"}</strong>
                    {message.role === "assistant" ? <ModeBadge mode={message.mode} /> : null}
                  </header>

                  {message.streaming && !message.text && message.stages && message.stages.length > 0 ? (
                    <div className="chat-stage-progress" role="status" aria-live="polite">
                      <ul className="chat-stage-progress__list">
                        {message.stages.map((item, index) => {
                          const isLast = index === (message.stages?.length ?? 0) - 1;
                          return (
                            <li
                              key={item.stage}
                              className={`chat-stage-progress__item ${
                                isLast
                                  ? "chat-stage-progress__item--active"
                                  : "chat-stage-progress__item--done"
                              }`}
                            >
                              {isLast ? (
                                <Loader2 className="spinner" size={13} aria-hidden="true" />
                              ) : (
                                <CheckCircle2 size={13} aria-hidden="true" />
                              )}
                              <span>
                                {item.label}
                                {isLast ? "…" : ""}
                              </span>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  ) : null}

                  {!(message.streaming && !message.text && message.stages?.length) ? (
                    <p className="chat-message__text">
                      {message.failed && !message.text ? (
                        <span className="muted">No response was received.</span>
                      ) : (
                        message.text
                      )}
                      {message.streaming ? <span className="chat-cursor" /> : null}
                    </p>
                  ) : null}

                  {message.checklistTool ? (
                    <ChecklistToolCard tool={message.checklistTool} />
                  ) : null}
                  {message.tariffLookup ? (
                    <TariffLookupCard lookup={message.tariffLookup} />
                  ) : null}

                  {message.disclaimer ? (
                    <p className="chat-message__disclaimer">{message.disclaimer}</p>
                  ) : null}

                  {message.sources && message.sources.length > 0 ? (
                    <details className="citation-card__technical">
                      <summary>Sources ({message.sources.length})</summary>
                      <ul className="citation-list">
                        {message.sources.map((source, index) => (
                          <ChatSourceCard key={`${message.id}-${index}`} source={source} />
                        ))}
                      </ul>
                    </details>
                  ) : null}

                  {message.role === "assistant" &&
                  !message.streaming &&
                  !message.failed &&
                  message.stages &&
                  message.stages.length > 0 ? (
                    <details className="citation-card__technical chat-stage-trace">
                      <summary>Verification steps ({message.stages.length})</summary>
                      <ol className="chat-stage-trace__list">
                        {message.stages.map((item) => (
                          <li key={item.stage}>{item.label}</li>
                        ))}
                      </ol>
                    </details>
                  ) : null}

                  {message.role === "assistant" && !message.streaming && !message.failed ? (
                    <div className="chat-message__footer">
                      <div className="feedback-buttons">
                        <button
                          type="button"
                          className={`icon-button ${message.feedback === "up" ? "icon-button--active" : ""}`}
                          aria-label="Helpful"
                          onClick={() => sendFeedback(message.id, "up")}
                        >
                          <ThumbsUp size={14} />
                        </button>
                        <button
                          type="button"
                          className={`icon-button ${message.feedback === "down" ? "icon-button--active" : ""}`}
                          aria-label="Not helpful"
                          onClick={() => sendFeedback(message.id, "down")}
                        >
                          <ThumbsDown size={14} />
                        </button>
                      </div>
                      {message.followUps && message.followUps.length > 0 ? (
                        <div className="follow-up-chips">
                          {message.followUps.map((followUp) => (
                            <button
                              key={followUp}
                              type="button"
                              className="follow-up-chip"
                              onClick={() => void ask(followUp)}
                              disabled={busy}
                            >
                              {followUp}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </article>
              ))}

              {error ? (
                <div className="notice notice--danger">
                  <AlertTriangle size={18} aria-hidden="true" />
                  <div>
                    <strong>Error</strong>
                    <p>{error}</p>
                  </div>
                </div>
              ) : null}

              <div ref={scrollAnchorRef} />

              <form
                className="chat-composer"
                onSubmit={(event) => {
                  event.preventDefault();
                  void ask(input);
                }}
              >
                <label className="sr-only" htmlFor="customs-chat-question">
                  Your question
                </label>
                <textarea
                  id="customs-chat-question"
                  className="text-input"
                  rows={3}
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="e.g. What documents do I need to export cotton yarn? (Enter to send, Shift+Enter for a new line)"
                  disabled={busy}
                />
                <button
                  type="submit"
                  className="button button--primary"
                  disabled={busy || !input.trim()}
                >
                  {busy ? (
                    <Loader2 className="spinner" size={16} aria-hidden="true" />
                  ) : (
                    <Send size={16} aria-hidden="true" />
                  )}
                  Send
                </button>
              </form>
            </div>
          </section>
        </div>

        <aside className="layout-sidebar">
          <div className="panel">
            <div className="panel__header">
              <h3>What I can help with</h3>
            </div>
            <div className="panel__body">
              <ul className="plain-list">
                <li>Importing and exporting: rules, procedures, documents</li>
                <li>Customs duties, taxes, and HS codes</li>
                <li>Pakistan Single Window and WeBOC</li>
                <li>Baggage, personal imports, couriers and parcels</li>
                <li>What to do if a shipment is held or you want to appeal</li>
              </ul>
            </div>
          </div>

          <div className="panel">
            <button
              type="button"
              className="panel__header panel__header--button"
              onClick={toggleCoverage}
            >
              <h3>What the corpus covers</h3>
              <ChevronDown
                size={16}
                aria-hidden="true"
                style={{
                  transform: coverageOpen ? "rotate(180deg)" : "none",
                  transition: "transform 0.15s ease",
                }}
              />
            </button>
            {coverageOpen ? (
              <div className="panel__body">
                {coverageLoading ? (
                  <p className="muted">
                    <Loader2 className="spinner" size={14} aria-hidden="true" /> Loading…
                  </p>
                ) : coverage ? (
                  <>
                    <p className="muted">
                      {coverage.indexed_documents.length} indexed source
                      {coverage.indexed_documents.length === 1 ? "" : "s"}
                      {coverage.corpus_snapshot_date
                        ? ` · snapshot ${coverage.corpus_snapshot_date}`
                        : ""}
                    </p>
                    <h4>Known gaps</h4>
                    <ul className="plain-list">
                      {coverage.known_gaps.map((gap) => (
                        <li key={gap}>{gap}</li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <p className="muted">Could not load coverage information.</p>
                )}
              </div>
            ) : null}
          </div>
        </aside>
      </div>
    </>
  );
}
