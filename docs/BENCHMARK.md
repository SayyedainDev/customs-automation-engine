# CACE Extraction Benchmark (`--variant text`)

Measured comparison across **32 digital PDFs** (16 scenarios, **476 required fields**) using Groq model `openai/gpt-oss-20b` (`temperature=0`, `max_retries=0`).

## 1. Results Summary (Full 32-Document Unpaced Batch, Real 8,000 TPM Limit)

| Approach | Field Accuracy | Silent Error Rate | Abstention (`MANUAL_REVIEW`) | Unflagged Missing | Avg Tokens / Doc (Total) | Latency Avg / Med / Max | Docs w/ `MANUAL_REVIEW` | Hybrid LLM Calls / Doc (% Docs Needing LLM) | Provider Failures |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| A) LLM-only (`single-shot Groq`) | **5.88%** (28/476) | 0.00% (0) | 0.00% (0) | 448 | 224.6 (7,187) | 0.328s / 0.201s / 2.454s | 0/32 | — | 30 |
| B) Deterministic-only (`regex + coords`) | **89.50%** (426/476) | 6.30% (30) | 4.20% (20) | 0 | 0.0 (0) | 0.006s / 0.004s / 0.012s | 4/32 | — | 0 |
| C) CACE Hybrid (`regex/coords + 1-call gapfill`) | **89.50%** (426/476) | 6.30% (30) | 4.20% (20) | 0 | 27.3 (874) | 0.031s / 0.004s / 0.864s | 4/32 | 0.03 (3.1%) | 0 |

### Non-Rate-Limited Subset (2 Documents / 28 Fields Where Approach A Cleared TPM Quota: `clean_bedsheets`)

| Approach | Field Accuracy | Silent Error Rate | Abstention (`MANUAL_REVIEW`) | Unflagged Missing | Avg Tokens / Doc (Total) | Latency Avg / Med / Max | Docs w/ `MANUAL_REVIEW` | Hybrid LLM Calls / Doc (% Docs Needing LLM) | Provider Failures |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| A) LLM-only (`single-shot Groq`) | **100.00%** (28/28) | 0.00% (0) | 0.00% (0) | 0 | 3,593.5 (7,187) | 2.266s / 2.266s / 2.454s | 0/2 | — | 0 |
| B) Deterministic-only (`regex + coords`) | **92.86%** (26/28) | 7.14% (2) | 0.00% (0) | 0 | 0.0 (0) | 0.005s / 0.005s / 0.005s | 0/2 | — | 0 |
| C) CACE Hybrid (`regex/coords + 1-call gapfill`) | **92.86%** (26/28) | 7.14% (2) | 0.00% (0) | 0 | 0.0 (0) | 0.004s / 0.004s / 0.005s | 0/2 | 0.00 (0.0%) | 0 |

## 2. Plain-English Interpretation & Tradeoffs

Across all 32 digital PDFs (476 required fields), sending every document straight to Groq (**Approach A**) immediately ran into Groq's real **8,000 TPM (tokens per minute)** free-tier cap: the first two documents consumed **7,187 tokens** (`4,116` for the invoice and `3,071` for the packing list, averaging `2.266s` per document), leaving insufficient TPM headroom for document #3 (`~4,404` requested tokens) and causing **30 consecutive HTTP 429 failures** (`5.88%` batch yield). By contrast, **Approach B** processed all 32 PDFs locally in **0.006s/doc** at **0 tokens**, and **Approach C (CACE Hybrid)** resolved **31/32 documents (96.9%)** locally at 0 tokens while escalating only **1 document (3.1%)** to a single bounded gap-fill call (`874` total tokens in `0.864s`), finishing all 32 documents at **89.50% accuracy** with **0 provider failures**.

