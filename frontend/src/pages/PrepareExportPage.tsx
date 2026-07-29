import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  SupportedProduct,
  DocumentGuidanceSchema,
  EvidenceClass,
  GuidanceCitation,
  GuidanceResponse,
} from "../api/types";
import { PageHeader } from "../components/PageHeader";
import {
  Loader2,
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  ChevronRight,
  RefreshCw,
} from "lucide-react";

const CATEGORY_LABELS: Record<string, string> = {
  raw_material: "Raw material",
  yarn: "Yarn",
  woven_fabric: "Woven fabric",
  knitted_garment: "Knitted garments",
  woven_garment: "Woven garments",
  made_up: "Made-up textiles",
};

// The product list used to be a literal five-entry array here, which drifted
// from the codes the engine actually supports. It now comes from the catalog.

interface FormInputs {
  product: string;
  pctCode: string;
  destination: string;
}

/** Whether a generated result still belongs to what the form currently says. */
export function isResultStale(current: FormInputs, checked: FormInputs | null): boolean {
  if (!checked) return false;
  return (
    current.product !== checked.product ||
    current.pctCode !== checked.pctCode ||
    current.destination.trim().toLowerCase() !==
      checked.destination.trim().toLowerCase()
  );
}

const EVIDENCE_CLASS_LABELS: Record<EvidenceClass, string> = {
  direct_evidence: "Direct evidence",
  indirect_support: "Indirect support",
  configured_rule_only: "Configured rule only",
  evidence_unavailable: "No evidence available",
  conflicting_evidence: "Conflicting evidence",
};

const EVIDENCE_CLASS_TONE: Record<EvidenceClass, string> = {
  direct_evidence: "success",
  indirect_support: "neutral",
  configured_rule_only: "neutral",
  evidence_unavailable: "warning",
  conflicting_evidence: "warning",
};

function CitationCard({ citation }: { citation: GuidanceCitation }) {
  const provenance = [
    citation.issuing_authority,
    citation.page_number != null ? `page ${citation.page_number}` : citation.section,
    citation.sro_number ? `SRO ${citation.sro_number}` : null,
  ].filter(Boolean);

  return (
    <li className="citation-card">
      <div className="citation-card__head">
        <BookOpen size={14} aria-hidden="true" />
        <strong>{citation.display_name ?? citation.source_document}</strong>
      </div>
      <div className="citation-card__provenance">
        <span
          className={`source-kind-chip source-kind-chip--${
            citation.is_official ? "official" : "curated"
          }`}
        >
          {citation.source_kind_label ?? "Unclassified source"}
        </span>
        {provenance.length > 0 ? (
          <span className="citation-card__section">{provenance.join(" · ")}</span>
        ) : null}
      </div>
      {citation.referenced_official_source ? (
        <p className="citation-card__section">
          References official source:{" "}
          <a href={citation.referenced_official_source} target="_blank" rel="noreferrer">
            {citation.referenced_official_source}
          </a>
        </p>
      ) : null}
      {citation.snippet ? (
        <p className="citation-card__snippet">“{citation.snippet}”</p>
      ) : null}
    </li>
  );
}

function DocumentCard({ doc }: { doc: DocumentGuidanceSchema }) {
  const tone = EVIDENCE_CLASS_TONE[doc.evidence_class] ?? "neutral";
  return (
    <details className="evidence-card" style={{ marginBottom: "0.75rem" }}>
      <summary>
        <span className="evidence-card__requirement">{doc.display_name}</span>
        <span
          className={`status-chip status-chip--${
            doc.requirement === "required" ? "failed" : "manual"
          }`}
        >
          {doc.requirement === "required" ? "Required" : "Conditional"}
        </span>
        <span className={`evidence-chip evidence-chip--${tone}`}>
          {EVIDENCE_CLASS_LABELS[doc.evidence_class] ?? doc.evidence_class}
        </span>
        <span className="muted"> Document to prepare</span>
      </summary>
      <div style={{ padding: "1rem" }}>
        <p>{doc.summary}</p>

        {doc.rule_sources.length > 0 ? (
          <p className="citation-card__section">
            Rule source: {doc.rule_sources.join(" · ")}
          </p>
        ) : null}

        <details className="citation-card__technical">
          <summary>Full reason and limitation</summary>
          <p style={{ whiteSpace: "pre-line" }}>{doc.reason}</p>
        </details>

        {doc.citations.length > 0 ? (
          <ul className="citation-list" style={{ marginTop: "1rem" }}>
            {doc.citations.map((citation, index) => (
              <CitationCard key={index} citation={citation} />
            ))}
          </ul>
        ) : null}
      </div>
    </details>
  );
}

