# CACE — Enterprise Customs Engine

Customs & Automated Compliance Engine (CACE) for Pakistan textile export operations. CACE combines deterministic regex/OCR and bounded LLM hybrid extraction, FY2026–27 `Decimal` cascading customs duty calculations, a stateful LangGraph compliance audit workflow with dossier export, an evidence-grounded Customs Chat assistant, an MCP server, a React operations console, and an Outcome Engineering (`spx`) specification tree.

## Core Capabilities

### 1. Document Ingestion, Page Previews & Provenance
- **Ingestion & Page Preview**: Handles digital and scanned PDFs with rendered page previews ([`page_preview.py`](backend/app/services/extraction/page_preview.py)) served through the documents API ([`documents.py`](backend/app/api/routes/documents.py)).
- **Visual Provenance Viewer**: Highlights extracted field bounding boxes and page coordinates directly in the review console ([`PdfProvenanceViewer.tsx`](frontend/src/components/PdfProvenanceViewer.tsx)).

### 2. Hybrid Document Extraction (Approaches A / B / C)
- **Deterministic First**: Regex and structural pattern extractors ([`patterns.py`](backend/app/services/extraction/patterns.py), [`regex_extractor.py`](backend/app/services/extraction/regex_extractor.py)) resolve structured export fields locally at zero token cost.
- **Selective LLM Escalation**: Ambiguous or incomplete fields escalate cleanly via Groq (`openai/gpt-oss-20b`, default `extraction_mode="hybrid"`, `ocr_page_segmentation_mode=4` in [`config.py`](backend/app/core/config.py)) across primary and supporting trade documents ([`hybrid_orchestrator.py`](backend/app/services/extraction/hybrid_orchestrator.py), [`supporting_document_hybrid.py`](backend/app/services/extraction/supporting_document_hybrid.py)).

### 3. FY2026–27 `Decimal` Duty & Compliance Engine
- **Exact Financial Precision**: Computes cascading Pakistan statutory customs duties and taxes using `Decimal` arithmetic ([`duty_calculator.py`](backend/app/services/compliance/duty_calculator.py)) backed by tariff schedule ingestion ([`load_tariff_lines.py`](backend/scripts/load_tariff_lines.py)).
- **Multi-Line Shipment Compliance**: Validates HS codes, valuation, and export requirements across single and multi-line shipments ([`multi_line_shipment_service.py`](backend/app/services/multi_line_shipment_service.py), [`compliance.py`](backend/app/api/routes/compliance.py)), surfaced via [`ComplianceReviewResult.tsx`](frontend/src/components/ComplianceReviewResult.tsx).

### 4. LangGraph Customs Audit & Dossier Export
- **Stateful Audit Workflow**: Executes multi-step compliance verification in LangGraph ([`state.py`](backend/app/services/customs_audit/state.py), [`nodes.py`](backend/app/services/customs_audit/nodes.py), [`workflow_service.py`](backend/app/services/customs_audit/workflow_service.py), [`report.py`](backend/app/services/customs_audit/report.py)).
- **Structured Audit Dossiers**: Generates downloadable JSON and PDF audit dossiers ([`dossier.py`](backend/app/services/customs_audit/dossier.py)) inspected in [`AgentAuditResult.tsx`](frontend/src/components/AgentAuditResult.tsx).

### 5. Customs AI Assistant & MCP Server
- **Grounded Customs Chat**: Multi-turn customs assistant pipeline ([`pipeline.py`](backend/app/services/customs_chat/pipeline.py)) with Pakistan tariff parsing ([`tariff_parser.py`](backend/app/services/customs_chat/knowledge/tariff_parser.py)) and export checklists ([`checklist_tool.py`](backend/app/services/customs_chat/knowledge/checklist_tool.py)), wired to [`CustomsChatPage.tsx`](frontend/src/pages/CustomsChatPage.tsx).
- **MCP Tool Server**: Exposes customs lookup, duty calculation, checklist, and regulation search tools via [`mcp_server.py`](backend/app/mcp_server.py).

### 6. React Operations Console
- **UI Workflows**: Built with React 18, TypeScript 5.7, and Vite 6 ([`frontend/package.json`](frontend/package.json)), featuring dedicated views for document compliance review ([`NewReviewPage.tsx`](frontend/src/pages/NewReviewPage.tsx)), export preparation ([`PrepareExportPage.tsx`](frontend/src/pages/PrepareExportPage.tsx)), and Customs Chat ([`CustomsChatPage.tsx`](frontend/src/pages/CustomsChatPage.tsx)).
- **Integrated Serving**: [`backend/app/main.py`](backend/app/main.py) mounts the compiled frontend at `/app` when present and emits `X-Request-ID` and `X-Process-Time-Ms` telemetry headers on API responses.

