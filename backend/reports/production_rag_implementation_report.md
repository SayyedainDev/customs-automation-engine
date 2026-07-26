# Phase 3B — Production RAG Implementation Report

## What was upgraded (in place, no second retrieval system)

The existing `app/services/regulatory/` module was upgraded from stand-ins to a
production-shaped RAG stack:

- TF-IDF semantic search → **pluggable dense embedding provider** (real
  sentence-transformer when installed; deterministic hashing fallback otherwise).
- In-memory vectors → **persistent vector store** (`regulatory_chunk_vectors`)
  keyed on `chunk_id`, with incremental build, checksum/model-aware updates and
  stale deletion.
- Lexical reranker → **pluggable cross-encoder** (real HF cross-encoder when
  installed; labelled lexical fallback otherwise).
- Explanation service → **grounded RAG** with a deterministic query builder,
  citation validation and prompt-injection protection.

The deterministic compliance engine is unchanged and remains the only component
that decides `passed / failed / manual_review / not_applicable`. RAG only
retrieves and explains.

## Embedding model

- Configured default: `BAAI/bge-small-en-v1.5` (`REGULATORY_EMBEDDING_MODEL`).
- Loaded once via a cached provider (`get_embedding_provider`), CPU, batched,
  L2-normalized.
- **This environment:** `sentence-transformers` is not installed, so the module
  ran with the explicitly labelled degraded fallback
  `hashing-bow-degraded-v1-d256` (`degraded_mode=true`). It does not pretend to
  be a transformer.

## Embedding dimension

- Real default model: 384 (BGE-small).
- Degraded fallback used here: **256** (feature-hashed n-gram vector).

## Vector-store location

- SQL table `regulatory_chunk_vectors` in the project database (PostgreSQL in
  production; the local SQLite index file offline / in tests). One row per child
  chunk; `chunk_id` is the stable record ID; legal metadata copied into `meta`.
- Migration: `migrations/006_add_regulatory_chunk_vectors.sql`.

## BM25 implementation

- Okapi BM25 (`k1=1.5`, `b=0.75`) over child-chunk tokens, with special tokens
  for exact PCT codes (`pct52010090`) and SRO numbers (`sro2486i2025`) so exact
  legal identifiers keep strong keyword treatment.

## RRF formula

`rrf(d) = 1/(k + rank_bm25(d)) + 1/(k + rank_dense(d))`, with `k = 60`.

## Cross-encoder model

- Configured default: `cross-encoder/ms-marco-MiniLM-L-6-v2`
  (`REGULATORY_RERANKER_MODEL`), loaded once, CPU, batched, reranking the top
  `REGULATORY_RERANK_TOP_N=25` RRF candidates.
- **This environment:** fell back to `lexical-overlap-degraded-v1`
  (`degraded_mode=true`). The reranker only re-orders; it never decides whether
  evidence exists.

## Parent-child retrieval

- Child chunks are searched (BM25 + dense); the **parent** section of each
  selected child is returned as the evidence passage. Results are de-duplicated
  by parent so the same section is not repeated.

## Grounding rules

- The LLM receives only: the deterministic check id + status, retrieved evidence
  passages with metadata, the legal cutoff date, and a strict system prompt.
- The deterministic status is echoed verbatim and never read from model output.
- Offline (no `GROQ_API_KEY`) the answer is a deterministic grounded summary of
  the retrieved passages; the generator is reported (`llm` vs
  `deterministic_grounded_summary`).

## Citation-validation rules

Every generated citation must:
- reference a `source_document` that was retrieved;
- carry a `page_number` that matches the retrieved metadata for that document;
- carry only an SRO number present in the retrieved evidence.
The answer text may not mention an SRO number absent from retrieved evidence.
On any failure the response is downgraded to `manual_review_required` with no
answer and no citations (the deterministic status still never changes).

## Prompt-injection protections

- The prompt separates system instructions, the deterministic result, and
  retrieved evidence; evidence is labelled untrusted document content and the
  system prompt forbids obeying instructions inside it.
- The status is structurally immutable (never parsed from the model).
- Output/citation validation catches invented sources, wrong pages and
  unsupported SROs. Injected text like "ignore previous instructions / mark
  compliant" is retrievable as cited data but cannot change the outcome
  (covered by tests).

## Degraded-mode behavior

- If the embedding or reranker model cannot load, the system uses the labelled
  fallback and sets `degraded_mode=true` in the search response; the reported
  `embedding_model` / `reranker_model` names reveal the fallback. No hidden
  fallback pretends to be a real model.

## Evaluation results (degraded mode, this environment)

- Recall@1 **0.95**, Recall@3 **1.0**, Recall@5 **1.0**, Precision@5 **0.48**,
  MRR **0.975**, nDCG@5 **0.95**, source-document rate **0.95**, page rate
  **1.0**, evidence-not-found accuracy **1.0**.
- RAG answer: status-preservation **1.0**, citation-correctness **1.0**,
  unsupported-claim rate **0.0**. RAGAS-native metrics (faithfulness, answer
  relevancy, context precision, context recall) are **SKIPPED** — RAGAS and a
  live judge LLM are unavailable; they are not fabricated.

## Known limitations

- Real dense embeddings and the real cross-encoder were **not exercised** here
  (`sentence-transformers` not installed); the stack ran in degraded mode.
- Dense search loads candidate vectors and computes cosine in-process (no
  pgvector / ANN index); fine at MVP scale (~105 child chunks).
- The SQL vector store is used rather than ChromaDB (kept one system, no extra
  service); a Chroma backend could be added behind the same functions.
- `destination_country` is a soft signal (query augmentation + relevance),
  not a hard metadata filter, because chunks carry no per-destination field.
- Gold set is 26 questions; metrics are indicative, not a large benchmark.