Looking honestly at the tradeoffs where Hybrid did **not** beat the alternatives: on the 2 unthrottled documents (`clean_bedsheets`), **Approach A actually beat both B and C on exact string accuracy (`100.00%` vs. `92.86%`)** because Groq preserved the trailing period on `"Faisalabad Home Textiles (Pvt.) Ltd."` and `"Hangzhou Home Living Co., Ltd."`, whereas deterministic normalizers trimmed `"Ltd."` to `"Ltd"` (raising B/C's `silent_error` rate to `6.30%` overall vs. `0.00%` for A). In addition, Hybrid matched B's accuracy across all 32 digital PDFs (`89.50%` vs. `89.50%`) without improving it and averaged slightly higher latency (`0.031s` vs. `0.006s`) because coordinate parsing already covered 31/32 digital documents—and on the one escalating packing list (`synthetic_customs_test_bundle`), spurious footer text parsed as extra table rows prevented the single-line declared-weight fallback from firing even after Groq returned the header weights (`874` tokens).

## 3. Shared Required Fields & Fairness Rules

Every approach evaluated the exact same 32 PDFs, mapped into the same Pydantic candidate/materialized schemas (`MultiLineInvoiceCandidates`, `MultiLinePackingListCandidates`), passed through the same provenance & confidence gate (`>= 0.80`), and was scored by `extraction_accuracy._compare_value`:

- **Commercial Invoice Header (9)**: `exporter_name, buyer_name, invoice_number, invoice_date, currency, destination_country, invoice_total, declared_net_weight_total, declared_gross_weight_total`
- **Commercial Invoice Line Item (9)**: `line_number, product_name, pct_code, quantity, unit, unit_price, line_total, net_weight, gross_weight`
- **Packing List Header (2)**: `declared_net_weight_total, declared_gross_weight_total`
- **Packing List Item (8)**: `line_number, product_name, pct_code, quantity, unit, net_weight, gross_weight, package_count`

## 4. Per-Field Breakdown (Approach C Hybrid across 32 Digital PDFs)

All other required fields across the 32 PDFs achieved **100%** accuracy. Non-100% fields:

| Document.Field | Correct | Silent Errors | `MANUAL_REVIEW` | Missing | Root Cause Observed |
| :--- | :---: | :---: | :---: | :---: | :--- |
| `commercial_invoice.exporter_name` | 1/16 | 15 | 0 | 0 | Regex normalizer stripped trailing `.` from `(Pvt.) Ltd.` -> `Ltd` (`_compare_value` uses exact `.casefold()` match) |
| `commercial_invoice.buyer_name` | 4/16 | 12 | 0 | 0 | Regex normalizer stripped trailing `.` from `Co., Ltd.` -> `Ltd` |
| `commercial_invoice.product_name` | 17/18 | 1 | 0 | 0 | Column coordinate boundary clipped `"Unclassified Synthetic Fibre Blend"` -> `"Unclassified Synthetic Fibre"` |
| `packing_list.product_name` | 16/18 | 2 | 0 | 0 | `"Blend"` clipped on `manual_review_unsupported_pct_code`; line index prepended (`"1 Cotton knitted T-shirts"`) on `synthetic_customs_test_bundle` |
| `commercial_invoice.net_weight` | 13/17 | 0 | 4 | 0 | Safe fail-closed abstention on 3-line `multi_line_shipment` (3) + `synthetic_customs_test_bundle` (1, no header total) |
| `commercial_invoice.gross_weight` | 13/17 | 0 | 4 | 0 | Same safe fail-closed abstention on multi-line / missing-header-total weights |
| `packing_list.net_weight` | 14/18 | 0 | 4 | 0 | Multi-line packing list (3) + `synthetic_customs_test_bundle` (1, spurious footer rows blocked single-line fallback) |
| `packing_list.gross_weight` | 14/18 | 0 | 4 | 0 | Multi-line packing list (3) + `synthetic_customs_test_bundle` (1, spurious footer rows blocked single-line fallback) |
| `packing_list.package_count` | 14/18 | 0 | 4 | 0 | Coordinate table reconstruction leaves per-item `package_count` unresolved (safely sent to `MANUAL_REVIEW`) |

## 5. Error Analysis (Top 5 Failure Cases per Approach)

### A) LLM-only (`single-shot Groq`)
- `clean_cotton_tshirts__commercial_invoice__text`: provider call failed (`429 TPM rate_limit_exceeded`: Limit 8,000, Used 6,637, Requested 4,404)
- `clean_cotton_tshirts__packing_list__text`: provider call failed (`429 TPM rate_limit_exceeded`: Limit 8,000, Used 6,610, Requested 3,177)
- `clean_cotton_yarn__commercial_invoice__text`: provider call failed (`429 TPM rate_limit_exceeded`)
- `clean_cotton_yarn__packing_list__text`: provider call failed (`429 TPM rate_limit_exceeded`)
- `clean_denim_fabric__commercial_invoice__text`: provider call failed (`429 TPM rate_limit_exceeded`)

### B) Deterministic-only (`regex + coords`)
- `clean_bedsheets__commercial_invoice__text` -> `header.exporter_name` (`silent_error`): expected `'Faisalabad Home Textiles (Pvt.) Ltd.'`, got `'Faisalabad Home Textiles (Pvt.) Ltd'` (trailing-dot normalization mismatch)
- `clean_bedsheets__commercial_invoice__text` -> `header.buyer_name` (`silent_error`): expected `'Hangzhou Home Living Co., Ltd.'`, got `'Hangzhou Home Living Co., Ltd'`
- `manual_review_unsupported_pct_code__commercial_invoice__text` -> `line_items[0].product_name` (`silent_error`): expected `'Unclassified Synthetic Fibre Blend'`, got `'Unclassified Synthetic Fibre'` (table x-coordinate clipping)
- `synthetic_customs_test_bundle__packing_list__text` -> `items[0].product_name` (`silent_error`): expected `'Cotton knitted T-shirts'`, got `'1 Cotton knitted T-shirts'` (line number token merged into description)
- `multi_line_shipment__packing_list__text` -> `items[0].net_weight` (`manual_review`): withheld (`None`) because multi-line table reconstruction intentionally does not guess per-line weights

### C) CACE Hybrid (`regex/coords + 1-call gapfill`)
- `clean_bedsheets__commercial_invoice__text` -> `header.exporter_name` (`silent_error`): expected `'Faisalabad Home Textiles (Pvt.) Ltd.'`, got `'Faisalabad Home Textiles (Pvt.) Ltd'`
- `clean_bedsheets__commercial_invoice__text` -> `header.buyer_name` (`silent_error`): expected `'Hangzhou Home Living Co., Ltd.'`, got `'Hangzhou Home Living Co., Ltd'`
- `manual_review_unsupported_pct_code__commercial_invoice__text` -> `line_items[0].product_name` (`silent_error`): expected `'Unclassified Synthetic Fibre Blend'`, got `'Unclassified Synthetic Fibre'`
- `synthetic_customs_test_bundle__packing_list__text` -> `items[0].product_name` (`silent_error`): expected `'Cotton knitted T-shirts'`, got `'1 Cotton knitted T-shirts'`
- `synthetic_customs_test_bundle__packing_list__text` -> `items[0].net_weight` (`manual_review`): Groq gap-fill resolved header weight `75.00` using `874` tokens (`0.864s`), but spurious footer rows parsed into `items` (`len(items) > 1`) prevented the single-line declared-weight fallback from populating row 1

## 6. Limitations

- **Synthetic dataset**: All 32 evaluated documents come from `synthetic_factory/` fixtures rather than noisy third-party exporter scans.
- **Digital PDFs only (`--variant text`)**: Scanned/Tesseract OCR PDFs (`*_scanned.pdf`) were intentionally deferred to a separate follow-up step, where regex misses are expected to trigger Hybrid gap-fill far more often than 3.1%.
- **Single model & rate tier**: All live LLM runs used Groq `openai/gpt-oss-20b` (`temperature=0`, `max_retries=0`) under an `8,000 TPM` free-tier limit; even at `temperature=0`, hosted LLM decoding can show minor run-to-run variation.
- **Strict punctuation comparison**: `extraction_accuracy._compare_value` compares strings via `.strip().casefold()` without trailing-punctuation stripping, treating `"Ltd"` vs. `"Ltd."` as a `silent_error` across all approaches equally.
