# Enterprise Customs Automation Engine

**CACE** is a deterministic-first customs readiness platform for Pakistan textile exports. It turns a pair of messy shipping documents into an explainable answer to one operational question:

> **Can this shipment be submitted for customs review, and if not, exactly what needs attention?**

Export teams usually answer that question by opening PDFs, copying values into spreadsheets, comparing invoices with packing lists, searching changing regulations, and asking a specialist to resolve every ambiguity. That process is slow, difficult to audit, and vulnerable to silent transcription errors.

CACE brings those steps into one review workspace. It extracts shipment data, compares documents, applies explicit compliance rules, retrieves regulatory evidence, and pauses for a human whenever the evidence is incomplete or contradictory.

It is a demonstrable MVP for Pakistan textile exports—not a replacement for Pakistan Customs, WeBOC, a licensed customs agent, or legal advice.

## The short version

```text
                    THE PROBLEM
  PDFs + inconsistent tables + scattered regulations
                         |
                         v
                    THE CACE LOOP
  Extract  ->  Match  ->  Check  ->  Explain  ->  Review
                         |
                         v
                    THE OUTCOME
  Evidence-backed: PASSED / FAILED / MANUAL_REVIEW
```

The important design choice is that the language model is not allowed to invent the verdict. Local extraction, typed validation, matching logic, and deterministic rules own the compliance status. AI is used for bounded gap-filling, plain-language explanations, and optional validation narration.

## What problem does it solve?

A shipment can look correct at a glance and still contain an operational failure:

- the invoice quantity does not match the packing list;
- a line total does not equal quantity × unit price;
- declared weights disagree across documents;
- a PCT code is outside the supported rule set;
- a required supporting document is missing or the wrong document was uploaded;
- a regulation applies, but its source and page cannot be shown to the reviewer.

Traditional automation often makes this worse by sending every document straight to an LLM and trusting a plausible-looking answer. CACE uses a different contract:

```text
  Unknown is safer than guessed.
  Every important value needs provenance.
  Every compliance result needs a reproducible rule.
  Every unresolved disagreement goes to a person.
```

## How a shipment moves through CACE

### 1. Extraction and document matching

```text
 Invoice PDF       Packing-list PDF       Supporting documents
      |                    |                       |
      +----------+---------+-----------------------+
                 v
        Text layer / word coordinates
                 |
       scanned? -+-> Tesseract OCR
                 |
                 v
       Regex + table reconstruction
                 |
          unresolved fields only
                 |
          one bounded Groq call*
                 |
                 v
       Typed invoice and packing-list data
                 |
                 v
       Line-by-line and shipment checks

  * Hybrid mode normally makes zero or one extraction call per document.
```

The hybrid extractor reads ordinary PDFs locally, reconstructs line-item tables from PDF coordinates, and uses OCR only when a page has no usable text layer. If a field remains unresolved, the optional model sees a small, label-adjacent context—not the entire document—and its output is run through the same normalizers as a local capture.

### 2. Deterministic compliance and evidence

```text
                  extracted shipment
                          |
          +---------------+----------------+
          |                                |
          v                                v
   Document checks                    Rule checks
   - invoice vs packing list          - PCT/product scope
   - quantities and totals            - textile requirements
   - weights and duplicates            - document conditions
   - required documents               - effective-date rules
          |                                |
          +---------------+----------------+
                          v
               Regulatory evidence retrieval
                 source + page + excerpt
                          |
                          v
                    CACE final status
```

The supported MVP rule set covers 17 validated textile PCT codes across raw materials, yarn, woven fabric, knitted garments, woven garments, and made-up textile products. The executable catalog lives in [`regulatory_data/config`](regulatory_data/config), while the source snapshot and curated guidance live under [`regulatory_data`](regulatory_data).

### 3. Human review instead of hidden uncertainty

```text
                  +----------------+
                  |  Run shipment  |
                  +--------+-------+
                           |
                           v
                  +----------------+
                  | Deterministic  |
                  | checks + audit |
                  +---+--------+---+
                      |        |
              all clear|        |conflict / missing evidence
                      v        v
                +---------+  +---------------+
                | PASSED  |  | MANUAL_REVIEW |
                +---------+  +-------+-------+
                                     |
                         reviewer corrects or confirms
                                     |
                                     v
                           re-run affected checks
                                     |
                                     v
                              final report + history
```

The audit workflow records the interruption, reviewer decision, corrections, affected checks, and revision history. A reviewer can see why a result was reached instead of receiving a black-box “approve” button.

## What is included

