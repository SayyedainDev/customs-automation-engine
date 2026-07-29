import { useState } from "react";
import type { KeyboardEvent } from "react";
import {
  AlertTriangle,
  BookOpen,
  Loader2,
  Send,
  ShieldAlert,
} from "lucide-react";
import { api } from "../api/client";
import type {
  ChecklistDocument,
  ProductCandidate,
  RegulatoryCitation,
  RegulatoryChatResponse,
} from "../api/types";
import { PageHeader } from "../components/PageHeader";

const SUGGESTED_QUESTIONS = [
  "What is Form-E?",
  "What is a Certificate of Origin?",
  "Which indexed sources explain PSW export declarations?",
  "What documents normally describe the goods in a shipment?",
  "Which sources mention phytosanitary requirements?",
  "What does the indexed corpus say about cotton exports?",
  "Search for references to PCT 52010090.",
  "How does CACE distinguish official sources from curated summaries?",
];

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  intent?: string;
  evidenceStatus?: string;
  evidenceScope?: string;
  answerMode?: string;
  requiredDocuments?: ChecklistDocument[];
  conditionalDocuments?: ChecklistDocument[];
  productCandidates?: ProductCandidate[];
  interpretedAs?: Record<string, string>;
  informationalOnly?: boolean;
  sources?: RegulatoryCitation[];
  limitations?: string[];
  supportedScope?: string[];
}

