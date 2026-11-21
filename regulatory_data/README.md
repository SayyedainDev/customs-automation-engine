# Regulatory data

This directory contains the cutoff-dated regulatory inputs used across CACE's
17 validated textile PCT codes. It is a scoped compliance snapshot, not a
complete or continuously updated feed of Pakistan customs law.

## Runtime inputs

The application directly uses:

- `config/textile_mvp_pct_codes.json`
- `processed/compliance/textile_mvp_executable_rules.json`
- `processed/commerce/export_policy/validated_text/`
- `raw/commerce/export_policy/base_order/export_policy_order_2022_sro_544_i_2022.pdf`
- `raw/psw/textile_product_requirements/textile_product_requirements.json`
- `raw/psw/single_declaration_export/psw_user_manual_single_declaration_exports.pdf`
- `raw/psw/user_manuals/tdap/psw_tdap_electronic_certificate_of_origin_form_issuance_traders_process_user_manual.pdf`
- `raw/tdap/export_document_guides/tdap_new_exporters_guide_part_a_export_procedures_2020.pdf`
- `raw/fbr/customs_act/customs_act_1969_updated_2025.pdf`
- `raw/fbr/customs_rules/customs_rules_2001_sro_450_i_2001_updated_2025_08_31.pdf`
- `curated/guidance/*.md` — the curated plain-English guidance pack (see below)

The middle group (PSW manuals, TDAP guide, Customs Act, Customs Rules) is
indexed for the general **Customs Assistant**
(`backend/app/services/customs_chat/`, see
`backend/docs/CUSTOMS_CHATBOT_ARCHITECTURE.md`) as well as the older
regulatory assistant. All of it is informational: deterministic textile
compliance decisions still come only from
`processed/compliance/textile_mvp_executable_rules.json`, never from the
retrieval corpus.

## Curated plain-English guidance (`curated/guidance/`)

One markdown file per Q&A entry, ingested as its own retrieval unit (source
key `customs_chat_curated_guidance`, `chunk_strategy: faq`) so it matches a
user's question closely instead of relying on statute vocabulary. Every file
carries `verification_status: needs_sme_review` in its frontmatter - this is
a first-pass draft written to close the "concept questions have no answer"
gap identified in the Customs Assistant architecture doc, not
subject-matter-expert-reviewed legal content. No entry states a specific
Pakistani number (rate, fee, deadline, threshold) - the same discipline the
live chatbot's Ring 2 (general, unevidenced) answers are held to - so a
wrong number is not a way this content can mislead a reader even before
review happens.

## Structured tariff lookup (`processed/fbr/pct/tariff_lines_fy2025_26.json`)

A full-document (all 327 pages, not just the textile chapters) machine
parse of the Pakistan Customs Tariff, produced by
`backend/scripts/parse_tariff_pdf.py` and loaded into the `tariff_lines`
table by `backend/scripts/load_tariff_lines.py` for exact/prefix HS-code
lookup - never through the RAG retriever (see
`backend/app/services/customs_chat/knowledge/tariff_parser.py`'s docstring
for the parsing approach and its known limitation: about 1.4% of rows could
not have their duty rate confidently located and are marked
`needs_review: true` rather than guessed). Cross-validated against the 17
previously human-verified rows in `config/textile_mvp_pct_codes.json` - all
17 matched on page number, description and a plausible rate - but the
remaining ~7,600 rows have only been spot-checked, not exhaustively audited
against the published tariff.

## Organization

- `config/` — supported PCT catalog and legal dates.
- `curated/` — curated plain-English content, not sourced from any single
  official document (see above).
- `processed/` — validated OCR text, structured amendment data, executable
  rules, and the parsed structured tariff (see above).
- `raw/` — canonical official-source snapshots and supporting research
  documents.
- `document_manifest.json` — initial organized inventory.
- `official_source_acquisition_manifest.json` — later acquisition and
  provenance audit.

Loose PDF downloads at the root and archival/superseded reference PDFs under
`raw/` are excluded from Git so the repository tracks only the 7 active runtime
PDFs and 7 checksum-verified EPO amendment PDFs (plus parsed JSON/curated
assets). Active export SROs can be fetched via
`backend/scripts/download_active_export_sros.py`.

## Scope boundary

The compliance result builder uses a legal cutoff date of **2026-07-22**.
Automated daily FBR/TDAP ingestion and full non-textile statutory clearance
coverage remain outside this release's runtime rule scope.
