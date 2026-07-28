import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { GuidanceResponse } from "../api/types";
import { PageHeader } from "../components/PageHeader";
import { FilePlus2, Loader2, AlertTriangle, BookOpen, CheckCircle2, ChevronRight } from "lucide-react";

const PRODUCTS = [
  { name: "Raw cotton", pct: "52010090" },
  { name: "Cotton yarn", pct: "52051100" },
  { name: "Denim fabric", pct: "52094200" },
  { name: "Cotton knitted T-shirts", pct: "61091000" },
  { name: "Cotton bed sheets", pct: "63023110" }
];

export function PrepareExportPage() {
  const [product, setProduct] = useState(PRODUCTS[0].name);
  const [pctCode, setPctCode] = useState(PRODUCTS[0].pct);
  const [destination, setDestination] = useState("China");
  
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GuidanceResponse | null>(null);

  const handleProductChange = (name: string) => {
    setProduct(name);
    const found = PRODUCTS.find(p => p.name === name);
    if (found) setPctCode(found.pct);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);

    try {
      const res = await api.getGuidance({
        product,
        pct_code: pctCode,
        destination,
      });
      setResult(res);
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
        description="Select a product and destination to view required documents and regulatory evidence before starting your submission."
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
                    {PRODUCTS.map(p => (
                      <option key={p.name} value={p.name}>{p.name}</option>
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
                  <p className="muted" style={{fontSize: "0.85rem", marginTop: "0.25rem"}}>Normalized to 8 digits automatically.</p>
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
                    Check required documents
                  </button>
                </div>
              </form>
            </div>
          </section>

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
            <div style={{ marginTop: "2rem" }}>
              <h2>Guidance Result</h2>
              
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
                <>
                  <div className="panel" style={{marginBottom: "1rem"}}>
                    <div className="panel__body">
                      <p><strong>Product:</strong> {result.product}</p>
                      <p><strong>PCT Code:</strong> {result.pct_code}</p>
                      <p><strong>Destination:</strong> {result.destination}</p>
                      {result.answer && <p style={{marginTop: "1rem"}}>{result.answer}</p>}
                    </div>
                  </div>

                  <h3>Required Documents</h3>
                  {result.documents.map((doc, i) => (
                    <details key={i} className="evidence-card" style={{marginBottom: "1rem"}} open>
                      <summary>
                        <span className="evidence-card__requirement">
                          {doc.display_name}
                        </span>
                        <span className="status-chip status-chip--passed">
                          {doc.requirement}
                        </span>
                        <span className={`evidence-chip evidence-chip--${doc.evidence_status === 'available' ? 'success' : 'neutral'}`}>
                          {doc.evidence_status === 'available' ? 'Evidence found' : doc.evidence_status === 'baseline' ? 'Baseline Document' : 'No regulatory evidence'}
                        </span>
                      </summary>
                      <div style={{padding: "1rem"}}>
                        <p>{doc.reason}</p>
                        
                        {doc.citations && doc.citations.length > 0 && (
                          <ul className="citation-list" style={{marginTop: "1rem"}}>
                            {doc.citations.map((cit: any, j) => (
                              <li key={j} className="citation-card">
                                <div className="citation-card__head">
                                  <BookOpen size={14} />
                                  <strong>{cit.display_name || cit.source_document}</strong>
                                </div>
                                <div className="citation-card__provenance">
                                  <span className="source-kind-chip source-kind-chip--official">Official regulatory source</span>
                                </div>
                                {cit.snippet && <p className="citation-card__snippet">“{cit.snippet}”</p>}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </details>
                  ))}
                  
                  <div style={{marginTop: "2rem", display: "flex", justifyContent: "flex-end"}}>
                    <Link to="/review" className="button button--primary">
                      Continue to document upload
                      <ChevronRight size={16} />
                    </Link>
                  </div>
                </>
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
                <li>This guidance covers only the five PCT codes supported by this prototype.</li>
                <li>It is not official customs or legal advice.</li>
              </ul>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}