export function PrepareExportPage() {
  const [products, setProducts] = useState<SupportedProduct[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [product, setProduct] = useState("");
  const [pctCode, setPctCode] = useState("");
  const [destination, setDestination] = useState("China");

  useEffect(() => {
    let active = true;
    api
      .getSupportedProducts()
      .then((response) => {
        if (!active) return;
        setProducts(response.products);
        const first = response.products[0];
        if (first) {
          setProduct(first.product_name);
          setPctCode(first.pct_code);
        }
      })
      .catch((err) => {
        if (!active) return;
        setCatalogError(
          err instanceof Error ? err.message : "Could not load supported products.",
        );
      });
    return () => {
      active = false;
    };
  }, []);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GuidanceResponse | null>(null);
  // The inputs the displayed result was actually generated from. Comparing
  // against the live form is what stops a Raw cotton result from being shown
  // under a Cotton yarn selection.
  const [checkedInputs, setCheckedInputs] = useState<FormInputs | null>(null);

  const stale = isResultStale({ product, pctCode, destination }, checkedInputs);

  const handleProductChange = (name: string) => {
    setProduct(name);
    const found = products.find((p) => p.product_name === name);
    if (found) setPctCode(found.pct_code);
  };

  const grouped = products.reduce<Record<string, SupportedProduct[]>>(
    (acc, item) => {
      (acc[item.textile_category] ??= []).push(item);
      return acc;
    },
    {},
  );
  const selected = products.find((p) => p.pct_code === pctCode);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    setCheckedInputs(null);

    try {
      const res = await api.getGuidance({
        product,
        pct_code: pctCode,
        destination,
      });
      setResult(res);
      setCheckedInputs({ product, pctCode, destination });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch guidance");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="New export"
        title="Prepare an Export"
        description="Select a product and destination to see the documents to prepare and the regulatory evidence behind each requirement, before starting your submission."
      />

      <div className="layout-split">
        <div className="layout-main">
          <section className="panel">
            <div className="panel__header">
              <h2>Shipment Details</h2>
            </div>
            <div className="panel__body">
              <form onSubmit={handleSubmit} className="form-layout">
                <div className="form-group">
                  <label htmlFor="product">Product</label>
                  <select
                    id="product"
                    value={product}
                    onChange={(e) => handleProductChange(e.target.value)}
                    className="text-input"
                  >
                    {Object.entries(grouped).map(([category, items]) => (
                      <optgroup
                        key={category}
                        label={CATEGORY_LABELS[category] ?? category}
                      >
                        {items.map((p) => (
                          <option key={p.pct_code} value={p.product_name}>
                            {p.product_name} ({p.display_pct_code})
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label htmlFor="pctCode">PCT Code</label>
                  <input
                    id="pctCode"
                    type="text"
                    value={pctCode}
                    onChange={(e) => setPctCode(e.target.value.replace(/[^0-9]/g, ''))}
                    className="text-input"
                  />
                  <p className="muted" style={{fontSize: "0.85rem", marginTop: "0.25rem"}}>
                    Normalized to 8 digits automatically.
                    {products.length > 0
                      ? ` ${products.length} validated textile PCT codes are supported.`
                      : ""}
                  </p>
                  {selected ? (
                    <p className="muted" style={{fontSize: "0.85rem"}}>
                      {selected.tariff_description}
                      {selected.tariff_source_page
                        ? ` (Pakistan Customs Tariff FY 2025-26, page ${selected.tariff_source_page})`
                        : ""}
                    </p>
                  ) : null}
                </div>

                <div className="form-group">
                  <label htmlFor="destination">Destination Country</label>
                  <input
                    id="destination"
                    type="text"
                    value={destination}
                    onChange={(e) => setDestination(e.target.value)}
                    className="text-input"
                    placeholder="e.g., China"
                    required
                  />
                </div>

                <div style={{ marginTop: "1rem" }}>
                  <button type="submit" className="button button--primary" disabled={busy}>
                    {busy ? <Loader2 className="spin" size={16} /> : <CheckCircle2 size={16} />}
                    {stale ? "Recheck required documents" : "Check required documents"}
                  </button>
                </div>
              </form>
            </div>
          </section>

          {catalogError && (
            <div className="notice notice--danger">
              <AlertTriangle size={18} />
              <div>
                <strong>Supported products unavailable</strong>
                <p>{catalogError}</p>
              </div>
            </div>
          )}

          {error && (
            <div className="notice notice--danger">
              <AlertTriangle size={18} />
              <div>
                <strong>Error</strong>
                <p>{error}</p>
              </div>
            </div>
          )}

          {result && (
            <div style={{ marginTop: "2rem" }} aria-live="polite">
              <h2>Guidance Result</h2>

              {stale && (
                <div className="notice notice--warning" style={{ marginBottom: "1rem" }}>
                  <RefreshCw size={18} />
                  <div>
                    <strong>Out of date</strong>
                    <p>
                      This result was generated for {checkedInputs?.product} (PCT{" "}
                      {checkedInputs?.pctCode}) to {checkedInputs?.destination}. The
                      form has changed since. Recheck to see documents for the
                      current selection.
                    </p>
                  </div>
                </div>
              )}

              {!result.supported_scope && (
                <div className="notice notice--warning" style={{marginBottom: "1rem"}}>
                  <AlertTriangle size={18} />
                  <div>
                    <strong>Unsupported Scope</strong>
                    <p>{result.answer}</p>
                  </div>
                </div>
              )}

              {result.supported_scope && (
                <div
                  aria-hidden={stale}
                  style={stale ? { opacity: 0.45, pointerEvents: "none" } : undefined}
                >
                  <div className="panel" style={{marginBottom: "1rem"}}>
                    <div className="panel__body">
                      <p><strong>Product:</strong> {result.product}</p>
                      <p><strong>PCT Code:</strong> {result.pct_code}</p>
                      <p><strong>Destination:</strong> {result.destination}</p>
                      {result.answer && <p style={{marginTop: "1rem"}}>{result.answer}</p>}
                    </div>
                  </div>

                  <h3>Documents to prepare</h3>
                  <p className="muted">
                    Nothing has been uploaded yet, so this is a checklist of
                    paperwork to obtain — not a list of missing documents. Expand
                    a card for the source, page, passage and limitation.
                  </p>
                  {result.documents.map((doc, i) => (
                    <DocumentCard key={i} doc={doc} />
                  ))}

                  <div style={{marginTop: "2rem", display: "flex", justifyContent: "flex-end"}}>
                    <Link to="/review" className="button button--primary">
                      Continue to document upload
                      <ChevronRight size={16} />
                    </Link>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <aside className="layout-sidebar">
          <div className="panel">
            <div className="panel__header">
              <h3>Limitations</h3>
            </div>
            <div className="panel__body">
              <ul className="plain-list">
                <li>This assistant is running in single-user prototype mode.</li>
                <li>Account-based authorization and multi-user document isolation are not implemented.</li>
                <li>
                  Deterministic compliance guidance covers only the {products.length || "validated"} textile PCT codes in the catalog.
                </li>
                <li>
                  For questions about other codes or general regulation, use{" "}
                  <Link to="/ask">Ask CACE</Link>.
                </li>
                <li>It is not official customs or legal advice.</li>
              </ul>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}
