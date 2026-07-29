# Stage 3 — Regulatory Ingestion Report

Storage backend: **postgresql**
Ingestion version: `regulatory-ingest-v1` | Rule-data version: `sha256:63dedc207d787401...`

## Totals

- Documents discovered: **6**
- Documents ingested (new): **3**
- Documents updated (checksum changed): **0**
- Documents skipped (idempotent, unchanged): **3**
- Documents errored: **0**
- Pages processed: **94**
- OCR pages (no embedded text): **0**
- Parent chunks: **119**
- Child chunks: **374**
- Duplicate chunk ids: **0**

## Per-document

| Key | Status | Pages | OCR | Parents | Children | Validation |
|---|---|--:|--:|--:|--:|---|
| sro_2486_2025_raw_cotton | ingested | 1 | 0 | 4 | 5 | verified |
| epo_2022_base_order | ingested | 93 | 0 | 14 | 75 | partially_verified |
| tipp_textile_product_requirements | ingested | 0 | 0 | 7 | 25 | partially_verified |
| psw_single_declaration_exports_manual | skipped_idempotent | 0 | 0 | 34 | 92 | partially_verified |
| psw_tdap_electronic_certificate_of_origin_manual | skipped_idempotent | 0 | 0 | 40 | 70 | partially_verified |
| tdap_new_exporters_guide_part_a | skipped_idempotent | 0 | 0 | 20 | 107 | partially_verified |

## Notes

- The 93-page Export Policy Order is filtered page-by-page to textile-relevant pages only; non-textile pages are skipped (not the whole archive).
- No original government PDF is modified; only extracted text is chunked.
- OCR pages are 0 because every ingested page carried embedded text. Scanned regulatory pages would use the existing Phase 2B Tesseract OCR fallback.
- Re-running ingestion with unchanged sources is a no-op (idempotent by checksum).
