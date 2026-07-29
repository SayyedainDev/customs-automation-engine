import {
  BookOpenCheck,
  ExternalLink,
  FileSearch,
  History,
  LoaderCircle,
  Search,
} from "lucide-react";
import { type FormEvent, type KeyboardEvent, useState } from "react";
import { api } from "../api/client";
import type {
  EvidenceResult,
  EvidenceSearchResponse,
  ShipmentSearchResponse,
} from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { labelize } from "../lib/format";

type SearchTab = "evidence" | "shipments";

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The search request could not be completed.";
}

function legalDate(value: string | null): string {
  if (!value) return "Not stated";

  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;

  return new Intl.DateTimeFormat("en-PK", {
    dateStyle: "medium",
  }).format(parsed);
}

function EvidenceCard({
  evidence,
  rank,
}: {
  evidence: EvidenceResult;
  rank: number;
}) {
  return (
    <article className="search-result evidence-result">
      <header className="search-result__header">
        <div>
          <p className="eyebrow">Evidence {rank}</p>
          <h3>{evidence.source_document}</h3>
        </div>
        <StatusBadge status={evidence.validation_status} />
      </header>

      <blockquote className="evidence-result__passage">
        {evidence.child_evidence_text}
      </blockquote>

      <dl className="metadata-list metadata-list--compact">
        <div>
          <dt>SRO reference</dt>
          <dd>{evidence.sro_number ?? "Not stated"}</dd>
        </div>
        <div>
          <dt>Page</dt>
          <dd>{evidence.page_number ?? "Not stated"}</dd>
        </div>
        <div>
          <dt>Section</dt>
          <dd>{evidence.section ?? "Not stated"}</dd>
        </div>
        <div>
          <dt>Issuing authority</dt>
          <dd>{evidence.issuing_authority ?? "Not stated"}</dd>
        </div>
        <div>
          <dt>Effective date</dt>
          <dd>{legalDate(evidence.effective_date)}</dd>
        </div>
        <div>
          <dt>Legal cutoff</dt>
          <dd>{legalDate(evidence.legal_cutoff_date)}</dd>
        </div>
        <div>
          <dt>Rule data version</dt>
          <dd>{evidence.rule_data_version}</dd>
        </div>
        <div>
          <dt>Reranker score</dt>
          <dd>{evidence.cross_encoder_score.toFixed(3)}</dd>
        </div>
      </dl>

      <div className="search-result__source">
        <span>
          Source path: <code>{evidence.source_path}</code>
        </span>
        {evidence.source_url ? (
          <a
            className="text-link"
            href={evidence.source_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open source
            <ExternalLink aria-hidden="true" size={14} />
          </a>
        ) : null}
      </div>

      {evidence.parent_evidence_text &&
      evidence.parent_evidence_text !== evidence.child_evidence_text ? (
        <details className="evidence-result__context">
          <summary>View wider source context</summary>
          <p>{evidence.parent_evidence_text}</p>
        </details>
      ) : null}
    </article>
  );
}

function RegulatoryEvidencePanel() {
  const [query, setQuery] = useState("");
  const [pctCode, setPctCode] = useState("");
  const [response, setResponse] = useState<EvidenceSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedQuery = query.trim();
    const normalizedPctCode = pctCode.trim();
    if (!normalizedQuery || loading) return;

    setLoading(true);
    setError(null);
    setResponse(null);

    try {
      const result = await api.searchEvidence({
        query: normalizedQuery,
        ...(normalizedPctCode ? { pct_code: normalizedPctCode } : {}),
        top_k: 5,
        verified_only: true,
      });
      setResponse(result);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setLoading(false);
    }
  }

  const noEvidence =
    response?.status === "evidence_not_found" ||
    (response !== null && response.results.length === 0);

  return (
    <section
      id="search-panel-evidence"
      className="search-panel"
      role="tabpanel"
      aria-labelledby="search-tab-evidence"
    >
      <div className="panel search-form-panel">
        <div className="panel__header">
          <div>
            <h2>Regulatory evidence</h2>
            <p>
              Search the indexed rulebook and return passages with legal
              provenance.
            </p>
          </div>
        </div>

        <form className="search-form" onSubmit={submit}>
          <div className="search-form__fields">
            <div className="field search-form__query">
              <label htmlFor="evidence-query">Search question</label>
              <input
                id="evidence-query"
                name="evidence-query"
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="For example: raw cotton export requirements"
                autoComplete="off"
                required
                disabled={loading}
                aria-describedby="evidence-query-help"
              />
              <p id="evidence-query-help" className="field__help">
                Use a specific product, document requirement, restriction, or
                destination.
              </p>
            </div>

            <div className="field search-form__filter">
              <label htmlFor="evidence-pct">PCT code (optional)</label>
              <input
                id="evidence-pct"
                name="evidence-pct"
                type="text"
                inputMode="numeric"
                value={pctCode}
                onChange={(event) => setPctCode(event.target.value)}
                placeholder="5201.0090"
                autoComplete="off"
                disabled={loading}
              />
            </div>
          </div>

          <div className="search-form__actions">
            <p className="muted">
              Returns up to five verified passages from the configured legal
              snapshot.
            </p>
            <button
              className="button button--primary"
              type="submit"
              disabled={loading || !query.trim()}
            >
              {loading ? (
                <LoaderCircle
                  className="spinner"
                  aria-hidden="true"
                  size={16}
                />
              ) : (
                <Search aria-hidden="true" size={16} />
              )}
              {loading ? "Searching evidence…" : "Search evidence"}
            </button>
          </div>
        </form>
      </div>

      {error ? (
        <ErrorNotice message={error} onDismiss={() => setError(null)} />
      ) : null}

      {loading ? (
        <div className="search-state" role="status" aria-live="polite">
          <LoaderCircle className="spinner" aria-hidden="true" size={22} />
          <div>
            <strong>Searching the regulatory index</strong>
            <p>Retrieval and reranking can take a moment on a cold service.</p>
          </div>
        </div>
      ) : null}

      {!loading && noEvidence ? (
        <EmptyState
          title="No verified evidence found"
          description="Try a broader phrase or remove the optional PCT-code filter. An empty result may also mean the regulatory index has not been initialized."
        />
      ) : null}

      {!loading && response && response.results.length > 0 ? (
        <section
          className="search-results"
          aria-labelledby="evidence-results-heading"
        >
          <div className="search-results__heading">
            <div>
              <p className="eyebrow">Search complete</p>
              <h2 id="evidence-results-heading">
                {response.result_count} verified{" "}
                {response.result_count === 1 ? "passage" : "passages"}
              </h2>
              <p>
                Results for <strong>“{response.query}”</strong>
              </p>
            </div>
            <StatusBadge
              status={response.degraded_mode ? "warning" : "ok"}
            />
          </div>

          <dl className="search-summary">
            <div>
              <dt>Retrieval mode</dt>
              <dd>{labelize(response.retrieval_mode)}</dd>
            </div>
            <div>
              <dt>Index version</dt>
              <dd>{response.vector_index_version}</dd>
            </div>
            <div>
              <dt>Embedding model</dt>
              <dd>{response.embedding_model}</dd>
            </div>
            <div>
              <dt>Elapsed</dt>
              <dd>{Math.round(response.retrieval_ms)} ms</dd>
            </div>
          </dl>

          {response.degraded_mode ? (
            <div className="notice notice--warning" role="status">
              <BookOpenCheck aria-hidden="true" size={18} />
              <div>
                <strong>Degraded retrieval mode</strong>
                <p>
                  Results are available, but one or more configured ranking
                  models were replaced by a deterministic fallback.
                </p>
              </div>
            </div>
          ) : null}

          <div className="search-result-list">
            {response.results.map((evidence, index) => (
              <EvidenceCard
                key={`${evidence.source_path}-${evidence.page_number ?? "na"}-${index}`}
                evidence={evidence}
                rank={index + 1}
              />
            ))}
          </div>
        </section>
      ) : null}
    </section>
  );
}

