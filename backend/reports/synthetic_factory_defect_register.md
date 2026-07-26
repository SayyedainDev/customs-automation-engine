# Synthetic Factory Defect Register

Derived from a live run against `http://127.0.0.1:8000`. Every record below corresponds to an observed failure; nothing is hypothetical.

- Scenarios attempted: **28**
- Fully correct: **6**
- Defect records: **25**
- False legal passes: **0**

## Families

| Family | Title | Category | Severity | Occurrences | Fix allowed |
| --- | --- | --- | --- | ---: | --- |
| BLOCKED | Not measured - model provider refused the request | `API_or_infrastructure` | blocker | 11 | NO |
| DEF-001 | Provider schema rejection collapses the whole document | `LLM_line_item_extraction` | critical | 4 | yes |
| DEF-002 | OCR silently drops invoice product rows | `OCR` | high | 5 | yes |
| DEF-003 | Field label absorbed into the extracted value | `LLM_header_extraction` | medium | 5 | yes |

## DEF-001 — Provider schema rejection collapses the whole document

- **Category**: `LLM_line_item_extraction`
- **Severity**: critical
- **Module**: `app/services/structured_extraction_service.py`

**Root cause.** Pydantic emits a JSON-Schema `pattern` containing a negative lookahead for the string branch of Decimal fields. Groq's constrained decoder cannot compile lookarounds, so it rejected every Phase 2C strict schema (HTTP 400 pattern_unsupported_feature) and the request silently fell back to unconstrained json_object mode. In that mode the model intermittently emitted empty strings between array objects ([obj, '', obj, '', obj]); local Pydantic validation correctly refused the payload, discarding every correctly-extracted line and failing the request with HTTP 502.

**Proposed fix.** Strip only the unsupported regex constructs from the provider-facing transport schema so strict mode is usable again (local Pydantic validation is unchanged), and add staged per-line extraction as a fallback so one malformed row can no longer destroy the valid rows.

**Affected (4):** `clean_raw_cotton` (text), `clean_cotton_yarn` (scanned), `multi_line_shipment` (text), `multi_line_shipment` (scanned)

## DEF-002 — OCR silently drops invoice product rows

- **Category**: `OCR`
- **Severity**: high
- **Module**: `app/core/config.py (ocr_page_segmentation_mode)`

**Root cause.** Tesseract ran with PSM 6 ('assume a single uniform block of text'). An invoice page is not a uniform block: it is a label-value header, a wide sparse table, then a footer. PSM 6 discarded product rows whose columns are separated by large horizontal gaps, so the model received a table header with no rows beneath it and returned zero line items. OCR confidence stayed ~0.94, so the confidence gate could not see the loss. Measured across all 14 scanned fixtures PSM 6 recovered 52/62 expected tokens versus 62/62 for PSM 3 and PSM 4.

**Proposed fix.** Use PSM 4 ('single column of text of variable sizes'), which recovers every row and additionally keeps a label and its value on one line, unlike PSM 3 which splits the label and value columns into separate blocks.

**Affected (5):** `clean_raw_cotton` (scanned), `clean_denim_fabric` (scanned), `clean_cotton_tshirts` (scanned), `clean_bedsheets` (scanned), `non_china_destination_coo` (scanned)

## DEF-003 — Field label absorbed into the extracted value

- **Category**: `LLM_header_extraction`
- **Severity**: medium
- **Module**: `app/services/extraction/ocr_extractor.py + header prompt`

**Root cause.** On OCR text where a label and its value share one line ('Exporter Multan Raw Cotton Traders (Pvt.) Ltd.'), the model returned the label as part of the value.

**Proposed fix.** Instruct the extractor explicitly that a printed field label is not part of the field value; verify against the manifest rather than string-repairing the model output.

**Affected (5):** `clean_raw_cotton` (scanned), `clean_cotton_tshirts` (scanned), `clean_bedsheets` (scanned), `single_line_declared_total_fallback` (scanned), `non_china_destination_coo` (scanned)

## BLOCKED — Not measured - model provider refused the request

- **Category**: `API_or_infrastructure`
- **Severity**: blocker
- **Module**: `external: Groq account quota`

**Root cause.** The Groq account exhausted its daily token quota (TPD limit 200000) mid-run, so these requests returned HTTP 429 in under 6s without the model ever seeing the document. These rows are an invalid measurement, not evidence of an extraction defect, and must be re-run once quota is available before any conclusion is drawn about them.

**Proposed fix.** Re-run once quota resets, or raise the account tier. No application change can make an unserved request produce a valid result.

**Affected (11):** `error_missing_form_e` (scanned), `error_quantity_mismatch` (text), `error_quantity_mismatch` (scanned), `error_arithmetic_mismatch` (text), `error_arithmetic_mismatch` (scanned), `error_weight_inversion` (text), `error_weight_inversion` (scanned), `error_raw_cotton_missing_sbp_deposit` (text), `error_raw_cotton_missing_sbp_deposit` (scanned), `error_raw_cotton_deadline_exceeded` (text), `error_raw_cotton_deadline_exceeded` (scanned)
