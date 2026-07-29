# Stage 3 — Regulatory Ingestion Report

Storage backend: **postgresql**
Ingestion version: `regulatory-ingest-v1` | Rule-data version: `sha256:3258d244212f77ef...`

## Totals

- Documents discovered: **9**
- Documents ingested (new): **3**
- Documents updated (checksum changed): **1**
- Documents skipped (idempotent, unchanged): **5**
- Documents errored: **0**
- Pages processed: **1240**
- OCR pages (no embedded text): **0**
- Parent chunks: **1078**
- Child chunks: **6756**
- Duplicate chunk ids: **0**

## Per-document

| Key | Status | Pages | OCR | Parents | Children | Validation |
|---|---|--:|--:|--:|--:|---|
| sro_2486_2025_raw_cotton | skipped_idempotent | 0 | 0 | 4 | 5 | verified |
| epo_2022_base_order | skipped_idempotent | 0 | 0 | 14 | 75 | partially_verified |
| tipp_textile_product_requirements | updated | 0 | 0 | 19 | 62 | partially_verified |
| psw_single_declaration_exports_manual | skipped_idempotent | 0 | 0 | 34 | 92 | partially_verified |
| psw_tdap_electronic_certificate_of_origin_manual | skipped_idempotent | 0 | 0 | 40 | 70 | partially_verified |
| tdap_new_exporters_guide_part_a | skipped_idempotent | 0 | 0 | 20 | 107 | partially_verified |
| customs_act_1969 | ingested | 279 | 0 | 279 | 1561 | partially_verified |
| customs_rules_2001 | ingested | 634 | 0 | 634 | 4615 | partially_verified |
| pakistan_customs_tariff_textile_chapters | ingested | 327 | 0 | 34 | 169 | partially_verified |

## Notes

- The 93-page Export Policy Order is filtered page-by-page to textile-relevant pages only; non-textile pages are skipped (not the whole archive).
- No original government PDF is modified; only extracted text is chunked.
- OCR pages are 0 because every ingested page carried embedded text. Scanned regulatory pages would use the existing Phase 2B Tesseract OCR fallback.
- Re-running ingestion with unchanged sources is a no-op (idempotent by checksum).
