# Enterprise Customs Automation Engine

A backend-only GenAI bootcamp capstone for auditing Pakistan textile export
documents before customs submission.

The project is an **agentic customs-audit prototype for five PCT codes**. It
combines hybrid PDF extraction, deterministic compliance rules, regulatory
retrieval, LangGraph validation roles, human review, and persistent shipment
history. It is a demonstrable MVP, not a production customs or legal-advice
system.

## Capstone workflow

```text
Invoice + packing-list PDFs
          |
          v
PyMuPDF text/coordinates -- scanned page --> Tesseract OCR
          |
          v
Regex + table reconstruction -- unresolved fields --> one bounded Groq gap-fill
          |
          v
Deterministic matching and five-PCT compliance checks
          |
          v
LangGraph Broker role --> Auditor challenge + regulatory evidence
          |
          +--> agreed result --> final report
          |
          `--> anomaly/uncertainty --> human review --> resume --> final report
                                                     |
                                                     v
                                      PostgreSQL history + semantic index
```

The deterministic compliance engine is the only authority for
`passed`, `failed`, and `manual_review`. LLM output can extract unresolved
fields or explain verified findings, but it cannot change the compliance
status.

## Implemented

| Capability | Implementation |
| --- | --- |
| PDF extraction | PyMuPDF text and word coordinates, with Tesseract fallback for scanned pages |
| Structured fields | Exporter, buyer, invoice data, PCT codes, quantities, prices, totals, and weights |
| Multi-line matching | Invoice/packing-list item matching and discrepancy checks |
| Compliance | Deterministic rules over a curated five-product textile dataset |
| Regulatory evidence | Local hybrid retrieval with source/page provenance and degraded-mode fallback |
| Agent orchestration | LangGraph Broker and Auditor validation roles with structured consensus |
| Human review | Durable interrupt, review task, correction history, resume, events, and retry |
| Explanation | One bounded Groq narration call with deterministic template fallback and caching |
| Historical search | Persistent shipment summaries with local semantic top-k retrieval |
| Synthetic testing | 192 fictional PDFs, including text and image-only scanned variants |

The hybrid extraction design and free-tier safeguards are documented in
[backend/EXTRACTION.md](backend/EXTRACTION.md).

## Supported compliance scope

| PCT code | Product |
| --- | --- |
| `5201.0090` | Raw cotton, other |
| `5205.1100` | Cotton yarn |
| `5209.4200` | Denim fabric |
| `6109.1000` | Cotton knitted T-shirts |
| `6302.3110` | Cotton bed sheets, mill-made |

The regulatory corpus is a curated FBR, Ministry of Commerce, PSW, and TDAP
snapshot with a legal cutoff date of **2026-07-22**. Unsupported codes fail
closed to `manual_review`.

This repository does not claim complete Pakistan Customs coverage, current
daily SRO ingestion, duty/tax calculation, or live WeBOC integration.

## Technology

- Python 3.11 and FastAPI
- PostgreSQL and SQLAlchemy
- PyMuPDF and Tesseract OCR
- Groq structured output
- LangGraph
- Sentence Transformers with deterministic degraded fallbacks
- NumPy/scikit-learn retrieval and ranking

## Repository layout

```text
enterprise_customs_engine/
├── backend/
│   ├── app/                 FastAPI application and services
│   ├── migrations/          SQL schema migrations
│   ├── reports/             Curated implementation/evaluation reports
│   ├── scripts/             Data, index, factory, and evaluation commands
│   └── tests/               Unit and integration tests
├── regulatory_data/         Curated official snapshots and executable rule data
└── synthetic_factory/       Fictional invoices, packing lists, and support documents
```

## Local setup

### 1. Prerequisites

- Python 3.11
- PostgreSQL
- Tesseract with English language data

On Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y postgresql tesseract-ocr tesseract-ocr-eng
```

### 2. Create the database

```bash
sudo -u postgres psql
```

```sql
CREATE USER customs_user WITH PASSWORD 'choose_a_password';
CREATE DATABASE customs_engine OWNER customs_user;
\q
```

### 3. Install the backend