| Capability | What it does |
| --- | --- |
| Hybrid PDF extraction | PyMuPDF text/coordinates first; Tesseract fallback for scanned pages |
| Structured extraction | Reads parties, invoice fields, PCT codes, quantities, prices, totals, packages, and weights |
| Cross-document matching | Compares invoice and packing-list lines, totals, quantities, and weights |
| Deterministic compliance | Applies the validated textile rule catalog and fails closed when scope is unknown |
| Supporting-document checks | Verifies Form-E/PSW declarations, certificates of origin, contracts, bills of lading, permits, and related evidence where configured |
| Regulatory retrieval | Hybrid lexical/vector retrieval with reranking, source provenance, and page references |
| Agent audit | LangGraph Broker/Auditor roles challenge the extracted result without owning the verdict |
| Human review | Durable pause, correction validation, recomputation, resume, and audit events |
| Explainability | Plain-language narration grounded in accepted evidence, with a deterministic fallback |
| Shipment history | Persists finalized summaries and supports semantic top-k search |
| Operations console | React/Vite interface for preparing exports, reviewing documents, asking CACE, and searching history |
| Synthetic evaluation | Fictional text and scanned document scenarios with independently declared expected outcomes |

## Safety model

The system is deliberately split into an authoritative path and an assistive path.

```text
                  AUTHORITATIVE PATH
  parsed values -> typed validators -> rules -> final status
         |                                             |
         +---------- provenance and audit trail --------+

                   ASSISTIVE PATH
  unresolved gaps -> bounded model call -> validated candidates
  accepted evidence -> model explanation -> output safety checks
```

Guardrails include:

- **No guessed legal or financial values.** Ambiguous dates and unlabelled figures remain unresolved.
- **Model output is revalidated.** A model cannot overwrite a reliable deterministic value or bypass a field’s normalizer.
- **Compliance status is immutable to the model.** LLM text can explain a result; it cannot turn `failed` into `passed`.
- **Prompt-injection resistance.** Instructions embedded in a PDF are treated as document content, not system instructions.
- **Evidence gating.** Explanations are generated only from accepted shipment/regulatory evidence and are labelled when degraded.
- **Fail-closed behavior.** Missing data, unsupported PCT codes, provider outages, and unresolved conflicts lead to review rather than confident fabrication.

## Project layout

```text
enterprise_customs_engine/
├── backend/
│   ├── app/
│   │   ├── api/routes/       FastAPI endpoints
│   │   ├── models/           SQLAlchemy persistence models
│   │   ├── schemas/          Request/response contracts
│   │   └── services/         extraction, compliance, retrieval, and audit logic
│   ├── migrations/           numbered SQL migrations
│   ├── scripts/              database, ingestion, indexing, and evaluation tools
│   └── tests/                unit, integration, OCR, RAG, and workflow tests
├── frontend/                 React + TypeScript operations console
├── regulatory_data/          official snapshots, curated guidance, and executable rules
├── synthetic_factory/        fictional invoices, packing lists, and review scenarios
└── docs/                     architecture, verification, and presentation notes
```

## Quick start

### Prerequisites

- Python 3.11
- Node.js 20 or newer
- PostgreSQL
- Tesseract OCR with English language data

On Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y postgresql tesseract-ocr tesseract-ocr-eng
```

### 1. Create a database

```bash
sudo -u postgres psql
```

```sql
CREATE USER customs_user WITH PASSWORD 'choose_a_password';
CREATE DATABASE customs_engine OWNER customs_user;
\q
```

### 2. Install the backend

Run these commands from the repository root:

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Set at least this value in `backend/.env`:

```text
CUSTOMS_DATABASE_URL=postgresql+psycopg://customs_user:choose_a_password@localhost:5432/customs_engine
```

`backend/.env` is ignored by Git. Set `GROQ_API_KEY` only if you want optional gap-filling or generated explanations; the core test suite does not require it.

### 3. Build the console and initialize the service

```bash
cd ../frontend
npm ci
npm run build

