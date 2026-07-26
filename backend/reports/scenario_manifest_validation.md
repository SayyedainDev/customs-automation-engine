# Scenario Manifest Validation

Every factual expectation in `synthetic_factory/scenario_manifest.json` is cross-checked below against the literal text of the generated PDFs. A `yes` in **Found in PDF** means the exact expected string is present in the document, so the manifest is describing the shipped artefact rather than an assumption.

Expected *statuses* are hand-declared from each scenario's purpose and are deliberately NOT produced by the compliance engine, so they can grade it.

**Result: 252/252 shipment expectations and 625/625 supporting-document expectations verified directly against PDF text.**

## `clean_raw_cotton`

- Purpose: A fully compliant raw-cotton export: every SRO 2486(I)/2025 condition is satisfied and shipment is inside the 180-day window.
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Multan Raw Cotton Traders (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Al Ain Fibre Trading LLC` | commercial invoice | yes |
| expected_invoice_number | `MRC-INV-2026-014` | commercial invoice | yes |
| expected_invoice_date | `2026-05-28` | commercial invoice | yes |
| expected_destination | `United Arab Emirates` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `2000.00` | commercial invoice | yes |
| expected_total_net_weight | `1000.00` | commercial invoice | yes |
| expected_total_gross_weight | `1025.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `MRC-INV-2026-014` | packing list | yes |
| item1.product_name | `Raw cotton, other` | commercial invoice | yes |
| item1.pct_code | `5201.0090` | commercial invoice | yes |
| item1.line_total | `2000.00` | commercial invoice | yes |
| item1.packing_quantity | `1000` | packing list | yes |
| item1.packing_net_weight | `1000.00` | packing list | yes |
| item1.invoice_net_weight | `1000.00` | commercial invoice | yes |

## `clean_cotton_yarn`

- Purpose: A fully compliant cotton-yarn export to China under CPFTA.
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Faisalabad Yarn Spinners (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Suzhou Textile Import Co., Ltd.` | commercial invoice | yes |
| expected_invoice_number | `FYS-INV-2026-101` | commercial invoice | yes |
| expected_invoice_date | `2026-06-10` | commercial invoice | yes |
| expected_destination | `China` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `16000.00` | commercial invoice | yes |
| expected_total_net_weight | `5000.00` | commercial invoice | yes |
| expected_total_gross_weight | `5050.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `FYS-INV-2026-101` | packing list | yes |
| item1.product_name | `Cotton yarn` | commercial invoice | yes |
| item1.pct_code | `5205.1100` | commercial invoice | yes |
| item1.line_total | `16000.00` | commercial invoice | yes |
| item1.packing_quantity | `5000` | packing list | yes |
| item1.packing_net_weight | `5000.00` | packing list | yes |
| item1.invoice_net_weight | `5000.00` | commercial invoice | yes |

## `clean_denim_fabric`

- Purpose: A fully compliant denim-fabric export to China under CPFTA.
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Karachi Denim Mills (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Guangzhou Fashion Fabrics Co., Ltd.` | commercial invoice | yes |
| expected_invoice_number | `KDM-INV-2026-207` | commercial invoice | yes |
| expected_invoice_date | `2026-06-18` | commercial invoice | yes |
| expected_destination | `China` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `9000.00` | commercial invoice | yes |
| expected_total_net_weight | `1800.00` | commercial invoice | yes |
| expected_total_gross_weight | `1850.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `KDM-INV-2026-207` | packing list | yes |
| item1.product_name | `Denim fabric` | commercial invoice | yes |
| item1.pct_code | `5209.4200` | commercial invoice | yes |
| item1.line_total | `9000.00` | commercial invoice | yes |
| item1.packing_quantity | `2000` | packing list | yes |
| item1.packing_net_weight | `1800.00` | packing list | yes |
| item1.invoice_net_weight | `1800.00` | commercial invoice | yes |

## `clean_cotton_tshirts`

- Purpose: A fully compliant cotton T-shirt export to China with weights printed on the invoice line itself.
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Lahore Cotton Garments (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Shanghai Sample Trading Co., Ltd.` | commercial invoice | yes |
| expected_invoice_number | `LCG-INV-2026-002` | commercial invoice | yes |
| expected_invoice_date | `2026-06-25` | commercial invoice | yes |
| expected_destination | `China` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `550.00` | commercial invoice | yes |
| expected_total_net_weight | `75.00` | commercial invoice | yes |
| expected_total_gross_weight | `80.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `LCG-INV-2026-002` | packing list | yes |
| item1.product_name | `Cotton knitted T-shirts` | commercial invoice | yes |
| item1.pct_code | `6109.1000` | commercial invoice | yes |
| item1.line_total | `550.00` | commercial invoice | yes |
| item1.packing_quantity | `100` | packing list | yes |
| item1.packing_net_weight | `75.00` | packing list | yes |
| item1.invoice_net_weight | `75.00` | commercial invoice | yes |

## `clean_bedsheets`

- Purpose: A fully compliant cotton bed-sheet export to China under CPFTA.
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Faisalabad Home Textiles (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Hangzhou Home Living Co., Ltd.` | commercial invoice | yes |
| expected_invoice_number | `FHT-INV-2026-305` | commercial invoice | yes |
| expected_invoice_date | `2026-07-01` | commercial invoice | yes |
| expected_destination | `China` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `13500.00` | commercial invoice | yes |
| expected_total_net_weight | `1200.00` | commercial invoice | yes |
| expected_total_gross_weight | `1260.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `FHT-INV-2026-305` | packing list | yes |
| item1.product_name | `Cotton bed sheets, mill-made` | commercial invoice | yes |
| item1.pct_code | `6302.3110` | commercial invoice | yes |
| item1.line_total | `13500.00` | commercial invoice | yes |
| item1.packing_quantity | `2000` | packing list | yes |
| item1.packing_net_weight | `1200.00` | packing list | yes |
| item1.invoice_net_weight | `1200.00` | commercial invoice | yes |

## `single_line_declared_total_fallback`

- Purpose: A real single-line invoice that prints the weight only once as a document-level declared total, exercising the deterministic single_line_declared_total fallback.
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Lahore Cotton Garments (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Shanghai Sample Trading Co., Ltd.` | commercial invoice | yes |
| expected_invoice_number | `LCG-INV-2026-001` | commercial invoice | yes |
| expected_invoice_date | `2026-07-20` | commercial invoice | yes |
| expected_destination | `China` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `550.00` | commercial invoice | yes |
| expected_total_net_weight | `75.00` | commercial invoice | yes |
| expected_total_gross_weight | `80.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `LCG-INV-2026-001` | packing list | yes |
| item1.product_name | `Cotton knitted T-shirts` | commercial invoice | yes |
| item1.pct_code | `6109.1000` | commercial invoice | yes |
| item1.line_total | `550.00` | commercial invoice | yes |
| item1.packing_quantity | `100` | packing list | yes |
| item1.packing_net_weight | `75.00` | packing list | yes |
| item1.invoice_net_weight | `(absent from the invoice line by design)` | commercial invoice | yes |

## `multi_line_shipment`

- Purpose: Three products on one invoice/packing-list pair, confirming per-item matching and that the single-line weight fallback does NOT fire for a multi-line invoice.
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **28/28**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Punjab Textile Exporters Consortium` | commercial invoice | yes |
| expected_buyer | `Shanghai Sample Trading Co., Ltd.` | commercial invoice | yes |
| expected_invoice_number | `PTE-INV-2026-410` | commercial invoice | yes |
| expected_invoice_date | `2026-07-08` | commercial invoice | yes |
| expected_destination | `China` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `4825.00` | commercial invoice | yes |
| expected_total_net_weight | `705.00` | commercial invoice | yes |
| expected_total_gross_weight | `730.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `PTE-INV-2026-410` | packing list | yes |
| item1.product_name | `Cotton knitted T-shirts` | commercial invoice | yes |
| item1.pct_code | `6109.1000` | commercial invoice | yes |
| item1.line_total | `550.00` | commercial invoice | yes |
| item1.packing_quantity | `100` | packing list | yes |
| item1.packing_net_weight | `75.00` | packing list | yes |
| item1.invoice_net_weight | `75.00` | commercial invoice | yes |
| item2.product_name | `Denim fabric` | commercial invoice | yes |
| item2.pct_code | `5209.4200` | commercial invoice | yes |
| item2.line_total | `2250.00` | commercial invoice | yes |
| item2.packing_quantity | `500` | packing list | yes |
| item2.packing_net_weight | `450.00` | packing list | yes |
| item2.invoice_net_weight | `450.00` | commercial invoice | yes |
| item3.product_name | `Cotton bed sheets, mill-made` | commercial invoice | yes |
| item3.pct_code | `6302.3110` | commercial invoice | yes |
| item3.line_total | `2025.00` | commercial invoice | yes |
| item3.packing_quantity | `300` | packing list | yes |
| item3.packing_net_weight | `180.00` | packing list | yes |
| item3.invoice_net_weight | `180.00` | commercial invoice | yes |

## `non_china_destination_coo`

- Purpose: A cotton-yarn export to Germany, exercising the general TDAP certificate-of-origin path instead of the China/CPFTA path.
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Faisalabad Yarn Spinners (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Bremen Textile Handels GmbH` | commercial invoice | yes |
| expected_invoice_number | `FYS-INV-2026-118` | commercial invoice | yes |
| expected_invoice_date | `2026-06-30` | commercial invoice | yes |
| expected_destination | `Germany` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `16000.00` | commercial invoice | yes |
| expected_total_net_weight | `5000.00` | commercial invoice | yes |
| expected_total_gross_weight | `5050.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `FYS-INV-2026-118` | packing list | yes |
| item1.product_name | `Cotton yarn` | commercial invoice | yes |
| item1.pct_code | `5205.1100` | commercial invoice | yes |
| item1.line_total | `16000.00` | commercial invoice | yes |
| item1.packing_quantity | `5000` | packing list | yes |
| item1.packing_net_weight | `5000.00` | packing list | yes |
| item1.invoice_net_weight | `5000.00` | commercial invoice | yes |

## `error_missing_form_e`

- Purpose: A positively-required export document is absent.
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Lahore Cotton Garments (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Shanghai Sample Trading Co., Ltd.` | commercial invoice | yes |
| expected_invoice_number | `LCG-INV-2026-011` | commercial invoice | yes |
| expected_invoice_date | `2026-06-12` | commercial invoice | yes |
| expected_destination | `China` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `550.00` | commercial invoice | yes |
| expected_total_net_weight | `75.00` | commercial invoice | yes |
| expected_total_gross_weight | `80.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `LCG-INV-2026-011` | packing list | yes |
| item1.product_name | `Cotton knitted T-shirts` | commercial invoice | yes |
| item1.pct_code | `6109.1000` | commercial invoice | yes |
| item1.line_total | `550.00` | commercial invoice | yes |
| item1.packing_quantity | `100` | packing list | yes |
| item1.packing_net_weight | `75.00` | packing list | yes |
| item1.invoice_net_weight | `75.00` | commercial invoice | yes |

## `error_quantity_mismatch`

- Purpose: The invoice and packing list state conflicting quantities.
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Lahore Cotton Garments (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Shanghai Sample Trading Co., Ltd.` | commercial invoice | yes |
| expected_invoice_number | `LCG-INV-2026-012` | commercial invoice | yes |
| expected_invoice_date | `2026-06-12` | commercial invoice | yes |
| expected_destination | `China` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `550.00` | commercial invoice | yes |
| expected_total_net_weight | `75.00` | commercial invoice | yes |
| expected_total_gross_weight | `80.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `LCG-INV-2026-012` | packing list | yes |
| item1.product_name | `Cotton knitted T-shirts` | commercial invoice | yes |
| item1.pct_code | `6109.1000` | commercial invoice | yes |
| item1.line_total | `550.00` | commercial invoice | yes |
| item1.packing_quantity | `99` | packing list | yes |
| item1.packing_net_weight | `75.00` | packing list | yes |
| item1.invoice_net_weight | `75.00` | commercial invoice | yes |

## `error_arithmetic_mismatch`

- Purpose: Quantity x unit price does not equal the declared line total.
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Lahore Cotton Garments (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Shanghai Sample Trading Co., Ltd.` | commercial invoice | yes |
| expected_invoice_number | `LCG-INV-2026-013` | commercial invoice | yes |
| expected_invoice_date | `2026-06-12` | commercial invoice | yes |
| expected_destination | `China` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `500.00` | commercial invoice | yes |
| expected_total_net_weight | `75.00` | commercial invoice | yes |
| expected_total_gross_weight | `80.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `LCG-INV-2026-013` | packing list | yes |
| item1.product_name | `Cotton knitted T-shirts` | commercial invoice | yes |
| item1.pct_code | `6109.1000` | commercial invoice | yes |
| item1.line_total | `500.00` | commercial invoice | yes |
| item1.packing_quantity | `100` | packing list | yes |
| item1.packing_net_weight | `75.00` | packing list | yes |
| item1.invoice_net_weight | `75.00` | commercial invoice | yes |

## `error_weight_inversion`

- Purpose: Declared gross weight is lower than declared net weight.
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Lahore Cotton Garments (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Shanghai Sample Trading Co., Ltd.` | commercial invoice | yes |
| expected_invoice_number | `LCG-INV-2026-014` | commercial invoice | yes |
| expected_invoice_date | `2026-06-12` | commercial invoice | yes |
| expected_destination | `China` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `550.00` | commercial invoice | yes |
| expected_total_net_weight | `80.00` | commercial invoice | yes |
| expected_total_gross_weight | `75.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `LCG-INV-2026-014` | packing list | yes |
| item1.product_name | `Cotton knitted T-shirts` | commercial invoice | yes |
| item1.pct_code | `6109.1000` | commercial invoice | yes |
| item1.line_total | `550.00` | commercial invoice | yes |
| item1.packing_quantity | `100` | packing list | yes |
| item1.packing_net_weight | `80.00` | packing list | yes |
| item1.invoice_net_weight | `80.00` | commercial invoice | yes |

## `error_raw_cotton_missing_sbp_deposit`

- Purpose: A raw-cotton-specific required document under SRO 2486(I)/2025 is absent.
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Multan Raw Cotton Traders (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Al Ain Fibre Trading LLC` | commercial invoice | yes |
| expected_invoice_number | `MRC-INV-2026-015` | commercial invoice | yes |
| expected_invoice_date | `2026-05-28` | commercial invoice | yes |
| expected_destination | `United Arab Emirates` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `2000.00` | commercial invoice | yes |
| expected_total_net_weight | `1000.00` | commercial invoice | yes |
| expected_total_gross_weight | `1025.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `MRC-INV-2026-015` | packing list | yes |
| item1.product_name | `Raw cotton, other` | commercial invoice | yes |
| item1.pct_code | `5201.0090` | commercial invoice | yes |
| item1.line_total | `2000.00` | commercial invoice | yes |
| item1.packing_quantity | `1000` | packing list | yes |
| item1.packing_net_weight | `1000.00` | packing list | yes |
| item1.invoice_net_weight | `1000.00` | commercial invoice | yes |

## `error_raw_cotton_deadline_exceeded`

- Purpose: Raw cotton shipped outside the SRO 2486(I)/2025 180-day window.
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Multan Raw Cotton Traders (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Al Ain Fibre Trading LLC` | commercial invoice | yes |
| expected_invoice_number | `MRC-INV-2026-016` | commercial invoice | yes |
| expected_invoice_date | `2026-07-10` | commercial invoice | yes |
| expected_destination | `United Arab Emirates` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `2000.00` | commercial invoice | yes |
| expected_total_net_weight | `1000.00` | commercial invoice | yes |
| expected_total_gross_weight | `1025.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `MRC-INV-2026-016` | packing list | yes |
| item1.product_name | `Raw cotton, other` | commercial invoice | yes |
| item1.pct_code | `5201.0090` | commercial invoice | yes |
| item1.line_total | `2000.00` | commercial invoice | yes |
| item1.packing_quantity | `1000` | packing list | yes |
| item1.packing_net_weight | `1000.00` | packing list | yes |
| item1.invoice_net_weight | `1000.00` | commercial invoice | yes |

## `manual_review_unsupported_pct_code`

- Purpose: The PCT code is well-formed but outside the curated textile MVP catalog.
- Hand-declared expected status: **manual_review**
- PDF evidence checks passed: **16/16**

| Field | Expected value | Source | Found in PDF |
| --- | --- | --- | --- |
| expected_exporter | `Lahore Cotton Garments (Pvt.) Ltd.` | commercial invoice | yes |
| expected_buyer | `Shanghai Sample Trading Co., Ltd.` | commercial invoice | yes |
| expected_invoice_number | `LCG-INV-2026-017` | commercial invoice | yes |
| expected_invoice_date | `2026-06-12` | commercial invoice | yes |
| expected_destination | `China` | commercial invoice | yes |
| expected_currency | `USD` | commercial invoice | yes |
| expected_invoice_total | `550.00` | commercial invoice | yes |
| expected_total_net_weight | `75.00` | commercial invoice | yes |
| expected_total_gross_weight | `80.00` | commercial invoice | yes |
| expected_invoice_number (packing cross-reference) | `LCG-INV-2026-017` | packing list | yes |
| item1.product_name | `Unclassified Synthetic Fibre Blend` | commercial invoice | yes |
| item1.pct_code | `9999.9999` | commercial invoice | yes |
| item1.line_total | `550.00` | commercial invoice | yes |
| item1.packing_quantity | `100` | packing list | yes |
| item1.packing_net_weight | `75.00` | packing list | yes |
| item1.invoice_net_weight | `75.00` | commercial invoice | yes |

# Supporting-Document Bundles

Each supporting document's declared field values are checked against the literal text of the generated supporting PDF, exactly as above.

## `clean_cotton_yarn_supporting`

- Purpose: Form-E, CPFTA certificate of origin, goods declaration, bill of lading and export contract, all agreeing with the invoice.
- Injected defect: none
- Documents: **5 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **50/50**

## `clean_denim_fabric_supporting`

- Purpose: Form-E, CPFTA certificate of origin, goods declaration, bill of lading and export contract, all agreeing with the invoice.
- Injected defect: none
- Documents: **5 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **50/50**

## `clean_cotton_tshirts_supporting`

- Purpose: Form-E, CPFTA certificate of origin, goods declaration, bill of lading and export contract, all agreeing with the invoice.
- Injected defect: none
- Documents: **5 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **50/50**

## `clean_bedsheets_supporting`

- Purpose: Form-E, CPFTA certificate of origin, goods declaration, bill of lading and export contract, all agreeing with the invoice.
- Injected defect: none
- Documents: **5 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **50/50**

## `non_china_destination_coo_supporting`

- Purpose: The CPFTA preferential certificate does not apply to Germany, so a general non-preferential certificate of origin is presented.
- Injected defect: none
- Documents: **5 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **50/50**

## `clean_raw_cotton_supporting`

- Purpose: Every document SRO 2486(I)/2025 requires for raw cotton, plus the commercial records, all agreeing with the invoice.
- Injected defect: none
- Documents: **9 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **passed**
- PDF evidence checks passed: **83/83**

## `supporting_form_e_claimed_without_upload`

- Purpose: The caller lists form_e as an uploaded document type but supplies no document UUID. A name is not evidence.
- Injected defect: form_e claimed as a string with no uploaded file
- Documents: **1 uploaded**, 1 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **10/10**

## `supporting_wrong_type_uploaded_as_form_e`

- Purpose: A readable, valid bill of lading is uploaded under the form_e slot. It must not satisfy the Form-E requirement.
- Injected defect: bill of lading uploaded as form_e
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **18/18**

## `supporting_coo_wrong_invoice_number`

- Purpose: Every other field agrees with the invoice, so exactly one check may fail.
- Injected defect: wrong invoice reference on the certificate of origin
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **21/21**

## `supporting_coo_wrong_exporter`

- Purpose: Every other field agrees with the invoice, so exactly one check may fail.
- Injected defect: wrong exporter on the certificate of origin
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **21/21**

## `supporting_coo_wrong_destination`

- Purpose: Every other field agrees with the invoice, so exactly one check may fail.
- Injected defect: wrong destination country on the certificate of origin
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **21/21**

## `supporting_coo_wrong_pct_code`

- Purpose: Every other field agrees with the invoice, so exactly one check may fail.
- Injected defect: wrong PCT code on the certificate of origin
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **21/21**

## `supporting_coo_missing_certificate_number`

- Purpose: The number is genuinely absent from the page, so it must stay null and become a manual review - never a repaired or invented value.
- Injected defect: certificate number absent from the certificate of origin
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **manual_review**
- PDF evidence checks passed: **20/20**

## `supporting_expired_import_permit`

- Purpose: The permit is readable and matches the shipment, but its printed expiry date is in the past.
- Injected defect: import permit expiry date in the past
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **19/19**

## `supporting_sbp_deposit_wrong_percentage`

- Purpose: The document is genuine in form but records a deposit below the percentage the SRO requires.
- Injected defect: deposit percentage 0.5 instead of 1
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **20/20**

## `supporting_sbp_confirmation_wrong_deposit_reference`

- Purpose: The confirmation quotes a deposit receipt number that does not match the deposit proof supplied with this shipment.
- Injected defect: related deposit reference does not match the deposit proof
- Documents: **3 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **27/27**

## `supporting_lc_wrong_beneficiary`

- Purpose: The credit is readable and in date, but its beneficiary is not the exporter on the invoice.
- Injected defect: letter-of-credit beneficiary is not the exporter
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **21/21**

## `supporting_lc_expired_shipment_deadline`

- Purpose: The credit's printed latest-shipment date is before the declared shipment date, so the shipment cannot be presented under it.
- Injected defect: LC latest shipment date earlier than the shipment date
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **21/21**

## `supporting_phytosanitary_wrong_commodity`

- Purpose: The certificate certifies rice, not the raw cotton being exported.
- Injected defect: phytosanitary commodity does not match the shipment
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **failed**
- PDF evidence checks passed: **20/20**

## `supporting_low_confidence_scan`

- Purpose: The scan is legible enough to attempt OCR but not enough to trust. Uncertainty must route to a human, never to a legal failure or pass.
- Injected defect: certificate of origin rasterized at a degraded resolution
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **manual_review**
- PDF evidence checks passed: **21/21**

## `supporting_unreadable_document`

- Purpose: The uploaded PDF yields no usable text even after OCR. The system must say it could not read it, not guess what it was.
- Injected defect: supporting document with no recoverable text
- Documents: **2 uploaded**, 0 claimed-only (name supplied without a UUID)
- Hand-declared expected status: **manual_review**
- PDF evidence checks passed: **11/11**
