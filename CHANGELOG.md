# Changelog

All notable changes to the **Customs & Automated Compliance Engine (CACE)** are documented in this file.

## [1.0.0] - 2026-11-21

### Added
- **Hybrid Document Ingestion & Extraction**: Bounded PDF upload streaming, PyMuPDF + Tesseract OCR (`PSM 4`), PNG page previews, coordinate/regex extraction, and single-call token-budgeted Groq gap-fill across primary and supporting trade documents.
- **Deterministic Compliance & FY2026–27 `Decimal` Duty Engine**: 17-PCT validated Pakistan textile rule catalog, multi-line commercial invoice vs. packing list matching, exact `Decimal` cascading customs tax calculations (`CD`, `ACD`, `RD`, `FED`, `ST`, `VAT`, `AIT`), and SHA-256 hash-chained audit ledger with downloadable JSON/PDF dossiers.
- **16-Node LangGraph HITL Audit Workflow**: Stateful `Broker` and `Auditor` agents with durable PostgreSQL/SQLite interrupts, targeted field-correction dependency recomputation, and immutable revision history.
- **Hybrid Regulatory & Shipment RAG**: Combined BM25 / PostgreSQL full-text search, cached BGE-small dense vector matrix, cross-encoder reranking, citation validation, guarded multi-turn Customs Chat, shipment document Q&A, historical shipment semantic search, and 4 MCP tools.
- **React Operations Console**: Vite 6 + React 18 + TypeScript 5.7 console served at `/app` with PDF provenance highlighting, compliance review, export preparation, and Customs Chat.
- **Outcome Engineering (`spx`) & CI/CD**: 21-node Spec Tree (`spx/cace.product.md`), 16 behavioral test suites, extraction benchmark (`docs/BENCHMARK.md`), Docker Compose PostgreSQL 16 stack, and GitHub Actions CI (`1,483` passing tests, `0` `mypy` errors).

### Hardened
- **Core Compliance, Security & API Boundaries**: Enforced upload-root path containment, strict non-positive weight and supporting-document checks, Unicode NFKC/zero-width normalization across prompt-injection guards, HTTP `422` on unconfirmed HS duty rates, sanitized `X-Request-ID` headers, and durable LangGraph `retry()` threads.
- **LangGraph & RAG Pipelines**: Included `executable_rule_checks` (`xr_*`) across graph evidence gathering and agent verification, reconstructed reused citations on Revision 2+ rechecks, expanded regulatory-family invalidation prefixes, indexed verified supporting documents, enforced exact 8-digit PCT token matching, excluded superseded SROs on current queries, switched RRF ranking to tie-safe competition ranks, scoped and invalidated the vector cache by collection/rebuild, isolated ingestion sources via SQLAlchemy savepoints, added OCR fallback and model-switch refresh for shipment indexing, and L2-normalized shipment search embeddings.
