# Regulatory data

This directory contains the cutoff-dated regulatory inputs used by the
five-PCT textile prototype. It is an engineering research snapshot, not a
complete or continuously updated statement of Pakistan customs law.

## Runtime inputs

The application directly uses:

- `config/textile_mvp_pct_codes.json`
- `processed/compliance/textile_mvp_executable_rules.json`
- `processed/commerce/export_policy/validated_text/`
- `raw/commerce/export_policy/base_order/export_policy_order_2022_sro_544_i_2022.pdf`
- `raw/psw/textile_product_requirements/textile_product_requirements.json`

## Organization

- `config/` — supported PCT catalog and legal dates.
- `processed/` — validated OCR text, structured amendment data, and executable
  rules.
- `raw/` — canonical official-source snapshots and supporting research
  documents.
- `document_manifest.json` — initial organized inventory.
- `official_source_acquisition_manifest.json` — later acquisition and
  provenance audit.

Loose PDF downloads at the root are intentionally excluded from Git. Canonical
repository copies belong under `raw/`.

## Scope warning

The compliance result builder uses a legal cutoff date of **2026-07-22**.
Automated daily FBR/TDAP ingestion, full tariff/tax computation, and complete
Pakistan Customs coverage are outside this capstone.
