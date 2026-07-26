# Stage 6 — Retrieval Evaluation Report

Gold set: `tests/fixtures/regulatory_gold_eval.json`. Top-k = 5. verified_only = true.

## Metrics

- **recall_at_5**: 1.0
- **precision_at_5**: 0.4556
- **mrr**: 1.0
- **page_reference_accuracy**: 1.0
- **source_document_accuracy**: 1.0
- **evidence_not_found_accuracy**: 1.0
- **questions_evaluated**: 11

## Per-question

| id | category | status | hit | top source |
|---|---|---|---|---|
| q01_exact_pct | exact_pct | ok | True | PSW/TIPP textile product export requirements |
| q02_sro_number | sro | ok | True | SRO 2486(I)/2025 — amendment to Export Polic |
| q03_semantic_deposit | semantic | ok | True | SRO 2486(I)/2025 — amendment to Export Polic |
| q04_china_coo | destination_specific | ok | True | PSW/TIPP textile product export requirements |
| q05_raw_cotton_sbp | raw_cotton_sbp | ok | True | PSW/TIPP textile product export requirements |
| q06_phytosanitary | phytosanitary | ok | True | PSW/TIPP textile product export requirements |
| q07_denim_coo | certificate_of_origin | ok | True | PSW/TIPP textile product export requirements |
| q08_form_e | form_e | ok | True | PSW/TIPP textile product export requirements |
| q09_tshirt_requirements | exact_pct | ok | True | PSW/TIPP textile product export requirements |
| q10_unsupported_product | unsupported | evidence_not_found | True |  |
| q11_no_evidence | no_verified_evidence | evidence_not_found | True |  |

## Notes

- The vector stage is a TF-IDF cosine (offline stand-in for a dense sentence-transformer); the reranker is a lexical scorer (offline stand-in for a transformer cross-encoder). BM25, RRF and parent retrieval are full implementations.
