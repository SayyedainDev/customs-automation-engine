# Export Policy Amendment OCR Validation Report

Generated: 2026-07-22
Legal cutoff: 2026-07-22

## Outcome

- Canonical PDFs processed: **8**
- Original pages processed: **16**
- OCR text files created: **8**
- Searchable OCR PDFs created and text-layer validated: **8**
- Page-count matches: **8 of 8**
- OCR execution failures: **0**
- Documents with pages requiring manual OCR review: **5**
- Original source checksum changes: **0**

All source PDFs remained unchanged. The searchable PDFs are processed derivatives containing the original page images plus an invisible OCR text layer. OCR text is **not legally validated** and must not be used for compliance-rule execution yet.

## Document validation

| SRO | OCR status | Original pages | OCR pages | Manual-review pages | PCT codes found or resolved | Amendment relationship | Extracted text | Searchable PDF |
|---|---:|---:|---:|---|---|---|---|---|
| 561(I)/2023 | Success | 3 | 3 | None | None; route amendment | Direct amendment to SRO 544(I)/2022, paragraph 7(5) | `amendments/sro_561_i_2023_amendment_to_export_policy_order_2022_ocr.txt` | `amendments/sro_561_i_2023_amendment_to_export_policy_order_2022_ocr_searchable.pdf` |
| 1087(I)/2023 | Success with review | 1 | 1 | 1 | No PCT printed; parent serial resolves to 1516.1000, 1516.2010, 1516.2020, 1518.0000 | Direct amendment to paragraph 7(6)–(7) and Schedule II serial 8 | `amendments/sro_1087_i_2023_amendment_to_export_policy_order_2022_ocr.txt` | `amendments/sro_1087_i_2023_amendment_to_export_policy_order_2022_ocr_searchable.pdf` |
| 629(I)/2024 | Success with review | 7 | 7 | 1, 2, 6 | Numerous printed old/new codes across Schedules and Appendices A/B/C/G/H/J; see detailed section and JSON | Direct code/description substitutions in SRO 544(I)/2022 | `amendments/sro_629_i_2024_amendment_to_export_policy_order_2022_ocr.txt` | `amendments/sro_629_i_2024_amendment_to_export_policy_order_2022_ocr_searchable.pdf` |
| 1021(I)/2024 | Success with review | 1 | 1 | 1 | No PCT printed; parent serial resolves to 1001.1900, 1001.9900, 1101.0010 | Direct amendment to Schedule I serial 19, column (4) | `amendments/sro_1021_i_2024_amendment_to_export_policy_order_2022_ocr.txt` | `amendments/sro_1021_i_2024_amendment_to_export_policy_order_2022_ocr_searchable.pdf` |
| 705(I)/2025 | Success | 1 | 1 | None | 3104.3000 | Direct amendment to Schedule I serial 12, column (4) | `amendments/sro_705_i_2025_amendment_to_export_policy_order_2022_ocr.txt` | `amendments/sro_705_i_2025_amendment_to_export_policy_order_2022_ocr_searchable.pdf` |
| 1727(I)/2025 | Success with review | 1 | 1 | 1 | No PCT printed; parent serials resolve to chapter 41/43 hide and leather codes listed in JSON | Direct amendment omitting Appendix G serials 174–175, 181–186 and 189–198 | `amendments/sro_1727_i_2025_amendment_to_export_policy_order_2022_ocr.txt` | `amendments/sro_1727_i_2025_amendment_to_export_policy_order_2022_ocr_searchable.pdf` |
| 1902(I)/2025 | Success | 1 | 1 | None | “Respective headings” is printed; parent Appendix G serial 170 resolves to 4101.5090 | Adds Schedule I serial 22 and changes Appendix G serial 170 | `amendments/sro_1902_i_2025_amendment_to_export_policy_order_2022_ocr.txt` | `amendments/sro_1902_i_2025_amendment_to_export_policy_order_2022_ocr_searchable.pdf` |
| 2486(I)/2025 | Success with review | 1 | 1 | 1 | No PCT printed; parent Schedule II serial 9 resolves to 5201.0000 | Direct substitution of Schedule II serial 9, column (4) | `amendments/sro_2486_i_2025_amendment_to_export_policy_order_2022_ocr.txt` | `amendments/sro_2486_i_2025_amendment_to_export_policy_order_2022_ocr_searchable.pdf` |