function ShipmentHistoryPanel() {
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState<ShipmentSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedQuery = query.trim();
    if (!normalizedQuery || loading) return;

    setLoading(true);
    setError(null);
    setResponse(null);

    try {
      setResponse(await api.searchShipments(normalizedQuery));
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setLoading(false);
    }
  }

  const noShipments =
    response?.status === "no_shipments_indexed" ||
    (response !== null && response.results.length === 0);

  return (
    <section
      id="search-panel-shipments"
      className="search-panel"
      role="tabpanel"
      aria-labelledby="search-tab-shipments"
    >
      <div className="panel search-form-panel">
        <div className="panel__header">
          <div>
            <h2>Shipment history</h2>
            <p>
              Find finalized customs-audit workflows using their indexed
              deterministic summaries.
            </p>
          </div>
        </div>

        <form className="search-form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="shipment-query">Search shipment history</label>
            <input
              id="shipment-query"
              name="shipment-query"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="For example: shipments with weight discrepancies"
              autoComplete="off"
              required
              disabled={loading}
              aria-describedby="shipment-query-help"
            />
            <p id="shipment-query-help" className="field__help">
              Search by product, exporter, failed check, status, or documented
              discrepancy.
            </p>
          </div>

          <div className="search-form__actions">
            <p className="muted">
              Returns up to five finalized workflows. No LLM generates these
              summaries.
            </p>
            <button
              className="button button--primary"
              type="submit"
              disabled={loading || !query.trim()}
            >
              {loading ? (
                <LoaderCircle
                  className="spinner"
                  aria-hidden="true"
                  size={16}
                />
              ) : (
                <Search aria-hidden="true" size={16} />
              )}
              {loading ? "Searching shipments…" : "Search shipments"}
            </button>
          </div>
        </form>
      </div>

      {error ? (
        <ErrorNotice message={error} onDismiss={() => setError(null)} />
      ) : null}

      {loading ? (
        <div className="search-state" role="status" aria-live="polite">
          <LoaderCircle className="spinner" aria-hidden="true" size={22} />
          <div>
            <strong>Searching finalized workflows</strong>
            <p>Comparing the query with indexed shipment summaries.</p>
          </div>
        </div>
      ) : null}

      {!loading && noShipments ? (
        <EmptyState
          title={
            response?.status === "no_shipments_indexed"
              ? "No finalized shipments are indexed"
              : "No matching shipments found"
          }
          description={
            response?.status === "no_shipments_indexed"
              ? "Complete a customs-audit workflow first, then search its persisted summary."
              : "Try a broader product, exporter, status, or discrepancy phrase."
          }
        />
      ) : null}

      {!loading && response && response.results.length > 0 ? (
        <section
          className="search-results"
          aria-labelledby="shipment-results-heading"
        >
          <div className="search-results__heading">
            <div>
              <p className="eyebrow">Search complete</p>
              <h2 id="shipment-results-heading">
                {response.result_count} matching{" "}
                {response.result_count === 1 ? "workflow" : "workflows"}
              </h2>
              <p>
                Results for <strong>“{response.query}”</strong>
              </p>
            </div>
            <StatusBadge status="ok" />
          </div>

          <p className="search-results__mode">
            Retrieval mode: <strong>{labelize(response.retrieval_mode)}</strong>
          </p>

          <div className="search-result-list">
            {response.results.map((shipment, index) => (
              <article
                className="search-result shipment-result"
                key={shipment.workflow_id}
              >
                <header className="search-result__header">
                  <div>
                    <p className="eyebrow">Match {index + 1}</p>
                    <h3>Customs audit workflow</h3>
                  </div>
                  <span className="search-score">
                    Score {shipment.score.toFixed(3)}
                  </span>
                </header>

                <p className="shipment-result__summary">{shipment.summary}</p>

                <div className="shipment-result__reference">
                  <span>Workflow ID</span>
                  <code>{shipment.workflow_id}</code>
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </section>
  );
}

export function SearchPage() {
  const [activeTab, setActiveTab] = useState<SearchTab>("evidence");

  function moveTab(
    event: KeyboardEvent<HTMLButtonElement>,
    destination: SearchTab,
  ) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      return;
    }

    event.preventDefault();
    const nextTab =
      event.key === "Home"
        ? "evidence"
        : event.key === "End"
          ? "shipments"
          : destination;
    setActiveTab(nextTab);
    window.requestAnimationFrame(() => {
      document.getElementById(`search-tab-${nextTab}`)?.focus();
    });
  }

  return (
    <>
      <PageHeader
        eyebrow="Knowledge search"
        title="Evidence and shipment search"
        description="Retrieve cited regulatory passages or search the deterministic summaries of completed customs audits."
      />

      <div className="search-tabs">
        <div className="search-tabs__list" role="tablist" aria-label="Search type">
          <button
            id="search-tab-evidence"
            className={`search-tabs__tab ${
              activeTab === "evidence" ? "search-tabs__tab--active" : ""
            }`}
            type="button"
            role="tab"
            aria-selected={activeTab === "evidence"}
            aria-controls="search-panel-evidence"
            tabIndex={activeTab === "evidence" ? 0 : -1}
            onClick={() => setActiveTab("evidence")}
            onKeyDown={(event) => moveTab(event, "shipments")}
          >
            <BookOpenCheck aria-hidden="true" size={17} />
            Regulatory evidence
          </button>
          <button
            id="search-tab-shipments"
            className={`search-tabs__tab ${
              activeTab === "shipments" ? "search-tabs__tab--active" : ""
            }`}
            type="button"
            role="tab"
            aria-selected={activeTab === "shipments"}
            aria-controls="search-panel-shipments"
            tabIndex={activeTab === "shipments" ? 0 : -1}
            onClick={() => setActiveTab("shipments")}
            onKeyDown={(event) => moveTab(event, "evidence")}
          >
            <History aria-hidden="true" size={17} />
            Shipment history
          </button>
        </div>

        {activeTab === "evidence" ? (
          <RegulatoryEvidencePanel />
        ) : (
          <ShipmentHistoryPanel />
        )}
      </div>

      <aside className="scope-note search-scope-note">
        <FileSearch aria-hidden="true" size={17} />
        <div>
          <strong>Search scope</strong>
          <span>
            Regulatory results come from the indexed regulatory corpus, which is broader than the deterministic PCT catalog. Legacy note: five-PCT legal index.
            Shipment results appear only after an audit workflow is finalized.
          </span>
        </div>
      </aside>
    </>
  );
}
