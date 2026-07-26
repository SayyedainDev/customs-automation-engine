# Synthetic Factory Baseline Evaluation

API: `http://127.0.0.1:8000`

## Live dependency preflight

- **api**: `ok`
- **database**: `{'status': 'ok', 'database': 'connected'}`
- **database_url_driver**: `postgresql+psycopg`
- **groq_configured**: `True`
- **groq_model**: `openai/gpt-oss-20b`
- **real_models_enabled**: `True`
- **degraded_mode**: `False`
- **evidence_results**: `1`
- **tesseract**: `tesseract 5.3.0`

## Text PDFs

Scenarios fully correct: **6/14**

### Field Extraction

| Metric | Value |
| --- | ---: |
| exporter_accuracy | 100.0 |
| buyer_accuracy | 100.0 |
| invoice_number_accuracy | 100.0 |
| invoice_date_accuracy | 100.0 |
| destination_accuracy | 100.0 |
| currency_accuracy | 100.0 |
| invoice_total_accuracy | 100.0 |
| total_net_weight_accuracy | 100.0 |
| total_gross_weight_accuracy | 100.0 |
| product_name_accuracy | 100.0 |
| pct_code_accuracy | 100.0 |
| quantity_accuracy | 100.0 |
| unit_accuracy | 100.0 |
| unit_price_accuracy | 100.0 |
| line_total_accuracy | 100.0 |
| item_net_weight_accuracy | 100.0 |
| item_gross_weight_accuracy | 100.0 |
| package_count_accuracy | 100.0 |
| exact_field_accuracy | 100.0 |
| missing_field_rate | 0.0 |
| incorrect_field_rate | 0.0 |
| manual_review_field_rate | 0.0 |
| line_item_count_accuracy | 100.0 |

### Item Matching

| Metric | Value |
| --- | ---: |
| scenarios_all_items_matched | 7 |
| scenarios_evaluated | 7 |
| match_accuracy | 100.0 |
| line_reference_matches | 7 |
| pct_code_matches | 0 |
| product_name_matches | 0 |

### Compliance

| Metric | Value |
| --- | ---: |
| overall_status_accuracy | 100.0 |
| expected_failed_checks_detected | 100.0 |
| unexpected_failed_checks | 1 |
| missed_failed_checks | 0 |
| false_pass_count | 0 |
| false_failure_count | 0 |
| false_manual_review_count | 0 |
| fallback_behaviour_correct | 100.0 |

### Workflow

| Metric | Value |
| --- | ---: |
| attempted | 14 |
| technical_failures | 7 |
| completed | 6 |
| awaiting_human_review | 0 |
| workflow_failed | 1 |
| human_review_accuracy | 85.71 |
| average_duration_seconds | 35.61 |
| median_duration_seconds | 35.86 |

### Document Processing

| Metric | Value |
| --- | ---: |
| pages_total | 14 |
| pages_requiring_ocr | 0 |
| ocr_attempted | 0 |
| ocr_completed | 0 |
| low_confidence_pages | 0 |

## Scanned PDFs

Scenarios fully correct: **0/14**

### Field Extraction

| Metric | Value |
| --- | ---: |
| exporter_accuracy | 16.67 |
| buyer_accuracy | 50.0 |
| invoice_number_accuracy | 100.0 |
| invoice_date_accuracy | 100.0 |
| destination_accuracy | 100.0 |
| currency_accuracy | 100.0 |
| invoice_total_accuracy | 100.0 |
| total_net_weight_accuracy | 100.0 |
| total_gross_weight_accuracy | 100.0 |
| product_name_accuracy | 16.67 |
| pct_code_accuracy | 16.67 |
| quantity_accuracy | 16.67 |
| unit_accuracy | 16.67 |
| unit_price_accuracy | 16.67 |
| line_total_accuracy | 16.67 |
| item_net_weight_accuracy | 16.67 |
| item_gross_weight_accuracy | 16.67 |
| package_count_accuracy | 100.0 |
| exact_field_accuracy | 61.9 |
| missing_field_rate | 31.75 |
| incorrect_field_rate | 6.35 |
| manual_review_field_rate | 0.0 |
| line_item_count_accuracy | 16.67 |

### Item Matching

