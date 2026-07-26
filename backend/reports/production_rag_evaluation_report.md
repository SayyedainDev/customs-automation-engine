# Phase 3B — Production RAG Evaluation Report

Embedding model: `hashing-bow-degraded-v1-d256` (degraded=True)
Reranker model: `lexical-overlap-degraded-v1` (degraded=True)
RAGAS installed: **False** | Live judge LLM: **False (no GROQ_API_KEY)**

## Retrieval metrics (computed)

- **questions_scored**: 24
- **recall_at_1**: 0.95
- **recall_at_3**: 1.0
- **recall_at_5**: 1.0
- **precision_at_5**: 0.48
- **mrr**: 0.975
- **ndcg_at_5**: 0.9495
- **source_document_rate**: 0.95
- **page_reference_rate**: 1.0
- **evidence_not_found_accuracy**: 1.0

## RAG answer metrics

Computed deterministically (offline):
- **answer_checks**: 3
- **answers_generated**: 3
- **status_preservation_accuracy**: 1.0
- **citation_correctness**: 1.0
- **unsupported_claim_rate**: 0.0

RAGAS-native (not fabricated):
- **faithfulness**: SKIPPED (RAGAS + live judge LLM unavailable)
- **answer_relevancy**: SKIPPED (RAGAS + live judge LLM unavailable)
- **context_precision**: SKIPPED (RAGAS + live judge LLM unavailable)
- **context_recall**: SKIPPED (RAGAS + live judge LLM unavailable)

## Per-question retrieval

| id | category | status | top source |
|---|---|---|---|
| q01_pct_raw_cotton | exact_pct | ok | PSW/TIPP textile product export requirem |
| q02_pct_yarn | exact_pct | ok | PSW/TIPP textile product export requirem |
| q03_pct_denim | exact_pct | ok | PSW/TIPP textile product export requirem |
| q04_pct_tshirt | exact_pct | ok | PSW/TIPP textile product export requirem |
| q05_pct_bedsheet | exact_pct | ok | PSW/TIPP textile product export requirem |
| q06_sro_2486 | exact_sro | ok | PSW/TIPP textile product export requirem |
| q07_sro_epo | exact_sro | ok | PSW/TIPP textile product export requirem |
| q08_semantic_deposit | semantic | ok | PSW/TIPP textile product export requirem |
| q09_semantic_deadline | semantic | ok | PSW/TIPP textile product export requirem |
| q10_semantic_coo | semantic | ok | PSW/TIPP textile product export requirem |
| q11_sbp_1pct | raw_cotton_sbp | ok | PSW/TIPP textile product export requirem |
| q12_sbp_confirmation | raw_cotton_sbp | ok | PSW/TIPP textile product export requirem |
| q13_180_day | raw_cotton_180_day | ok | PSW/TIPP textile product export requirem |
| q14_phytosanitary | phytosanitary | ok | Export Policy Order, 2022 — SRO 544(I)/2 |
| q15_form_e | form_e | ok | PSW/TIPP textile product export requirem |
| q16_china_coo | china_coo | ok | PSW/TIPP textile product export requirem |
| q17_china_coo_denim | china_coo | ok | PSW/TIPP textile product export requirem |
| q18_denim_reqs | product_reqs | ok | PSW/TIPP textile product export requirem |
| q19_yarn_reqs | product_reqs | ok | PSW/TIPP textile product export requirem |
| q20_bedsheet_reqs | product_reqs | ok | PSW/TIPP textile product export requirem |
| q21_unsupported_rubber | unsupported_pct | evidence_not_found |  |
| q22_unsupported_machine | unsupported_pct | evidence_not_found |  |
| q23_missing_weather | missing_evidence | evidence_not_found |  |
| q24_missing_unrelated | missing_evidence | evidence_not_found |  |

## Notes

- Retrieval ran in **degraded mode** (hashing embeddings and/or lexical reranker) because `sentence-transformers` is not installed in this environment. Install it and set `REGULATORY_ENABLE_REAL_MODELS=true` to evaluate the real dense + cross-encoder stack.
- Conflicting-evidence and adversarial-prompt questions (q25, q26) are answer-layer behaviours verified by unit tests, not retrieval metrics.