From the repository root:

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env` and set at least:

```text
CUSTOMS_DATABASE_URL=postgresql+psycopg://customs_user:choose_a_password@localhost:5432/customs_engine
```

`backend/.env` is ignored by Git.

### 4. Initialize and run

```bash
python -m app.core.init_db
python -m scripts.build_regulatory_vector_index
python -m uvicorn app.main:app --reload
```

Use `python -m uvicorn`, not a bare global `uvicorn` command. This guarantees
that the server uses the virtual environment containing the configured model
dependencies.

Open:

- Swagger UI: <http://127.0.0.1:8000/docs>
- API health: <http://127.0.0.1:8000/health>
- Database health: <http://127.0.0.1:8000/health/database>

## Configuration

Copy [backend/.env.example](backend/.env.example) and adjust these main values:

| Variable | Purpose |
| --- | --- |
| `CUSTOMS_DATABASE_URL` | Required PostgreSQL connection |
| `GROQ_API_KEY` | Optional gap-fill and explanation provider |
| `EXTRACTION_MODE=hybrid` | Free local extraction first; at most one Groq extraction call per document |
| `REGULATORY_ENABLE_REAL_MODELS` | Use Sentence Transformer models when available; set `false` for a fast offline demo |
| `LANGGRAPH_CHECKPOINT_BACKEND` | `sqlite` for a local demo or `postgres` for a deployed instance |
| `LANGGRAPH_ENABLE_LIVE_AGENTS=false` | Keep Broker/Auditor reasoning deterministic and avoid two optional narration calls |

### Free Groq tier

Hybrid mode is the default. It:

- resolves text, coordinates, tables, and regex fields locally;
- makes at most one Groq extraction call per document when gaps remain;
- caches unchanged document results;
- never retries a quota response;
- falls back to `manual_review` or a template explanation when the provider is
  unavailable.

For a class demonstration, use one known scenario and keep
`LANGGRAPH_ENABLE_LIVE_AGENTS=false`. Do not run the full synthetic factory
against the live provider.

## Main API groups

| Route | Purpose |
| --- | --- |
| `POST /documents/upload` | Validate and store a PDF or DOCX upload |
| `POST /documents/uploads/{id}/extract` | Persist page text and PDF word coordinates |
| `POST /api/v1/compliance/check-documents/multi-line` | Primary invoice/packing-list extraction and compliance workflow |
| `POST /api/v1/compliance/check` | Direct deterministic compliance check |
| `POST /api/v1/regulatory-evidence/search` | Retrieve grounded regulatory evidence |
| `POST /api/v1/customs-audit/workflows` | Start the LangGraph customs-audit workflow |
| `POST /api/v1/customs-audit/workflows/{id}/review` | Resolve a human-review interrupt and resume |
| `GET /api/v1/customs-audit/workflows/{id}/events` | Read the workflow audit trail |
| `POST /api/v1/shipment-search/search` | Retrieve semantically similar finalized shipments |

Swagger documents the complete request and response schemas.

## Suggested class demonstration

1. Start PostgreSQL and the API, then show `/health/database`.
2. Select `synthetic_factory/clean_cotton_tshirts/` or
   `synthetic_factory/error_quantity_mismatch/`.
3. Upload and extract the invoice and packing list.
4. Run `/api/v1/compliance/check-documents/multi-line` and show the structured
   fields, matched lines, provenance, and deterministic checks.
5. Start `/api/v1/customs-audit/workflows` with the same document IDs.
6. Show the Broker report, Auditor challenge, regulatory evidence, audit events,
   and final explanation.
7. If the workflow pauses, submit the human-review decision and show it resume
   on the same thread.
8. Search finalized history with `/api/v1/shipment-search/search`.

Keep screenshots or saved JSON from the same scenario as a backup for provider
quota or classroom connectivity problems.

## Synthetic factory and evidence

All synthetic entities and filings are fictional. The current artifacts include
15 shipment scenarios, 21 supporting-document bundles, text/scanned variants,
and one legacy demonstration bundle. Expected statuses in
`synthetic_factory/scenario_manifest.json` are hand-declared independently of
the compliance engine.

Useful evidence:

- [Report index](backend/reports/README.md)
- [Synthetic factory guide](synthetic_factory/README.md)
- [Scenario manifest validation](backend/reports/scenario_manifest_validation.md)
- [Hybrid extraction coverage](backend/EXTRACTION.md)
- [Regulatory retrieval evaluation](backend/reports/retrieval_evaluation_report.md)
- [LangGraph implementation report](backend/reports/langgraph_multi_agent_implementation_report.md)
- [Accuracy improvement report](backend/reports/synthetic_factory_accuracy_improvements.md)
- [Real-document validation limitations](backend/reports/real_document_validation_report.md)

## Tests

The test suite uses fakes/mocks for provider calls unless a script explicitly
states that it performs a live evaluation.

```bash
cd backend
python -m pytest -q
```

No Groq key is required for the ordinary unit/integration suite.

## Deployment

The Docker image is intended for a bootcamp demonstration, not a production
service. Build from the **repository root** so the image can copy both
`backend/` and the runtime regulatory subset:

```bash
docker build -f backend/Dockerfile -t customs-engine .
docker run --name customs-engine --rm -p 8000:8000 \
  --env-file backend/.env customs-engine
```

The configured PostgreSQL host must be reachable from inside the container;
`localhost` inside Docker is the container itself. On a fresh database, build
the regulatory index once from another terminal:

```bash
docker exec customs-engine python -m scripts.build_regulatory_vector_index
```

This index command uses local embeddings (or the explicitly labelled hashing
fallback) and makes no Groq call.

For Railway, use the repository root as the build context and
`backend/Dockerfile` as the Dockerfile path. Use PostgreSQL and set
`LANGGRAPH_CHECKPOINT_BACKEND=postgres`; container-local SQLite and uploaded
files are not durable across deployments. Run the same index command once from
the Railway service shell after the database is initialized.

## Known limitations

- Five textile PCT codes, not the complete customs tariff.
- Fixed regulatory snapshot, not automated daily legal updates.
- No tax/duty calculation and no live government-system integration.
- Tesseract OCR, not a multimodal vision model.
- Synthetic documents are the main benchmark; genuine exporter-document
  validation is not complete.
- Broker/Auditor are structured validation roles; optional LLM use is
  non-authoritative narration.
- Historical search is top-k semantic retrieval, not aggregate analytics.
- Embeddings are stored in SQL JSON fields and scored in-process.
- Core work is executed inline in API requests; there is no distributed job
  queue.
- No authentication, authorization, production monitoring, or enterprise
  scaling.
- The legacy `/shipments` CRUD demonstration is in memory; customs-audit
  workflow records and search summaries are persisted.
