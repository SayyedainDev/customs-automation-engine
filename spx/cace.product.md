---
id: 019ab100-0000-7000-8000-000000000001
kind: product
---

# Enterprise Customs Automation Engine (CACE)

Enterprise Customs Automation Engine (CACE) verifies Pakistan textile export shipments against deterministic customs compliance rules, computes statutory FY2026-27 cascading customs duties and taxes, retrieves official regulatory evidence, orchestrates resumable human-reviewed customs audits, and delivers grounded customs advisory over HTTP, MCP, and Web Console boundaries.

## Product-local semantics

- **Deterministic compliance engine**: The non-LLM rule evaluator that computes authoritative `passed`, `failed`, `manual_review`, or `not_applicable` shipment outcomes over verified PCT codes.
- **Provenance-carrying field**: An extracted field retaining its value, source document, page number, confidence score, extraction method, and validation status (`verified` or `manual_review`).
- **Compounded tax bases**: Pakistan Customs statutory duty assessment order in `Decimal` arithmetic: DPV equals `V + CD + ACD + RD + FED`, and Income Tax Base equals `DPV + ST + VAT`.
- **Official source kind**: Regulatory evidence issued directly by a government body (FBR, State Bank of Pakistan, Ministry of Commerce, or Pakistan Single Window), distinct from curated rule summaries.

## Product boundaries

- **Includes**: Digital and scanned export document ingestion, hybrid extraction with confidence and OCR gating, supporting-document classification and verification, deterministic multi-line item matching, textile compliance and cascading duty computation, hybrid BM25 and dense regulatory retrieval, human-in-the-loop audit workflows with immutable event hashing and PDF/JSON dossiers, historical shipment search, guarded customs advisory, and HTTP/MCP/Web Console interfaces.
- **Excludes**: Direct statutory filing submission to WeBOC or Pakistan Single Window, legal representation or clearance guarantees outside deterministic rule data, and unverified LLM-invented compliance verdicts or duty schedules.