| Metric | Value |
| --- | ---: |
| scenarios_all_items_matched | 1 |
| scenarios_evaluated | 6 |
| match_accuracy | 16.67 |
| line_reference_matches | 1 |
| pct_code_matches | 0 |
| product_name_matches | 0 |

### Compliance

| Metric | Value |
| --- | ---: |
| overall_status_accuracy | 16.67 |
| expected_failed_checks_detected | None |
| unexpected_failed_checks | 0 |
| missed_failed_checks | 0 |
| false_pass_count | 0 |
| false_failure_count | 0 |
| false_manual_review_count | 5 |
| fallback_behaviour_correct | 100.0 |

### Workflow

| Metric | Value |
| --- | ---: |
| attempted | 14 |
| technical_failures | 8 |
| completed | 1 |
| awaiting_human_review | 5 |
| workflow_failed | 0 |
| human_review_accuracy | 16.67 |
| average_duration_seconds | 32.69 |
| median_duration_seconds | 38.16 |

### Document Processing

| Metric | Value |
| --- | ---: |
| pages_total | 12 |
| pages_requiring_ocr | 12 |
| ocr_attempted | 12 |
| ocr_completed | 12 |
| low_confidence_pages | 0 |

## Per-scenario results

| Scenario | Variant | OK | Expected | Actual | Lines exp/act | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| clean_raw_cotton | text | NO | passed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| clean_raw_cotton | scanned | NO | passed | manual_review | 1/0 | bad fields: ['exporter', 'buyer', 'item1.product_name', 'item1.pct_code', 'item1.quantity', 'item1.unit']  |
| clean_cotton_yarn | text | yes | passed | passed | 1/1 |  |
| clean_cotton_yarn | scanned | NO | passed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| clean_denim_fabric | text | yes | passed | passed | 1/1 |  |
| clean_denim_fabric | scanned | NO | passed | manual_review | 1/0 | bad fields: ['item1.product_name', 'item1.pct_code', 'item1.quantity', 'item1.unit', 'item1.unit_price', 'item1.line_tot |
| clean_cotton_tshirts | text | yes | passed | passed | 1/1 |  |
| clean_cotton_tshirts | scanned | NO | passed | manual_review | 1/0 | bad fields: ['exporter', 'buyer', 'item1.product_name', 'item1.pct_code', 'item1.quantity', 'item1.unit']  |
| clean_bedsheets | text | yes | passed | passed | 1/1 |  |
| clean_bedsheets | scanned | NO | passed | manual_review | 1/0 | bad fields: ['exporter', 'buyer', 'item1.product_name', 'item1.pct_code', 'item1.quantity', 'item1.unit']  |
| single_line_declared_total_fallback | text | yes | passed | passed | 1/1 |  |
| single_line_declared_total_fallback | scanned | NO | passed | passed | 1/1 | bad fields: ['exporter']  |
| multi_line_shipment | text | NO | passed | None | 3/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| multi_line_shipment | scanned | NO | passed | None | 3/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| non_china_destination_coo | text | yes | passed | passed | 1/1 |  |
| non_china_destination_coo | scanned | NO | passed | manual_review | 1/0 | bad fields: ['exporter', 'item1.product_name', 'item1.pct_code', 'item1.quantity', 'item1.unit', 'item1.unit_price']  |
| error_missing_form_e | text | NO | failed | failed | 1/1 | unexpected: ['xr_common_form_e']  |
| error_missing_form_e | scanned | NO | failed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| error_quantity_mismatch | text | NO | failed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| error_quantity_mismatch | scanned | NO | failed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| error_arithmetic_mismatch | text | NO | failed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| error_arithmetic_mismatch | scanned | NO | failed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| error_weight_inversion | text | NO | failed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| error_weight_inversion | scanned | NO | failed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| error_raw_cotton_missing_sbp_deposit | text | NO | failed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| error_raw_cotton_missing_sbp_deposit | scanned | NO | failed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| error_raw_cotton_deadline_exceeded | text | NO | failed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |
| error_raw_cotton_deadline_exceeded | scanned | NO | failed | None | 1/0 | multi-line HTTP 502: The language model returned malformed structured data. |

## Critical: false legal passes

None. No scenario produced a legal pass it did not deserve.