## Architecture & Repository Layout

```text
enterprise_customs_engine/
├── backend/
│   ├── app/
│   │   ├── api/routes/          # FastAPI route handlers (13 registered routers)
│   │   ├── core/                # Configuration & structured domain exceptions
│   │   ├── schemas/             # Pydantic request/response contracts
│   │   ├── services/
│   │   │   ├── compliance/      # FY2026-27 Decimal duty calculator & rules
│   │   │   ├── customs_audit/   # LangGraph state, nodes, reports & dossier export
│   │   │   ├── customs_chat/    # RAG/tool pipeline, tariff & checklist knowledge
│   │   │   └── extraction/      # Hybrid orchestrator, regex/OCR & page previews
│   │   ├── main.py              # FastAPI entrypoint, middleware & router wiring
│   │   └── mcp_server.py        # 4 MCP customs intelligence & duty tools
│   ├── cace_testing/            # Shared Outcome Engineering harnesses & generators
│   ├── scripts/
│   │   ├── benchmark_extraction.py
│   │   └── load_tariff_lines.py
│   └── tests/                   # Backend API & unit test suites
├── frontend/
│   ├── package.json             # cace-operations-console (React 18 / Vite 6 / TS)
│   └── src/
│       ├── api/                 # Typed client & contracts
│       ├── components/          # PdfProvenanceViewer, ComplianceReviewResult, AgentAuditResult
│       ├── hooks/               # useDocumentReview
│       └── pages/               # NewReviewPage, PrepareExportPage, CustomsChatPage
├── spx/                         # Outcome Engineering (Spec Tree) specifications & L0-L2 tests
│   ├── cace.product.md
│   ├── 15-authoritative-safety.pdr.md
│   ├── 18-architecture-and-evidence.adr.md
│   ├── local/                   # Project test-evidence & Python overlays
│   └── conftest.py
├── docs/                        # Benchmark, chatbot architecture, scope & project docs
├── pyproject.toml               # Unified pytest, ruff, and mypy configuration
├── spx.config.yaml              # Spec Tree 3.x configuration
├── AGENTS.md                    # Generated agent instructions & product commands
└── CLAUDE.md                    # Generated repository working conventions
```

## API Routers & MCP Tools

### Registered FastAPI Routers ([`backend/app/main.py`](backend/app/main.py))
- `health` — Service health & readiness checks
- `documents` — Document upload, inspection & PDF page preview endpoints
- `structured_extraction` — Single-document hybrid/regex field extraction
- `compliance` — HS validation, FY2026–27 duty calculation & review results
- `shipment_extraction` — Shipment-level trade packet extraction
- `multi_line_shipment` — Multi-line invoice & packing list compliance
- `regulatory_evidence` — Supporting SRO and regulatory evidence lookup
- `compliance_explain` — Structured compliance decision explanations
- `customs_audit` — LangGraph audit execution & JSON/PDF dossier export
- `shipment_search` — Shipment lookup and search
- `assistant` — Assistant endpoints
- `customs_chat` — Multi-turn assistant (`messages`, `conversations/{id}`, `suggestions`, `coverage`, `feedback`)
- `shipments` — Internal compatibility router (`include_in_schema=False`)

Interactive OpenAPI documentation is available at `/docs` when running the FastAPI server.

### MCP Server Tools ([`backend/app/mcp_server.py`](backend/app/mcp_server.py))
- `lookup_pakistan_tariff`
- `calculate_pakistan_customs_duties`
- `get_textile_export_checklist`
- `search_pakistan_customs_regulations`

## Measured Extraction Benchmark

Evaluated with [`benchmark_extraction.py`](backend/scripts/benchmark_extraction.py) over 32 digital PDFs (476 fields); full methodology and breakdowns live in [`docs/BENCHMARK.md`](docs/BENCHMARK.md).

| Approach | Accuracy / Yield | Tokens Used | Avg Latency / Doc | Operational Notes |
| --- | --- | --- | --- | --- |
| **Approach A (LLM-Only)** | `100.00%` (2 unthrottled `clean_bedsheets` docs); `5.88%` (`28/476`) full-batch yield | `7,187` | `0.328s` | Hit 30 HTTP `429` errors under 8,000 TPM cap |
| **Approach B (Deterministic Regex/Rules)** | `89.50%` (`426/476`) | `0` | `0.006s` | `4/32` documents flagged for manual review |
| **Approach C (Hybrid)** | `89.50%` (`426/476`) | `874` | `0.864s` (on escalated doc) | `31/32` (`96.9%`) resolved locally at 0 tokens; `1/32` escalated; 0 provider failures |