cd ../backend
python -m app.core.init_db
python -m scripts.apply_migrations
python -m scripts.build_regulatory_vector_index
python -m uvicorn app.main:app --reload
```

Open:

- Console: <http://127.0.0.1:8000/app/>
- Swagger API docs: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/health>
- Database health: <http://127.0.0.1:8000/health/database>

For frontend hot reload, run `npm run dev` from `frontend/` in a second terminal. Vite proxies API requests to the FastAPI service.

## Configuration that matters

The complete template is [`backend/.env.example`](backend/.env.example). The most important switches are:

| Variable | Purpose |
| --- | --- |
| `CUSTOMS_DATABASE_URL` | PostgreSQL connection used by the API and persisted audit history |
| `GROQ_API_KEY` | Optional provider key for bounded extraction gaps and explanations |
| `EXTRACTION_MODE=hybrid` | Local deterministic extraction first; `legacy` is retained for comparison |
| `REGULATORY_ENABLE_REAL_MODELS` | Enables Sentence Transformer embeddings/reranking; disable for a fast offline mode |
| `LANGGRAPH_CHECKPOINT_BACKEND` | `sqlite` for local demos, `postgres` for deployed workflow durability |
| `LANGGRAPH_ENABLE_LIVE_AGENTS` | Enables optional Broker/Auditor narration; keep `false` for a quota-safe demo |

Hybrid mode is designed for constrained provider quotas: it caches unchanged extraction results, sends only bounded context, and avoids retry cascades after a quota or provider failure.

## API map

The full contract is available in Swagger at `/docs`. The main groups are:

| Route | Purpose |
| --- | --- |
| `POST /documents/upload` | Validate and store a PDF or DOCX upload |
| `POST /documents/uploads/{id}/extract` | Persist page text and PDF word coordinates |
| `POST /api/v1/compliance/check-documents/multi-line` | Primary invoice/packing-list extraction and compliance workflow |
| `POST /api/v1/compliance/check` | Run a direct deterministic compliance check |
| `POST /api/v1/regulatory-evidence/search` | Retrieve grounded regulatory evidence |
| `POST /api/v1/customs-audit/workflows` | Start the resumable audit workflow |
| `POST /api/v1/customs-audit/workflows/{id}/review` | Resolve a human-review interrupt and resume |
| `GET /api/v1/customs-audit/workflows/{id}/events` | Read the audit event trail |
| `POST /api/v1/shipment-search/search` | Search finalized shipment history |

## Try the demo

The repository contains fictional scenarios so the workflow can be demonstrated without exposing real exporter data. A useful path is:

```text
synthetic_factory scenario
          |
          v
  Upload invoice + packing list
          |
          v
  Review extracted fields and matched lines
          |
          v
  Inspect checks, evidence, and final status
          |
          +--> clean scenario: show PASSED
          |
          `--> mismatch scenario: correct a field or confirm MANUAL_REVIEW
```

The synthetic factory guide is at [`synthetic_factory/README.md`](synthetic_factory/README.md), and the expected scenario outcomes are declared in [`synthetic_factory/scenario_manifest.json`](synthetic_factory/scenario_manifest.json).

## Testing

The normal suite uses fakes and mocks for provider calls:

```bash
cd backend
python -m pytest -q
```

Useful focused checks:

```bash
python -m pytest tests/unit/test_hybrid_extraction.py -q
python -m pytest tests/unit/test_customs_audit.py -q
python -m pytest tests/integration/test_adversarial_rag.py -q
python -m scripts.extraction_coverage_report
```

The extraction coverage report distinguishes “field not present” from “field present but missed,” which makes parser improvements measurable instead of anecdotal. See [`backend/EXTRACTION.md`](backend/EXTRACTION.md) for the extraction contract and quota controls.

## Docker and deployment

Build from the repository root. The image builds the React console, installs the Python API and Tesseract, bootstraps the schema, applies migrations, and serves the console from FastAPI:

```bash
docker build -f backend/Dockerfile -t customs-engine .
docker run --name customs-engine --rm -p 8000:8000 \
  --env-file backend/.env customs-engine
```

For a deployed instance:

- use PostgreSQL for durable audit checkpoints and history;
- set `LANGGRAPH_CHECKPOINT_BACKEND=postgres`;
- build the regulatory index after the database is initialized;
- treat container-local uploads and SQLite files as ephemeral;
- keep the regulatory corpus snapshot and legal cutoff visible to reviewers.

The deployed demo, when available, is hosted at <https://cace-production.up.railway.app/app/#/review>.

## Scope and limitations

CACE is intentionally honest about what it does not do:

- The compliance catalog covers 17 textile PCT codes, not the complete Pakistan Customs tariff.
- The regulatory index is a curated snapshot, not an automated daily SRO or law update pipeline.
- There is no live WeBOC or government-system integration.
- The system does not claim to calculate every duty, tax, valuation, or import entitlement.
- Tesseract OCR and synthetic documents are the main validation surface; broad real-exporter validation is not complete.
- Embeddings are stored in SQL JSON fields and scored in-process; this is not a horizontally scaled vector platform.
- Core work runs inline in API requests; there is no distributed job queue.
- Authentication, authorization, production monitoring, and enterprise-scale tenancy are outside this MVP.

When evidence is outside the supported scope, the correct product behavior is `MANUAL_REVIEW`, not a confident answer.

## Further reading

- [Hybrid extraction design](backend/EXTRACTION.md)
- [Assistant and RAG architecture](docs/CACE_ASSISTANT_REPORT.md)
- [Synthetic factory guide](synthetic_factory/README.md)
- [Backend reports](backend/reports/README.md)

## License and data note

This repository contains fictional shipment artifacts for testing and demonstration. Regulatory PDFs and extracted guidance are included for this prototype’s evidence workflow; always verify current requirements with official Pakistan Customs, FBR, PSW, TDAP, and Ministry of Commerce sources before filing a real shipment.
