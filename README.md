Enterprise Customs Automation Engine

A GenAI bootcamp capstone for auditing Pakistan textile export documents beforecustoms submission. It includes a FastAPI backend and a restrained operationsconsole for the class demonstration.

The project is an agentic customs-audit prototype covering 17 validatedtextile PCT codes. It combines hybrid PDF extraction, deterministiccompliance rules, regulatory retrieval with Groq-generated plain-languageexplanation, LangGraph validation roles, human review, and persistent shipmenthistory. It is a demonstrable MVP, not a production customs or legal-advicesystem.

Submission links





Live application

https://cace-production.up.railway.app/app/#/review

Demo video

add the URL here before submitting

The live console opens on Review Export Documents. The other twoexperiences are Prepare an Export and Ask CACE, reachable from thenavigation.

Capstone workflow

Invoice + packing-list PDFs
          |
          v
PyMuPDF text/coordinates -- scanned page --> Tesseract OCR
          |
          v
Regex + table reconstruction -- unresolved fields --> one bounded Groq gap-fill
          |
          v
Deterministic matching and 17-PCT compliance checks
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

The deterministic compliance engine is the only authority forpassed, failed, and manual_review. LLM output can extract unresolvedfields or explain verified findings, but it cannot change the compliancestatus.

Implemented

Capability

Implementation

PDF extraction

PyMuPDF text and word coordinates, with Tesseract fallback for scanned pages

Structured fields

Exporter, buyer, invoice data, PCT codes, quantities, prices, totals, and weights

Multi-line matching

Invoice/packing-list item matching and discrepancy checks

Compliance

Deterministic rules over a curated 17-product textile dataset

Regulatory evidence

Local hybrid retrieval with source/page provenance and degraded-mode fallback

Agent orchestration

LangGraph Broker and Auditor validation roles with structured consensus

Human review

Durable interrupt, review task, correction history, resume, events, and retry

Ask CACE

Hybrid retrieval, reranking and an evidence gate, then one bounded Groq call that explains the accepted passages in plain English

Explanation

One bounded Groq narration call with deterministic template fallback and caching

Historical search

Persistent shipment summaries with local semantic top-k retrieval

Operations console

React interface for uploads, compliance results, audit workflows, and evidence search

Synthetic testing

192 fictional PDFs, including text and image-only scanned variants

The hybrid extraction design and free-tier safeguards are documented inbackend/EXTRACTION.md.

Supported compliance scope

PCT code

Product

Category

5201.0090

Raw cotton, other

Raw material

5205.1100

Cotton yarn

Yarn

5205.2100

Combed cotton yarn (heavy count)

Yarn

5208.5200

Printed cotton fabric (light)

Woven fabric

5209.3100

Dyed cotton fabric (heavy)

Woven fabric

5209.4200

Denim fabric

Woven fabric

5211.4200

Blended denim fabric

Woven fabric

6105.1000

Men's knitted cotton shirts

Knitted garment

6106.1000

Women's knitted cotton blouses

Knitted garment

6109.1000

Cotton knitted T-shirts

Knitted garment

6110.2000

Cotton knitted jerseys and pullovers

Knitted garment

6203.4200

Men's woven cotton trousers

Woven garment

6204.6290

Women's woven cotton trousers

Woven garment

6205.2090

Men's woven cotton shirts

Woven garment

6301.3000

Cotton blankets and travelling rugs

Made-up

6302.3110

Cotton bed sheets, mill-made

Made-up

6302.6010

Cotton terry towels (mill-made)

Made-up

The regulatory corpus is a curated FBR, Ministry of Commerce, PSW, and TDAPsnapshot with a legal cutoff date of 2026-07-22. Unsupported codes failclosed to manual_review.

This repository does not claim complete Pakistan Customs coverage, currentdaily SRO ingestion, duty/tax calculation, or live WeBOC integration.

Technology

Python 3.11 and FastAPI

PostgreSQL and SQLAlchemy

PyMuPDF and Tesseract OCR

Groq structured output

LangGraph

Sentence Transformers with deterministic degraded fallbacks

NumPy/scikit-learn retrieval and ranking

React, TypeScript, and Vite

Repository layout

