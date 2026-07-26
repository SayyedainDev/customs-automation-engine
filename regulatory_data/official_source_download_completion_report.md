# Official Source Download Completion Report

**Execution date:** 2026-07-22
**Completed at:** 14:14 PKT
**Approved scope:** exactly eight official PDFs listed in `missing_official_sources_dry_run.md`
**OCR, embeddings, database insertion, text extraction, and rule creation:** not performed

## Summary

| Result | Count |
| --- | ---: |
| Approved URLs attempted | 8 |
| Downloaded successfully | 6 |
| Duplicates skipped | 0 |
| Failed downloads | 2 |
| Non-PDF responses saved | 0 |
| Existing files overwritten | 0 |
| Files deleted | 0 |

The six successful files total **22,933,923 bytes**. Each was downloaded to temporary staging, validated, compared against every existing `regulatory_data/` checksum, and then copied without overwrite to its proposed canonical path. The destination checksum matched the staged checksum in every case.

## Downloaded successfully

| # | Document and exact official source | Final relative path | Bytes | Pages | SHA-256 |
| --- | --- | --- | ---: | ---: | --- |
| 1 | [Export Facilitation Scheme 2021 — SRO 957(I)/2021 as on 17 June 2026](https://download1.fbr.gov.pk/Docs/20266171464838384UpdatedEFSRules-2021Ason17.06.2026.pdf) | `raw/fbr/export_facilitation_scheme/consolidated/export_facilitation_scheme_2021_sro_957_i_2021_as_on_2026_06_17.pdf` | 688,961 | 45 | `045833ecf3fa14c0631aa554a84bf9def8ef6ec865464af2d9836800333cbda4` |
| 2 | [SRO 551(I)/2020](https://download1.fbr.gov.pk/SROs/20206241463635573SRO-551.pdf) | `raw/fbr/export_sros/textile_duty_drawback/sro_551_i_2020_amendment_to_sro_209_i_2009_schedule_xxi_carpets.pdf` | 166,474 | 1 | `92e7a3471b5a951d495569eaa01fcfa85cdd4490b6a523150287d19a2d7e7080` |
| 3 | [SRO 192(I)/2019](https://download1.fbr.gov.pk/Docs/201911111111448556192-customs.pdf) | `raw/fbr/export_sros/regulatory_duty/sro_192_i_2019_amendment_to_sro_645_i_2018.pdf` | 8,109,404 | 1 | `2dc80b11e0fc19ea2445dcc691a37b7c9775df7e298820c31c92bb52c7535b6a` |
| 4 | [SRO 528(I)/2026](https://download1.fbr.gov.pk/SROs/202631913336182SRO528%28I%29-2026dated19.03.2026.pdf) | `raw/fbr/customs_rules/post_august_2025_amendments/sro_528_i_2026_export_facilitation_scheme_customs_rules_amendment.pdf` | 436,834 | 2 | `f0a55a2bd41501813a61846b3e50b23531c92f5a88027757df6b3e5e20557dd5` |
| 5 | [HS-2022 Transposition Table](https://download1.fbr.gov.pk/Docs/2022411043559455HS-2022-TRANSPOSITIONTABLE.pdf) | `raw/fbr/hs_transposition/hs_2017_to_hs_2022_transposition_table.pdf` | 10,432,437 | 492 | `8e763acf7eb21ece1147b75f49befdf81c115456a1d372983c0cad3c13427e76` |
| 6 | [PSW User Manual — Single Declaration Exports](https://psw.gov.pk/media/Manuals/PSW-User-Manual-SD-Exports.pdf) | `raw/psw/single_declaration_export/psw_user_manual_single_declaration_exports.pdf` | 3,099,813 | 34 | `b8d9c56b6a91c6400246f9a574b877f2f2e021948dcbce66dccade61d3dfe090` |

## Failed downloads

| # | Document and exact official source | Proposed relative path | Result | File saved |
| --- | --- | --- | --- | --- |
| 1 | [SRO 629(I)/2024](https://www.commerce.gov.pk/wp-content/uploads/2024/05/SRO-629-EPO.pdf) | `raw/commerce/export_policy/amendments/sro_629_i_2024_amendment_to_export_policy_order_2022.pdf` | HTTP 403 Forbidden. The exact URL was retried with ordinary browser headers and the official Commerce SRO page as referrer; it remained blocked. | No |
| 2 | [SRO 1727(I)/2025](https://www.commerce.gov.pk/wp-content/uploads/2025/09/sro1727.pdf) | `raw/commerce/export_policy/amendments/sro_1727_i_2025_amendment_to_export_policy_order_2022.pdf` | HTTP 403 Forbidden under the same retry policy. | No |

No alternate URL, mirror, unofficial copy, rendered webpage, or renamed error response was used. Both records are now marked `requires_manual_access` in the acquisition manifest. Their proposed destinations remain absent.

## Duplicate checks

- Before placement, each successful SHA-256 was compared with all files already under `regulatory_data/`.
- None matched an existing checksum.
- The six final destination checksums are mutually unique.
- Therefore, **duplicates skipped: 0** and **duplicate files added: 0**.

## PDF validation

Every successful file passed all of these checks:

1. The first five bytes were `%PDF-`.
2. The detected MIME type was `application/pdf`.
3. `pdfinfo` completed successfully, confirming a readable PDF structure.
4. The final destination SHA-256 exactly matched the staged-download SHA-256.
5. The destination did not exist before placement and was written using no-clobber semantics.

## Updated records

- `official_source_acquisition_manifest.json` now records six `downloaded` sources with checksums and validation results, plus two `requires_manual_access` sources with their HTTP 403 errors.
- `missing_official_sources_dry_run.md` now contains the approved queue’s final status and revised remaining blockers.
- No existing organized PDF was modified, moved, overwritten, or deleted.
