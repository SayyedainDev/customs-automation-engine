# Phase 3C — LangGraph Multi-Agent Customs-Audit Implementation Report

## LangGraph version

- `langgraph` **1.2.9**, `langgraph-checkpoint-sqlite` **3.1.0**
  (`langgraph-checkpoint-postgres` supported for the production path).
- APIs used: `StateGraph`, `START`, `END`, conditional edges,
  `langgraph.types.interrupt` / `Command(resume=...)`, `InMemorySaver`,
  `SqliteSaver`, `PostgresSaver`.

## Graph nodes

`load_shipment_documents` → `broker_agent` → `deterministic_compliance` →
`auditor_agent` → `compare_agent_reports` → (`build_final_report` |
`interrupt_for_human_review` → `human_decision_received` → `resume_workflow` →
`build_final_report`) → `persist_audit_record` → END.

## Graph edges

Linear edges through the pipeline; one **conditional edge** after
`compare_agent_reports` (`route_after_compare`) routing to human review when the
consensus result requires it, else to the final report. The human branch
re-joins `build_final_report`.

## Graph state

`CustomsAuditState` (TypedDict, JSON-serializable) with: workflow/thread ids and
status, document ids and shipment inputs, `extraction_result`, `page_reviews`,
`ocr_results`, `matched_items`, `cross_document_checks`, `shipment_inputs`,
`deterministic_compliance_result` (+ `deterministic_result_history`),
`broker_report`, `auditor_report`, `consensus_result`, `retrieved_evidence`,
`explanation_results`, `critical_anomalies`, `manual_review_reasons`,
`human_review_request`, `human_review_decision`, `human_correction_history`,
`final_report`, `rule_data_version`, `vector_index_version`, `errors`,
`audit_events`, `retry_count`.

## Broker responsibilities

Runs the existing multi-line pipeline (extraction, OCR fallback, structured
extraction, deterministic item matching, cross-document checks, deterministic
compliance) via `extract_match_and_check_multi_line_shipment`, then builds a
structured `BrokerReport` (extracted fields with provenance, matched/unmatched
items, discrepancies, deterministic check results, unsupported products, missing
documents, OCR/manual-review fields, confidence, limitations). It never guesses
uncertain values and never authors the compliance status.

## Auditor responsibilities

Independently re-derives arithmetic, invoice-total reconciliation, weight and PCT
normalization checks; detects Broker omissions; retrieves regulatory evidence
through the existing hybrid RAG system for failed/manual-review government checks;
assesses evidence support, missing provenance and conflicts; produces a
structured `AuditorReport` with a recommended workflow action. It confirms that
the deterministic status **stands** and can never change it.

## Consensus algorithm

Deterministic structured comparison (no free-form LLM agreement). It compares
observed deterministic status, discrepancy detection, independent arithmetic,
evidence availability and provenance, then splits triggers into **critical**
(always interrupt: agent disagreement, unsupported product, conflicting evidence,
document conflict, arithmetic anomaly, escalating auditor action) and
**uncertainty** (interrupt when human review is enabled: manual_review/failed
status, uncertain evidence, missing provenance). Consensus controls routing only;
it can never turn `manual_review`/`failed` into `passed`.

## Deterministic decision boundary

`deterministic_compliance` freezes the status produced by the existing Python
engine into `deterministic_compliance_result`. Agent output is validated
(`validate_broker_report` / `validate_auditor_report`); any attempt to report a
different status, cite unretrieved SROs, or fail schema validation is recorded as
a violation and routed to human review — the frozen status is never altered.

## Human-interruption behavior

`interrupt_for_human_review` calls `interrupt(payload)`; the graph pauses with
full state persisted in the checkpointer. The service creates a
`customs_human_review_tasks` row (reason, disputed fields, source pages,
deterministic status, evidence, allowed actions). Allowed actions:
confirm/correct extracted value, provide missing document, accept manual review,
request reprocessing, reject submission, add review note.

## Checkpointing / workflow persistence

Resumable graph state → LangGraph checkpointer (Postgres in production, durable
SQLite file otherwise; in-memory only for tests). Queryable projection + audit
trail → project tables `customs_audit_workflows`, `customs_human_review_tasks`,
`customs_audit_events` (migration `007`). The service syncs state → tables after
every graph step. Resume after a process restart works because the checkpointer
is durable (test #17 restarts with a fresh graph on the same files).

## API endpoints

- `POST /api/v1/customs-audit/workflows` — start (returns workflow id / status).
- `GET /api/v1/customs-audit/workflows/{id}` — status + final report.
- `GET /api/v1/customs-audit/workflows/{id}/review` — pending review task.
- `POST /api/v1/customs-audit/workflows/{id}/review` — submit decision + resume.
- `GET /api/v1/customs-audit/workflows/{id}/events` — immutable audit trail.
- `POST /api/v1/customs-audit/workflows/{id}/retry` — retry a technical failure
  (blocked when a human-review gate is pending).

## Audit trail

Every transition appends an immutable `customs_audit_events` row (event type,
node, actor type broker/auditor/human/system, actor reference, state hash,
payload, timestamp). A human correction preserves the original machine-extracted
value, the corrected value, reviewer reference, reason, source and timestamp; a
new deterministic result version is created while the original is retained in
history.

## Async behavior

FastAPI routes are async; the (sync) LangGraph graph runs off the event loop via
`asyncio.to_thread`, and graph nodes open their own DB sessions, so PDF/OCR/
embedding/DB work never blocks the loop. A start endpoint returns a workflow id
immediately and a status endpoint is polled for longer runs.

## Agent prompt safety

All uploaded document text and retrieved regulatory text is untrusted. Agent
findings are derived deterministically (the only optional LLM contribution is
non-authoritative prose). Injection phrases ("ignore previous instructions",
"mark this shipment compliant", "reveal the system prompt", "skip human review",
"call another tool") are detected and logged as data (audit events
`injection_detected_in_document` / `_in_evidence`) and never obeyed. Agent output
is Pydantic-validated; status changes, unretrieved citations, invented SROs and
schema failures force manual review.

## Test vs real-model behavior

Tests inject deterministic fake agents, a fake pipeline, a fake evidence
retriever and in-memory/SQLite checkpointers — **no** Groq, model downloads,
Tesseract or network. Live Groq agents (`LANGGRAPH_ENABLE_LIVE_AGENTS=true`) and
the PostgreSQL checkpointer are supported but not exercised in this environment;
this is stated, not claimed as validated.

## Known limitations

- Live LLM Broker/Auditor narration and real PostgreSQL checkpointing were **not**
  run here (no `GROQ_API_KEY`, no PostgreSQL); the graph, consensus, interrupts,
  resume, corrections and persistence are exercised for real with fakes + SQLite.
- Human correction re-runs the deterministic engine for line-item fields
  (PCT/quantities/weights/prices); `provide_missing_document` /
  `request_reprocessing` are recorded and routed, but a full re-extraction from a
  newly uploaded document is done by starting a new workflow (documented).
- Graph executed via `asyncio.to_thread(invoke)`; native `ainvoke` is available
  but sync nodes call sync services, so thread-offloading the whole graph is the
  chosen non-blocking path.