enterprise_customs_engine/
├── frontend/                 Operations console built and served at `/app/`
├── backend/
│   ├── app/                 FastAPI application and services
│   ├── migrations/          SQL schema migrations
│   ├── reports/             Curated implementation/evaluation reports
│   ├── scripts/             Data, index, factory, and evaluation commands
│   └── tests/               Unit and integration tests
├── regulatory_data/         Curated official snapshots and executable rule data
└── synthetic_factory/       Fictional invoices, packing lists, and support documents

Database bootstrap

A fresh database is created from the ORM metadata and then brought up to datewith the numbered SQL migrations:

python -m app.core.init_db          # create tables from app/models
python -m scripts.apply_migrations  # apply backend/migrations in order
python -m scripts.apply_migrations --status

Both steps are idempotent and safe to re-run. Migrations 001-004 are ALTER-onlyupgrade scripts for databases created before those columns existed, so theyassume init_db (or an earlier deployment) has already created the basetables; the migration runner is not a from-scratch schema builder on its own.Do not create tables by hand.

Local setup

1. Prerequisites

Python 3.11

Node.js 20 or newer

PostgreSQL

Tesseract with English language data

On Ubuntu:

sudo apt-get update
sudo apt-get install -y postgresql tesseract-ocr tesseract-ocr-eng

2. Create the database

sudo -u postgres psql

CREATE USER customs_user WITH PASSWORD 'choose_a_password';
CREATE DATABASE customs_engine OWNER customs_user;
\q

3. Install the backend

From the repository root:

cd backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env

Edit backend/.env and set at least:

CUSTOMS_DATABASE_URL=postgresql+psycopg://customs_user:choose_a_password@localhost:5432/customs_engine

backend/.env is ignored by Git.

4. Build the operations console

From backend/:

cd ../frontend
npm ci
npm run build
cd ../backend

5. Initialize and run

python -m app.core.init_db
python -m scripts.build_regulatory_vector_index
python -m uvicorn app.main:app --reload

Use python -m uvicorn, not a bare global uvicorn command. This guaranteesthat the server uses the virtual environment containing the configured modeldependencies.

Open:

Operations console (production build): http://127.0.0.1:8000/app/

Swagger UI: http://127.0.0.1:8000/docs

API health: http://127.0.0.1:8000/health

Database health: http://127.0.0.1:8000/health/database

For frontend development with hot reload, use a second terminal:

cd frontend
npm ci
npm run dev

Vite runs at http://127.0.0.1:5173/app/ and proxies the real API routes tothe local FastAPI service. The console uses real backend responses; it containsno production mock data.

Configuration

Copy backend/.env.example and adjust these main values:

Variable

Purpose

CUSTOMS_DATABASE_URL

Required PostgreSQL connection

GROQ_API_KEY

Optional gap-fill and explanation provider

EXTRACTION_MODE=hybrid

Free local extraction first; at most one Groq extraction call per document

GROQ_SUPPORTING_GAPFILL_MAX_COMPLETION_TOKENS=512

Bounded Form-E/COO unresolved-field response

REGULATORY_ENABLE_REAL_MODELS

Use Sentence Transformer models when available; set false for a fast offline demo

LANGGRAPH_CHECKPOINT_BACKEND

sqlite for a local demo or postgres for a deployed instance

LANGGRAPH_ENABLE_LIVE_AGENTS=false

Keep Broker/Auditor reasoning deterministic and avoid two optional narration calls

Free Groq tier

Hybrid mode is the default. It:

resolves text, coordinates, tables, and regex fields locally;

makes at most one Groq extraction call per document when gaps remain;

caches unchanged document results;

never retries a quota response;

falls back to manual_review or a template explanation when the provider isunavailable.

For a class demonstration, use one known scenario and keepLANGGRAPH_ENABLE_LIVE_AGENTS=false. Do not run the full synthetic factoryagainst the live provider.

Main API groups

Route

Purpose

POST /documents/upload

Validate and store a PDF or DOCX upload

POST /documents/uploads/{id}/extract

Persist page text and PDF word coordinates

POST /api/v1/compliance/check-documents/multi-line

Primary invoice/packing-list extraction and compliance workflow

POST /api/v1/compliance/check

Direct deterministic compliance check

POST /api/v1/regulatory-evidence/search

Retrieve grounded regulatory evidence

POST /api/v1/customs-audit/workflows

Start the LangGraph customs-audit workflow

POST /api/v1/customs-audit/workflows/{id}/review