All paths in the table are relative to `regulatory_data/processed/commerce/export_policy/`.

## Pages requiring manual review

| SRO | Page | Problem found |
|---|---:|---|
| 1087(I)/2023 | 1 | The OCR text lost the beginning of the quoted phrase being omitted from Schedule II serial 8. |
| 629(I)/2024 | 1 | The handwritten SRO number was not recognized and “30 April” was misread. |
| 629(I)/2024 | 2 | New PCT `2903.4910` was misread as `2303.4910`; some footer-area text was lost. |
| 629(I)/2024 | 6 | Part of the serial 342 PCT sequence was corrupted by OCR. |
| 1021(I)/2024 | 1 | The handwritten SRO number was missed and several heading/date words were misspelled. |
| 1727(I)/2025 | 1 | The handwritten SRO number and part of the date were missed; some column references were corrupted. |
| 2486(I)/2025 | 1 | Most of condition (i), including the 1% SBP security-deposit wording, was not recovered correctly. |

No page was completely missing OCR text. Pages not listed above still require ordinary legal proofreading before ingestion, even though no material OCR defect was identified during this pass.

## PCT validation notes

Only SRO 629(I)/2024 and SRO 705(I)/2025 print numeric PCT codes directly in their amendment text. Other numeric codes in the structured JSON were resolved from the cited serial numbers in the local consolidated Export Policy Order and are labelled accordingly.

For SRO 629(I)/2024, the OCR found substitutions involving:

- Schedule I: headings 2844, 4407 and 9701–9706.
- Appendices A–C: controlled-chemical codes in 2903, 2920, 2931 and 2933.
- Appendix G: 0403 and 0410.
- Appendix H: 3002, 3006, 3822, 4015 and 9027.
- Appendix J: 0709, 0712, 0802, 0813, 4401, 4418 and 4420.

The full reviewed code arrays are in `export_policy_amendments.json`. The raw OCR text must not be used to load code substitutions automatically because it contains demonstrated digit errors.

## Extracted amendment summary

| SRO | Source page | Extracted change | Textile MVP relevance |
|---|---:|---|---|
| 561(I)/2023 | 3 | Adds Angoor Adda after Karez in paragraph 7(5). | Conditional route relevance |
| 1087(I)/2023 | 1 | Omits paragraph 7(6)–(7) and an Afghanistan-only exclusion in Schedule II serial 8. | Not directly textile-specific |
| 629(I)/2024 | 1–7 | Substitutes many legacy PCT codes and product descriptions across schedules/appendices. | Conditional code-mapping relevance |
| 1021(I)/2024 | 1 | Omits Schedule I serial 19, column (4), concerning wheat/wheat products. | Not relevant |
| 705(I)/2025 | 1 | Adds a Gwadar Free Zone export permission for K2SO4, PCT 3104.3000. | Not relevant |
| 1727(I)/2025 | 1 | Omits specified Appendix G hide, skin and leather rows. | Not relevant to current scope |
| 1902(I)/2025 | 1 | Creates conditional donkey-hide export permission and removes the former ban note. | Not relevant |
| 2486(I)/2025 | 1 | Replaces cotton export conditions with SBP deposit, LC, 180-day shipment and forfeiture requirements. | **Directly relevant** |

## Validation boundary

OCR, searchable-PDF creation, page-count validation and structured extraction are complete. Legal validation remains pending. No embeddings, RAG ingestion, database insertion, or compliance-rule execution was performed.