Run or reproduce offline benchmark verification with:

```bash
python backend/scripts/benchmark_extraction.py --verify-offline
```

Supported benchmark CLI options include `--variant {text,scanned,both}`, `--limit`, `--scenario`, `--dry-run`, `--estimate-tokens`, `--verify-offline`, `--no-cache`, `--cache-dir`, `--delay-seconds`, `--output`, and `--write-report`.

## Quick Start & Verification

### 1. Run Backend & Spec Tree Tests

From `enterprise_customs_engine/` (using root [`pyproject.toml`](pyproject.toml)):

```bash
pytest -c pyproject.toml
```

Run only the Spec Tree verification suites (includes L2 `npm --prefix frontend run build`):

```bash
pytest -c pyproject.toml spx -v
```

Run linting and static type checks:

```bash
ruff check
mypy backend/app
mypy backend/cace_testing spx
```

### 2. Start Backend API & MCP Server

From `enterprise_customs_engine/backend/`:

```bash
uvicorn app.main:app --reload
```

For MCP integrations, [`backend/app/mcp_server.py`](backend/app/mcp_server.py) exposes the four Pakistan customs tools (`lookup_pakistan_tariff`, `calculate_pakistan_customs_duties`, `get_textile_export_checklist`, `search_pakistan_customs_regulations`).

### 3. Build & Run Frontend Console

From `enterprise_customs_engine/`:

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

Production type-check and build (`tsc --noEmit && vite build`):

```bash
npm --prefix frontend run build
npm --prefix frontend run preview
```

## Outcome Engineering (Spec Tree)

CACE uses a native 3.x Spec Tree ([`spx.config.yaml`](spx.config.yaml)) rooted at [`spx/cace.product.md`](spx/cace.product.md) alongside authoritative-safety PDR ([`15-authoritative-safety.pdr.md`](spx/15-authoritative-safety.pdr.md)) and architecture/evidence ADR ([`18-architecture-and-evidence.adr.md`](spx/18-architecture-and-evidence.adr.md)) decisions:

- **21 Feature/Platform Nodes & 2 Decisions** spanning PDF ingestion, hybrid extraction, duty calculation, LangGraph audit, Customs Chat, MCP server, and frontend console build verification.
- **16 Evidence-Tiered Test Suites** under `spx/**/tests/` wired to shared test harnesses and generators in `backend/cace_testing/` and [`spx/conftest.py`](spx/conftest.py).

Validate specs and track next deliverable work items with `spx`:

```bash
spx config validate
spx validation markdown spx/
spx spec status --format json
spx spec next
pytest -c pyproject.toml spx -v
```

## Documentation Index

- [`docs/BENCHMARK.md`](docs/BENCHMARK.md) — Digital PDF extraction benchmark results (Approaches A, B, and C)
- [`docs/CUSTOMS_CHATBOT_ARCHITECTURE.md`](docs/CUSTOMS_CHATBOT_ARCHITECTURE.md) — Customs AI chatbot design & tool architecture
- [`docs/CACE_ASSISTANT_REPORT.md`](docs/CACE_ASSISTANT_REPORT.md) — Assistant implementation report
- [`docs/CACE_ASSISTANT_FLOWCHARTS.md`](docs/CACE_ASSISTANT_FLOWCHARTS.md) — Assistant execution flowcharts
- [`docs/CACE_ASSISTANT_SCOPE_FLOWCHARTS.md`](docs/CACE_ASSISTANT_SCOPE_FLOWCHARTS.md) — Assistant scope boundaries & flowcharts
- [`docs/CACE_ASSISTANT_FINAL_VERIFICATION.md`](docs/CACE_ASSISTANT_FINAL_VERIFICATION.md) — Assistant verification summary
- [`docs/PROJECT_MAP.md`](docs/PROJECT_MAP.md) — High-level module & project map
- [`docs/PHASE_1_COMPLIANCE_CODE_REVIEW.md`](docs/PHASE_1_COMPLIANCE_CODE_REVIEW.md) — Phase 1 compliance code review
- [`docs/PRESENTATION_GUIDE.md`](docs/PRESENTATION_GUIDE.md) — Architecture & demo walkthrough guide
- [`spx/cace.product.md`](spx/cace.product.md) — Outcome Engineering product root specification