Resolve a human-review interrupt and resume

GET /api/v1/customs-audit/workflows/{id}/events

Read the workflow audit trail

POST /api/v1/shipment-search/search

Retrieve semantically similar finalized shipments

Swagger documents the complete request and response schemas.

Suggested class demonstration

Open /app/ and confirm the API and database status in the top bar.

Select synthetic_factory/clean_cotton_tshirts/ orsynthetic_factory/error_quantity_mismatch/.

Use New review to upload the invoice and packing list. The interfaceshows real upload, extraction, and compliance stages.

Show the structured fields, matched lines, provenance, and deterministicchecks.

Choose Run agent audit only once when you want the Broker/Auditorexplanation; it is manual to protect the Groq free-tier quota.

Show the Broker report, Auditor challenge, regulatory evidence, audit events,and final explanation.

If the workflow pauses, submit the human-review decision and show it resumeon the same thread.

Search finalized history with /api/v1/shipment-search/search.

Keep screenshots or saved JSON from the same scenario as a backup for providerquota or classroom connectivity problems.

Synthetic factory and evidence

All synthetic entities and filings are fictional. The current artifacts include15 shipment scenarios, 21 supporting-document bundles, text/scanned variants,and one legacy demonstration bundle. Expected statuses insynthetic_factory/scenario_manifest.json are hand-declared independently ofthe compliance engine.

Useful evidence:

Report index

Synthetic factory guide

Scenario manifest validation

Hybrid extraction coverage

Regulatory retrieval evaluation

LangGraph implementation report

Accuracy improvement report

Real-document validation limitations

Tests

The test suite uses fakes/mocks for provider calls unless a script explicitlystates that it performs a live evaluation.

cd backend
python -m pytest -q

No Groq key is required for the ordinary unit/integration suite.

Deployment

The Docker image is intended for a bootcamp demonstration, not a productionservice. Its multi-stage build compiles the React console and serves it fromFastAPI at /app/. Build from the repository root so the image can copythe frontend, backend, and runtime regulatory subset:

docker build -f backend/Dockerfile -t customs-engine .
docker run --name customs-engine --rm -p 8000:8000 \
  --env-file backend/.env customs-engine

The configured PostgreSQL host must be reachable from inside the container;localhost inside Docker is the container itself. On a fresh database, buildthe regulatory index once from another terminal:

docker exec customs-engine python -m scripts.build_regulatory_vector_index

This index command uses local embeddings (or the explicitly labelled hashingfallback) and makes no Groq call.

For Railway, use the repository root as the build context andbackend/Dockerfile as the Dockerfile path. Use PostgreSQL and setLANGGRAPH_CHECKPOINT_BACKEND=postgres; container-local SQLite and uploadedfiles are not durable across deployments. Run the same index command once fromthe Railway service shell after the database is initialized.

Recommended Railway source settings:

Root Directory: /
Dockerfile Path: backend/Dockerfile
Branch: main
Auto deploys: enabled

With those settings, pushing a commit to main rebuilds the same Railwayservice automatically. Container startup runs init_db and then the numberedmigration runner before accepting requests. The public domain redirects to theconsole, while Swagger remains available at /docs.

Known limitations

17 textile PCT codes, not the complete customs tariff.

Fixed regulatory snapshot, not automated daily legal updates.

No tax/duty calculation and no live government-system integration.

Tesseract OCR, not a multimodal vision model.

Synthetic documents are the main benchmark; genuine exporter-documentvalidation is not complete.

Broker/Auditor are structured validation roles; optional LLM use isnon-authoritative narration.

8 of the 9 regulatory sources are partially_verified: the documents areofficial, but their extracted text has not been validated page by page, so aclean shipment can still be routed to human review.

Shipment date is not inferred from an invoice, declaration or issue date. Ifno document states it, the review reports manual_review rather thanguessing.

Historical search is top-k semantic retrieval, not aggregate analytics.

Embeddings are stored in SQL JSON fields and scored in-process.

Core work is executed inline in API requests; there is no distributed jobqueue.

No authentication, authorization, production monitoring, or enterprisescaling.

The API has no UUID-upload or workflow-list endpoint, so console overviewtotals cover documents tracked by the current browser, not every databaserecord.

The legacy /shipments CRUD demonstration is in memory; customs-auditworkflow records and search summaries are persisted.