function ChecklistSections({
  required,
  conditional,
  candidates,
}: {
  required: ChecklistDocument[];
  conditional: ChecklistDocument[];
  candidates: ProductCandidate[];
}) {
  if (required.length === 0 && conditional.length === 0 && candidates.length === 0) {
    return null;
  }
  return (
    <div className="checklist">
      {required.length > 0 ? (
        <section className="checklist__group">
          <h4>Required documents</h4>
          <ul className="plain-list">
            {required.map((doc) => (
              <li key={doc.display_name}>{doc.display_name}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {conditional.length > 0 ? (
        <section className="checklist__group">
          <h4>Conditional documents</h4>
          <ul className="plain-list">
            {conditional.map((doc) => (
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
      {candidates.length > 0 ? (
        <section className="checklist__group">
          <h4>Which product do you mean?</h4>
          <ul className="plain-list">
            {candidates.map((candidate) => (
              <li key={candidate.pct_code}>
                {candidate.product_name} — PCT {candidate.pct_code}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function SourceCard({ citation }: { citation: RegulatoryCitation }) {
  const provenance = [
    citation.issuing_authority,
    citation.page_number != null ? `page ${citation.page_number}` : citation.section,
    citation.sro_number ? `SRO ${citation.sro_number}` : null,
  ].filter(Boolean);

  const dates = [
    citation.publication_date ? `published ${citation.publication_date}` : null,
    citation.effective_date ? `effective ${citation.effective_date}` : null,
    citation.corpus_snapshot_date
      ? `corpus snapshot ${citation.corpus_snapshot_date}`
      : null,
  ].filter(Boolean);

  return (
    <li className="citation-card">
      <div className="citation-card__head">
        <BookOpen size={14} aria-hidden="true" />
        <strong>{citation.title}</strong>
      </div>
      <div className="citation-card__provenance">
        <span
          className={`source-kind-chip source-kind-chip--${
            citation.is_official ? "official" : "curated"
          }`}
        >
          {citation.source_kind_label}
        </span>
        {provenance.length > 0 ? (
          <span className="citation-card__section">{provenance.join(" · ")}</span>
        ) : null}
      </div>
      {dates.length > 0 ? (
        <p className="citation-card__section">{dates.join(" · ")}</p>
      ) : null}
      {citation.referenced_official_source ? (
        <p className="citation-card__section">
          References official source:{" "}
          <a
            href={citation.referenced_official_source}
            target="_blank"
            rel="noreferrer"
          >
            {citation.referenced_official_source}
          </a>
        </p>
      ) : null}
      <details className="citation-card__technical">
        <summary>Accepted passage · {citation.evidence_status}</summary>
        <p className="citation-card__snippet">{citation.accepted_passage}</p>
      </details>
    </li>
  );
}

export function RegulatoryAssistantPage() {
  const [activeQuestion, setActiveQuestion] = useState<ChatMessage | null>(null);
  const [activeAnswer, setActiveAnswer] = useState<ChatMessage | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed || busy) return;

    setError(null);
    setInput("");
    // Replace the previous turn before the network request starts. This
    // prevents stale answers and source cards from remaining visible while a
    // new question loads.
    setActiveQuestion({
      id: crypto.randomUUID(),
      role: "user",
      text: trimmed,
    });
    setActiveAnswer(null);
    setBusy(true);

    try {
      const response: RegulatoryChatResponse = await api.askRegulatory({
        question: trimmed,
        top_k: 3,
      });
      setActiveAnswer({
        id: response.message_id,
        role: "assistant",
        text: response.answer,
        intent: response.intent,
        evidenceStatus: response.evidence_status,
        evidenceScope: response.evidence_scope,
        answerMode: response.answer_mode,
        requiredDocuments: response.required_documents,
        conditionalDocuments: response.conditional_documents,
        productCandidates: response.product_candidates,
        interpretedAs: response.interpreted_as,
        informationalOnly: response.informational_only,
        sources: response.sources.slice(0, 3),
        limitations: response.limitations,
        supportedScope: response.supported_compliance_scope,
      });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "The assistant could not be reached.",
      );
    } finally {
      setBusy(false);
    }
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
        title="Regulatory assistant"
        description="Ask about customs, export documentation and the regulatory sources indexed by CACE. Answers quote the indexed corpus and cite the exact source. This assistant is informational: deterministic compliance decisions are available for 17 validated textile PCT codes in Prepare an Export."
      />

      <div className="layout-split">
        <div className="layout-main">
          <section className="panel">
            <div className="panel__header">
              <h2>Current question</h2>
            </div>
            <div className="panel__body">
              {!activeQuestion ? (
                <p className="muted">
                  Ask a question about export documentation, trade regulation or
                  the indexed regulatory sources. Questions outside customs and
                  trade are declined.
                </p>
              ) : null}

              {[activeQuestion, activeAnswer].filter(
                (message): message is ChatMessage => message !== null,
              ).map((message) => (
                <article
                  key={message.id}
                  className={`chat-message chat-message--${message.role}`}
                >
                  <header className="chat-message__head">
                    <strong>{message.role === "user" ? "You" : "CACE"}</strong>
                  </header>

                  <p className="chat-message__text">
                    {message.answerMode === "checklist" ||
                    message.answerMode === "clarification"
                      ? message.text.split("\n\n")[0]
                      : message.text}
                  </p>

                  {message.answerMode === "checklist" ||
                  message.answerMode === "clarification" ? (
                    <ChecklistSections
                      required={message.requiredDocuments ?? []}
                      conditional={message.conditionalDocuments ?? []}
                      candidates={message.productCandidates ?? []}
                    />
                  ) : null}

                  {message.informationalOnly && message.role === "assistant" ? (
                    <div className="notice notice--warning">
                      <ShieldAlert size={16} aria-hidden="true" />
                      <div>
                        <strong>Informational answer</strong>
                        <p>
                          This is informational. Deterministic compliance
                          decisions cover{" "}
                          {(message.supportedScope ?? []).length} validated
                          textile PCT codes.
                        </p>
                      </div>
                    </div>
                  ) : null}

                  {message.sources && message.sources.length > 0 ? (
                    <details className="citation-card__technical">
                      <summary>
                        Relevant sources ({message.sources.length})
                      </summary>
                      <ul className="citation-list">
                        {message.sources.map((citation, index) => (
                          <SourceCard
                            key={`${message.id}-${index}`}
                            citation={citation}
                          />
                        ))}
                      </ul>
                    </details>
                  ) : null}

                  {message.limitations && message.limitations.length > 0 ? (
                    <details className="citation-card__technical">
                      <summary>Limitations</summary>
                      <ul className="plain-list">
                        {message.limitations.map((limitation, index) => (
                          <li key={index}>{limitation}</li>
                        ))}
                      </ul>
                    </details>
                  ) : null}
                </article>
              ))}

              {busy ? (
                <p className="muted">
                  <Loader2 className="spin" size={16} aria-hidden="true" /> Searching
                  the regulatory corpus…
                </p>
              ) : null}

              {error ? (
                <div className="notice notice--danger">
                  <AlertTriangle size={18} aria-hidden="true" />
                  <div>
                    <strong>Error</strong>
                    <p>{error}</p>
                  </div>
                </div>
              ) : null}

              <form
                className="chat-composer"
                onSubmit={(event) => {
                  event.preventDefault();
                  void ask(input);
                }}
              >
                <label className="sr-only" htmlFor="regulatory-question">
                  Your question
                </label>
                <textarea
                  id="regulatory-question"
                  className="text-input"
                  rows={3}
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="e.g. What is Form-E?  (Enter to send, Shift+Enter for a new line)"
                  disabled={busy}
                />
                <button
                  type="submit"
                  className="button button--primary"
                  disabled={busy || !input.trim()}
                >
                  {busy ? (
                    <Loader2 className="spin" size={16} aria-hidden="true" />
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
              <h3>Suggested questions</h3>
            </div>
            <div className="panel__body">
              <ul className="plain-list">
                {SUGGESTED_QUESTIONS.map((question) => (
                  <li key={question}>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => void ask(question)}
                      disabled={busy}
                    >
                      {question}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <div className="panel">
            <div className="panel__header">
              <h3>Scope</h3>
            </div>
            <div className="panel__body">
              <ul className="plain-list">
                <li>
                  Searches every active regulatory source indexed by CACE, not
                  only the supported PCT codes.
                </li>
                <li>
                  Deterministic compliance decisions are available for 17
                  validated textile PCT codes in Prepare an Export.
                </li>
                <li>
                  Shipment-specific questions need a shipment to be selected
                  first; this conversation is not attached to one.
                </li>
                <li>
                  Answers reflect a fixed corpus snapshot and are not official
                  customs or legal advice.
                </li>
              </ul>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}
