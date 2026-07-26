# Missing Official Sources — Dry Run

**Generated:** 2026-07-22 13:43 PKT
**Legal cutoff:** 2026-07-22
**Scope:** Pakistan, exports only, textile-focused MVP
**Phase:** dry run completed; approved eight-file queue executed
**Download update:** 2026-07-22 14:14 PKT
**Final queue result:** **6 downloaded, 0 duplicates skipped, 2 failed with HTTP 403**

## Decision

The approved run closed six major acquisition gaps: the consolidated EFS, SRO 551(I)/2020, SRO 192(I)/2019, SRO 528(I)/2026, the HS-2022 transposition table, and the PSW Single Declaration Exports manual are now present and checksum-verified. The local collection is still **not sufficient for current textile compliance decisions** because the cumulative Export Policy Order layer remains incomplete, product-specific TIPP requirements have no selected PCT codes, dated NBP customs exchange-rate evidence is absent, and the identified legal-chain uncertainties remain unresolved.

The FBR Active Export SRO page was **not processed or downloaded again**. Its existing manifest contains all 34 rows.

## What was inspected

- A recursive pre-report snapshot contained **51 files**: 44 PDFs, 3 JSON files, and 4 Markdown files.
- Both existing manifests and all existing organization, missing-document, download, and textile-classification reports were read.
- SHA-256 was calculated for every snapshot file.
- **No two physical files had the same SHA-256.**
- The existing Active Export SRO manifest has seven logical `duplicate` rows. Each points to an already-organized canonical document, so no second physical copy exists.
- No file was renamed, moved, overwritten, or deleted.
- `config/textile_mvp_pct_codes.json` did not exist. The required empty template was created, and group H was stopped without selecting random codes.

The machine-readable acquisition decisions are in [official_source_acquisition_manifest.json](official_source_acquisition_manifest.json).

## Approved automatic-download queue — final result

Only the eight approved exact official URLs were attempted:

| # | Official source | Final status | Final path / error | SHA-256 |
| --- | --- | --- | --- | --- |
| 1 | [SRO 629(I)/2024](https://www.commerce.gov.pk/wp-content/uploads/2024/05/SRO-629-EPO.pdf) | **Failed** | HTTP 403 from exact official URL; no file saved | — |
| 2 | [SRO 1727(I)/2025](https://www.commerce.gov.pk/wp-content/uploads/2025/09/sro1727.pdf) | **Failed** | HTTP 403 from exact official URL; no file saved | — |
| 3 | [Consolidated EFS as on 17 June 2026](https://download1.fbr.gov.pk/Docs/20266171464838384UpdatedEFSRules-2021Ason17.06.2026.pdf) | **Downloaded and valid** | `raw/fbr/export_facilitation_scheme/consolidated/export_facilitation_scheme_2021_sro_957_i_2021_as_on_2026_06_17.pdf` | `045833ecf3fa14c0631aa554a84bf9def8ef6ec865464af2d9836800333cbda4` |
| 4 | [SRO 551(I)/2020](https://download1.fbr.gov.pk/SROs/20206241463635573SRO-551.pdf) | **Downloaded and valid** | `raw/fbr/export_sros/textile_duty_drawback/sro_551_i_2020_amendment_to_sro_209_i_2009_schedule_xxi_carpets.pdf` | `92e7a3471b5a951d495569eaa01fcfa85cdd4490b6a523150287d19a2d7e7080` |
| 5 | [SRO 192(I)/2019](https://download1.fbr.gov.pk/Docs/201911111111448556192-customs.pdf) | **Downloaded and valid** | `raw/fbr/export_sros/regulatory_duty/sro_192_i_2019_amendment_to_sro_645_i_2018.pdf` | `2dc80b11e0fc19ea2445dcc691a37b7c9775df7e298820c31c92bb52c7535b6a` |
| 6 | [SRO 528(I)/2026](https://download1.fbr.gov.pk/SROs/202631913336182SRO528%28I%29-2026dated19.03.2026.pdf) | **Downloaded and valid** | `raw/fbr/customs_rules/post_august_2025_amendments/sro_528_i_2026_export_facilitation_scheme_customs_rules_amendment.pdf` | `f0a55a2bd41501813a61846b3e50b23531c92f5a88027757df6b3e5e20557dd5` |
| 7 | [HS-2022 Transposition Table](https://download1.fbr.gov.pk/Docs/2022411043559455HS-2022-TRANSPOSITIONTABLE.pdf) | **Downloaded and valid** | `raw/fbr/hs_transposition/hs_2017_to_hs_2022_transposition_table.pdf` | `8e763acf7eb21ece1147b75f49befdf81c115456a1d372983c0cad3c13427e76` |
| 8 | [PSW User Manual — Single Declaration Exports](https://psw.gov.pk/media/Manuals/PSW-User-Manual-SD-Exports.pdf) | **Downloaded and valid** | `raw/psw/single_declaration_export/psw_user_manual_single_declaration_exports.pdf` | `b8d9c56b6a91c6400246f9a574b877f2f2e021948dcbce66dccade61d3dfe090` |

Every saved response had a `%PDF-` signature, MIME type `application/pdf`, passed `pdfinfo` structural validation, and matched its staged SHA-256 after placement. No downloaded checksum matched any pre-existing file, so **duplicates skipped = 0**.

Three other exact official files are technically downloadable but are **not in the minimum queue**:

- [EFS Appendix VII insurance proforma](https://download1.fbr.gov.pk/Docs/2025121612125951297InsuranceProformaunderAppendixVIIofEFsRules2021.pdf) — conditional on using an insurance guarantee.
- [SRO 750(I)/2025 — Suspension of Trade with India](https://www.commerce.gov.pk/wp-content/uploads/2025/05/SRO750.pdf) — conditional companion restriction, not a verified amendment to SRO 544(I)/2022.
- [SBP historical bank-floating average XLS](https://www.sbp.org.pk/ecodata/IBF_Arch.xls) — official analytical data, but **not** the FBR-designated customs-rate source and therefore not a legal substitute for NBP rates.

## Sources requiring manual access or user input

| Source/gap | Official entry point | Reason automatic acquisition is not ready |
| --- | --- | --- |
| SRO 629(I)/2024 | [Exact official PDF](https://www.commerce.gov.pk/wp-content/uploads/2024/05/SRO-629-EPO.pdf) | Approved automatic attempts returned HTTP 403; manual browser retrieval from the same official URL is required. |
| SRO 1727(I)/2025 | [Exact official PDF](https://www.commerce.gov.pk/wp-content/uploads/2025/09/sro1727.pdf) | Same. |
| SRO 561(I)/2023 | [Ministry of Commerce SRO index](https://www.commerce.gov.pk/sros/) | Official row verified; exact attachment URL was not exposed. |
| SRO 1087(I)/2023 | [Ministry of Commerce SRO index](https://www.commerce.gov.pk/sros/) | Same. |
| SRO 1021(I)/2024 | [Ministry of Commerce SRO index](https://www.commerce.gov.pk/sros/) | Same; do not confuse with IPO SRO 1022(I)/2024. |
| SRO 705(I)/2025 | [Ministry of Commerce SRO index](https://www.commerce.gov.pk/sros/) | Official row verified; exact attachment URL unresolved. |
| SRO 1902(I)/2025 | [Ministry of Commerce SRO index](https://www.commerce.gov.pk/sros/) | Same. |
| SRO 2486(I)/2025 | [Ministry of Commerce SRO index](https://www.commerce.gov.pk/sros/) | Same. |
| SRO 382(I)/2024 and SRO 433(I)/2024 | [Ministry of Commerce SRO index](https://www.commerce.gov.pk/sros/) | Official export instruments exist, but their titles alone do not prove a formal SRO 544 relationship; both concern non-textile products. |
| Second Kinnow index row dated 2023-10-30 | [Ministry of Commerce SRO index](https://www.commerce.gov.pk/sros/) | It may be a correction/re-upload of the 2023-10-24 circular; attachment comparison is required. |
| SRO 1139(I)/2026 local PDF | Local `20267211373126387SRO1139OF2026.pdf` | Existing metadata does not establish its official title, authority, source URL, date, subject, or parent. No relationship is inferred from the filename. |
| Textile product requirements | [Tradeverse/TIPP](https://www.tipp.gov.pk/) | `config/textile_mvp_pct_codes.json` now exists but contains an empty `pct_codes` array. The user must supply the intended PCT codes first. |
| NBP historical customs rates | [NBP Rate Sheet archive](https://www.nbp.com.pk/RateSheet/index.aspx?view=ExternalLink) | The public source is a daily PDF archive, not one consolidated machine-readable file. The required shipment/date range must be defined before acquisition. |
| Older generic HS crosswalks | [FBR historical tariffs](https://www.fbr.gov.pk/categ/customs-tariff/51149/70853/131190) | No standalone official 2007→2012 or 2012→2017 table was found on the current FBR site. Historical tariff PDFs are not crosswalks. |

## A. Export Policy Order 2022 amendment audit

The authoritative [Ministry of Commerce SRO index](https://www.commerce.gov.pk/sros/) was checked through its latest entries before the cutoff. It shows no Export Policy Order 2022 amendment in 2026 through 22 July 2026.

### High-confidence parent-amendment set

| Instrument | Ministry date | Local status | Official download/source | Textile-MVP decision |
| --- | --- | --- | --- | --- |
| SRO 544(I)/2022 — base EPO | 2022-04-22 | Already present | [Official PDF](https://www.commerce.gov.pk/wp-content/uploads/2022/04/EPO-2022-SRO-544-2022-dt.22.4.22.pdf) | Required |
| SRO 561(I)/2023 | 2023-05-15 | Missing; manual attachment access | [Official index](https://www.commerce.gov.pk/sros/) | Required for cumulative EPO reconstruction |
| SRO 1087(I)/2023 | 2023-08-18 | Missing; manual attachment access | [Official index](https://www.commerce.gov.pk/sros/) | Required |
| **SRO 629(I)/2024** | 2024-04-30 | Missing; automatic attempt failed HTTP 403; manual access required | [Official PDF](https://www.commerce.gov.pk/wp-content/uploads/2024/05/SRO-629-EPO.pdf) | Required; additional item beyond the supplied known list |
| SRO 1021(I)/2024 | 2024-07-11 | Missing; manual attachment access | [Official index](https://www.commerce.gov.pk/sros/) | Required |
| SRO 705(I)/2025 | 2025-04-18 | Missing; manual attachment access | [Official index](https://www.commerce.gov.pk/sros/) | Required |
| SRO 1727(I)/2025 | 2025-09-08 | Missing; automatic attempt failed HTTP 403; manual access required | [Official PDF](https://www.commerce.gov.pk/wp-content/uploads/2025/09/sro1727.pdf) | Required |
| SRO 1902(I)/2025 | 2025-10-02 | Missing; manual attachment access | [Official index](https://www.commerce.gov.pk/sros/) | Required |
| SRO 2486(I)/2025 | 2025-12-23 | Missing; manual attachment access | [Official index](https://www.commerce.gov.pk/sros/) | Required |

Effective dates and precise operative subjects remain `null` where the Ministry index does not expose them. They must be established only after approved acquisition; none was invented.

### Other EPO-scope candidates and date relaxations

| Instrument | Date | Classification | MVP decision |
| --- | --- | --- | --- |
| SRO 382(I)/2024 — ban on bananas and onions | 2024-03-12 | Possible EPO-governed prohibition; formal SRO 544 link unverified | Not required for textiles; retain as uncertain complete-archive candidate |
| SRO 433(I)/2024 — wheat/wheat-flour permission under EFS | 2024-03-25 | Possible EPO relaxation; formal SRO 544 link unverified | Not required for textiles |
| Mango export-date extension | 2022-05-14 | Temporary/historical; attachment unresolved | Not required |
| [Kinnow export-date change](https://www.commerce.gov.pk/wp-content/uploads/2023/10/Change-in-date-of-export-of-kinnow.pdf) | 2023-10-24 | Temporary; linked by official metadata to EPO Schedule II | Not required |
| Second Kinnow row | 2023-10-30 | Possible correction/duplicate; manual review | Not required |
| [Mango export-date extension](https://www.commerce.gov.pk/wp-content/uploads/2025/05/Extension-of-Date-of-Export-of-Mangoes.pdf) | 2025-05-06 | Temporary/historical | Not required |
| [SRO 750(I)/2025 — suspension of trade with India](https://www.commerce.gov.pk/wp-content/uploads/2025/05/SRO750.pdf) | 2025-05-04 | Standalone companion restriction, not a verified SRO 544 amendment | Conditional on route/origin/destination |

Boundary findings:

- SRO 1397(I)/2023 amends Afghanistan-transit SRO 151(I)/2004, not EPO 2022.
- SRO 1259(I)/2023 concerns crude oil and petroleum import/re-export.
- SRO 642(I)/2023 and SRO 1989(I)/2025 govern the B2B barter mechanism; they do not amend SRO 544.
- SRO 760 material concerns precious metals, not EPO 2022.
- Condonation committee notices are administrative mechanisms; the index metadata does not establish amendments to SRO 544.

## B. Current Export Facilitation Scheme

FBR’s [Export Facilitation Schemes page](https://www.fbr.gov.pk/export-facilitaion-schemes/132200) exposes the official consolidation **as on 17 June 2026**.

| Source | Status | Decision |
| --- | --- | --- |
| SRO 957(I)/2021 original notification | Present, SHA `7c63ea02fd1e9122514742ed9f7ab8d190b239a14be28e768685eeebaf61d059` | Keep; it is not a substitute for the current consolidation |
| Consolidated EFS as on 2026-06-17 | Downloaded and validated, SHA `045833ecf3fa14c0631aa554a84bf9def8ef6ec865464af2d9836800333cbda4` | Required source now present |
| Appendix VII insurance proforma | Missing; direct PDF ready | Conditional |
| EFS FAQ | Present, SHA `09797a8442759556ff1aa30ac50bea187d8ca1185c9b7ae407d06d5435d27245` | Guidance only |

The consolidated PDF contains Appendices I–VII. No other separate required form was exposed besides the insurance proforma. No later EFS amendment was found through the cutoff.

## C. Textile duty-drawback chain

The chronological source set is:

```text
SRO 209(I)/2009 base
  ├─ SRO 579(I)/2012 — PCT-code amendment                 present
  ├─ SRO 754(I)/2014 — Schedule XXXVIII amendment         present
  ├─ SRO 979(I)/2015 — schedule/rate amendment             present
  ├─ SRO 576(I)/2017 — PCT-code amendment                 present
  └─ SRO 551(I)/2020 — Schedule XXI carpet-entry changes  downloaded and verified
```

These are chronological direct amendments to the base; the arrows do not imply that each later SRO amends the immediately preceding SRO.

Important corrections and uncertainties:

- SRO 979(I)/2015 **cannot be applied independently**; it modifies schedules in SRO 209(I)/2009. The base is now present locally.
- SRO 551(I)/2020 changes specified **Schedule XXI** hand-knotted wool carpet/rug/runner entries. It does **not** insert Schedule XXXVII. The old missing-document report’s statement to the contrary is incorrect and must not be used.
- SRO 754(I)/2014 inserts Schedule XXXVIII “after Schedule XXXVII,” while the identity of the official instrument that first added Schedule XXXVII has not been verified. This remains an uncertain relationship requiring an official archival search; no SRO number is invented.
- No later official amendment, substitution, rescission, corrigendum, or updated schedule expressly affecting SRO 209(I)/2009 was discovered in the indexed official sources through the cutoff. This is a search finding, not a guarantee that unindexed archival material does not exist.
- SRO 579(I)/2012 and SRO 576(I)/2017 supply notification-specific code updates. The newly downloaded generic HS-2017→HS-2022 table provides the later crosswalk.

## D. SRO 645(I)/2018 current-effect chain

The verified relationship is:

```text
SRO 645(I)/2018 — base regulatory-duty notification                 present
  ├─ SRO 1011(I)/2018 — direct amendment, 15 Aug 2018               present but unorganized
  └─ SRO 192(I)/2019 — later direct amendment, 11 Feb 2019          downloaded and verified
```

The two later instruments are direct amendments to SRO 645; SRO 192 is chronologically later, but it must not be described as an amendment to SRO 1011 unless its text says so.

FBR still lists SRO 645(I)/2018 on its [Active Export SRO page](https://www.fbr.gov.pk/ActiveSrosExport). That is evidence of FBR’s active/operative listing, but the local base reportedly carries an original 30 June 2019 end date. The exact effect of SROs 1011 and 192 on products, rates, and duration must be established after approved acquisition/document analysis. No rescission or later amendment was found in the official index, but current legal effect remains **uncertain**, not conclusively proven.

## E. Customs Rules

The local file is the official FBR compilation titled **“Customs Rules 2001 dated 18.06.2001 (Updated 31st August, 2025)”**:

- Local path: `raw/fbr/customs_rules/customs_rules_2001_sro_450_i_2001_updated_2025_08_31.pdf`
- Local SHA-256: `a662e5c64b26151a941022dedec3333b2572bc823fee3ab99dcfe37ecb62832c`
- [Official FBR listing](https://www.fbr.gov.pk/categ/customs-tariff/51149/70853/131188)
- [Official direct PDF](https://download1.fbr.gov.pk/SROs/202510311510162884CustomsRules2001.pdf)

FBR publishes no checksum, so byte-for-byte remote equality cannot be independently re-proved without downloading; the verified title/update metadata and existing 634-page local audit match. No duplicate download is proposed.

Only **SRO 528(I)/2026** was identified as an operative post-31-August-2025 amendment directly within the requested textile-export scopes. It finalizes EFS/Chapter XL changes and is now downloaded and checksum-verified.

- SRO 520(I)/2026 and SRO 211(I)/2026 are drafts; do not use them as operative law.
- The reviewed post-cutoff instruments concerning transit, IPR, auction, foreign post, import IGM, marine bunkering, overstayed import cargo, customs-port declarations, and international transshipment were excluded from the textile-export MVP.
- Local SRO 1130(I)/2026 is the final International Transshipment Rules amendment and is not required for this MVP.
- Local SRO 1139(I)/2026 remains unclassified because its subject was not established without parsing the PDF.

## F. Customs tariff and code mapping

**Official consolidated FY 2026–27 tariff not found as of 2026-07-22.**

The current [FBR Customs Tariff page](https://www.fbr.gov.pk/categ/customs-tariff/51149/70853/131188) still exposes FY 2025–26. The local FY 2025–26 tariff is present, and the enacted Finance Act 2026 is already local as the temporary update layer. The Finance Bill is a superseded proposal and must never be treated as final law.

| Source | Status | Decision |
| --- | --- | --- |
| Pakistan Customs Tariff FY 2025–26 | Present, SHA `7fd91a8b358f50d1ea83301716cabcc2029ff5a1ad6c465e3c6265e97d979fea` | Baseline only after 2026-07-01 |
| [Finance Act 2026](https://download1.fbr.gov.pk/Docs/20266291261044366FinanceAct2026.pdf) | Equivalent enacted Act already local, SHA `49ad283594c4d1cb015fe224d3a5a126b0742bb1e416cf11089aef01df5cb124` | Current enacted update layer; no duplicate download |
| Finance Bill 2026 | Local, superseded/draft | Exclude from operative rules |
| [HS-2022 Transposition Table](https://download1.fbr.gov.pk/Docs/2022411043559455HS-2022-TRANSPOSITIONTABLE.pdf) | Downloaded and validated, SHA `8e763acf7eb21ece1147b75f49befdf81c115456a1d372983c0cad3c13427e76` | Required source now present |
| Generic 2007→2012 and 2012→2017 tables | Not found on current official FBR site | Manual archival access; use notification-specific SRO 579/576 where applicable and do not invent other mappings |

## G. PSW Single Declaration — Export

The current official [Single Declaration — Export overview](https://psw.gov.pk/public/single-declaration-export) and its [downloadable user manual](https://psw.gov.pk/media/Manuals/PSW-User-Manual-SD-Exports.pdf) were found. The manual is now downloaded and validated at `raw/psw/single_declaration_export/psw_user_manual_single_declaration_exports.pdf`, SHA `b8d9c56b6a91c6400246f9a574b877f2f2e021948dcbce66dccade61d3dfe090`. No separate public downloadable export schema was found during the dry run.

## H. PSW/Tradeverse product requirements

The requested configuration did not exist, so this template was created:

```json
{
  "pct_codes": []
}
```

Path: `config/textile_mvp_pct_codes.json`

Because the list is empty, no PCT code was selected, no TIPP/Tradeverse product record was collected, and no destination JSON was created. Add the actual MVP codes before this group can continue.

## I. Exchange-rate data

FBR’s [Exchange Rates page](https://www.fbr.gov.pk/exchange-rates/51149/131194) identifies National Bank of Pakistan rates as the customs source. FBR also describes daily NBP-to-WeBOC rate delivery; an authenticated WeBOC portal must not be scraped or bypassed.

The lawful public source is the [NBP Rate Sheet archive](https://www.nbp.com.pk/RateSheet/index.aspx?view=ExternalLink):

- Format: one public PDF per available business day.
- Observed coverage: at least 5 May 2015 through 22 July 2026.
- Requested currencies: USD, EUR, GBP, CNY, AED, and SAR are represented in the published rate-sheet format.
- Example cutoff file: [NBP Rate Sheet — 22 July 2026](https://www.nbp.com.pk/RateSheetFiles/NBP-RateSheet-22-07-2026.pdf).
- No consolidated public machine-readable historical customs-rate dataset was found.

Recommended lawful method: define the MVP’s shipment/declaration date range, then acquire and checksum the official NBP PDF for every applicable business date. Where a non-business day needs a rate, encode the legally applicable carry-forward rule only after it is verified from official Customs/NBP procedure. For future automation, obtain authorized NBP/WeBOC access rather than scraping a protected portal. Until then, missing dates must be fetched from the public archive or entered manually with source URL and checksum.

SBP’s machine-readable [bank-floating average historical XLS](https://www.sbp.org.pk/ecodata/IBF_Arch.xls) is official and downloadable, but it is analytical data rather than the FBR-designated customs rate. It should not drive legal currency conversion.

## Broken official links and access failures

- No newly identified **required** direct official PDF link was found to return 404 in this dry run.
- Approved downloads for SRO 629(I)/2024 and SRO 1727(I)/2025 each returned HTTP 403 from the exact official Ministry URLs, including retries with ordinary browser headers and the Ministry SRO page as referrer. No response body was saved as a PDF, and no alternate source was used.
- The previously processed Active Export SRO manifest records the old ATA Carnet SRO 1157/2007 link as 404. It is historical and outside the textile MVP.
- Several Commerce rows expose official metadata while their exact attachment URLs are hidden/unresolved by the current site. They are classified `requires_manual_access`, not `unavailable_officially` and not silently replaced with unofficial copies.

## Duplicate audit

- Physical duplicate checksum groups in the recursive snapshot: **0**.
- Logical duplicate rows in the existing 34-row Active Export SRO manifest: **7** — SROs 2335(I)/2025, 957(I)/2021, 645(I)/2018, 646(I)/2018, 979(I)/2015, 805(I)/2009, and 209(I)/2009.
- Their canonical files already exist; the complete Active Export page must not be downloaded again.

## Historical, temporary, firm-specific, and draft exclusions

These findings are retained as raw/history but must not enter a current general textile rule set:

- Drafts: SRO 211(I)/2026, SRO 520(I)/2026, local SRO 784(I)/2021, local SRO 194(I)/2019, and the superseded Finance Bill 2026.
- Temporary historical rules: mango/Kinnow date changes and local SRO 323(I)/2010 (60-day yarn regulatory duty).
- Firm-specific historical rule: local SRO 755(I)/2014 for Sapphire Finishing Mills and a defined historical export period.
- Product-unrelated active-export items remain preserved in the existing raw collection but excluded from textile ingestion under the existing classification report.

## Uncertain relationships requiring later legal review

1. Whether SRO 382(I)/2024 and SRO 433(I)/2024 formally amend SRO 544(I)/2022.
2. Whether the two 2023 Kinnow index entries are separate documents, a correction, or a duplicate upload.
3. Which verified official instrument first inserted Schedule XXXVII into SRO 209(I)/2009.
4. The exact current effect of SRO 645(I)/2018 after SROs 1011(I)/2018 and 192(I)/2019, including the apparent date conflict with FBR’s active listing.
5. The identity and relevance of local SRO 1139(I)/2026.
6. Any unindexed later SRO 209 or SRO 645 chain item; none was found, but archival completeness is not asserted.

## Minimum blockers before current compliance logic is enabled

1. Manually retrieve SRO 629(I)/2024 and SRO 1727(I)/2025 from their exact official Ministry URLs because automated requests received HTTP 403.
2. Manually retrieve the six other high-confidence Commerce amendments whose official index attachments could not be resolved: SROs 561, 1087, 1021, 705, 1902, and 2486.
3. Supply the actual textile PCT-code list so TIPP product requirements can be collected.
4. Define the exchange-rate date window and acquire the corresponding official NBP daily sheets.
5. Resolve the Schedule XXXVII and SRO 645 current-effect uncertainties before executing those rules.
6. Continue monitoring FBR for an official consolidated FY 2026–27 tariff; until published, use only the FY 2025–26 baseline plus the enacted Finance Act 2026 update layer.

Infrastructure work can continue, but prohibited/restricted-export, EPO, current EFS, drawback, regulatory-duty, LPCO, current-PCT, and legal currency-conversion decisions should remain blocked until their applicable items above are closed.

## Recursive checksum snapshot

This is the 51-file snapshot taken after creating the empty PCT config and before creating this report and the acquisition manifest. The two new output files are therefore intentionally not self-hashed here.

```text
1590b2f660dbd0a63646eb58eebf3a5a428bd0c533855db8817388f459ad08b7  20188271581428749SRO1011of2018.pdf
169716e949d87c392c7c107b334871898dade13bf5a726606451eef9629152c7  20267171772124483SRO1130(I)2026.pdf
5236bc22f663d5e5257c8b8e0a859e4815078c588e1fc79fa7318780357557ba  20267211373126387SRO1139OF2026.pdf
1ab847783025c5769ca6a762adde89f572049f733633e9c3c425bc1ee99ac43b  config/textile_mvp_pct_codes.json
b4b3913513d6802a920789fde8d54a26ba4f57f4750928f62ba28ecfbf4083b4  document_manifest.json
93f720c04028cd27c4a7570e4fe5d2280a5305e31f346a15e39d9d1924bc35b7  document_organization_plan.md
a23548bb2c5f2a1c9fcc35026615a1cff5b03bed8adad0275f3f19ad0f76c0a2  missing_documents_report.md
fc4e108c5e9aa0a46d6093828ee1b9d596fdeb2c7f52bb2e6a6c5a03860364c3  raw/commerce/export_policy/base_order/export_policy_order_2022_sro_544_i_2022.pdf
9e22264a08ff3b5817329433f88fb6559a9bdb289a77614e586dcc39a3d51fd8  raw/fbr/customs_act/customs_act_1969_updated_2025.pdf
962b7df4f29991cee4a8779d2bb69abba7e75b5bc4fc5f27d877110281935051  raw/fbr/customs_rules/customs_rules_2001_sro_450_i_2001.pdf
a662e5c64b26151a941022dedec3333b2572bc823fee3ab99dcfe37ecb62832c  raw/fbr/customs_rules/customs_rules_2001_sro_450_i_2001_updated_2025_08_31.pdf
09797a8442759556ff1aa30ac50bea187d8ca1185c9b7ae407d06d5435d27245  raw/fbr/export_facilitation_scheme/guidance/export_facilitation_scheme_faqs_2023.pdf
7c63ea02fd1e9122514742ed9f7ab8d190b239a14be28e768685eeebaf61d059  raw/fbr/export_facilitation_scheme/sro_957_i_2021_export_facilitation_scheme_2021.pdf
a919054de61dd32c1cf1aba0ba88f2944ad32173befdf9df1421e46ba1714d5f  raw/fbr/export_sros/all_active_export_sros/sro_1065_i_2005_temporary_importation_for_exporters.pdf
a1c54e76fa36ee1222e3a7969c3267fbeba21c32d66cfdf7a3ef2098e9dcf3a0  raw/fbr/export_sros/all_active_export_sros/sro_1185_i_2007_wheat_products_export_regulatory_duty.pdf
4597e75cdcdc4ff890583636b60a2e875800c2107279c124f2a007d9df8a2900  raw/fbr/export_sros/all_active_export_sros/sro_1186_i_2007_rescission_of_sro_474_i_2006.pdf
ba8d9205700318a566dceb3abfcc39ed7fa555c972e576f1e84aa35552ae7ba8  raw/fbr/export_sros/all_active_export_sros/sro_1301_i_2020_khalachi_customs_station_rebatable_exports.pdf
bd70dc1b54f516b7943eab96d0ad6e2a6baf8b6c3bad06c22795da8e6bbaba9c  raw/fbr/export_sros/all_active_export_sros/sro_194_i_2019_draft_export_oriented_units_rules_2008_amendment.pdf
d581d232f9fffe2ca323fcd912364efa0936b26080ceb11a1fabf98cafe54e19  raw/fbr/export_sros/all_active_export_sros/sro_210_i_2009_leather_sports_goods_duty_drawback_rates.pdf
74eb55c318528f66dae965ff8c2115df4d388527762817fc44b953a7c3d86d03  raw/fbr/export_sros/all_active_export_sros/sro_211_i_2009_engineering_metal_duty_drawback_rates.pdf
dadb4f401d7c627962ccdce7f143fafc0218fa0464d9bfc4b5daec82546880b5  raw/fbr/export_sros/all_active_export_sros/sro_212_i_2009_miscellaneous_products_duty_drawback_rates.pdf
dbde201749181c32d0969eae11e26ec24282ed16b3516b31af47eacd776f2743  raw/fbr/export_sros/all_active_export_sros/sro_323_i_2010_yarn_export_regulatory_duty.pdf
f184438b44f1a07840f4d5163c69d94c5d78910845039b8edc0cf4095d2db4ad  raw/fbr/export_sros/all_active_export_sros/sro_326_i_2008_export_oriented_unit_duty_tax_exemption.pdf
5c7b858d7694c3865693a7cdff12633c6c92b647cd3214490783bae192d15639  raw/fbr/export_sros/all_active_export_sros/sro_327_i_2008_export_oriented_units_sme_rules_2008.pdf
6ae8864953ee837a98e8bdb61eb97bf5b5168f1b7c8308787470960299770cf5  raw/fbr/export_sros/all_active_export_sros/sro_427_i_2022_bazarcha_border_terminal_customs_station.pdf
745db19109dfc7d7651d0fc78f19cd5b857d19e5d7e4becc79467743768f3b7f  raw/fbr/export_sros/all_active_export_sros/sro_482_i_2007_ferrous_nonferrous_scrap_export_regulatory_duty.pdf
ce93dcc34e1fd48d92a4c8ccbdaa0412d9623a2e5a799b901491042d9bb51274  raw/fbr/export_sros/all_active_export_sros/sro_492_i_2006_pulses_export_regulatory_duty.pdf
92811027d8f90d89b33b1c8a8b8d2e9530db4ceadbca65704d4df307678596f0  raw/fbr/export_sros/all_active_export_sros/sro_755_i_2014_customs_duty_repayment.pdf
b250cf538a0e3b54b2889becc16ffad558d0bb0dd83fc571f759a2fae29ea721  raw/fbr/export_sros/all_active_export_sros/sro_784_i_2021_draft_export_oriented_units_sme_rules_amendments.pdf
bea9f47c6a1d17e40769b3734ad38cca454a6bd8ee4cde21416bf3104cd8d986  raw/fbr/export_sros/all_active_export_sros/sro_888_i_2009_export_oriented_units_sme_rules_amendment.pdf
cdae61f100d82cca902cd91e9b0944971d160a627bc7302b59afd3f68abfa87a  raw/fbr/export_sros/all_active_export_sros/sro_988_i_2021_amendment_to_sro_212_i_2009.pdf
31f6cb8da4c9276e93a457b3d99bd2ad7da6c5f61a22ce44a404c08ceb195266  raw/fbr/export_sros/all_active_export_sros/sro_unknown_i_2010_copper_aluminium_export_regulatory_duty.pdf
4e084f9e483b37a6768819e83ebef8cc9f85e4e66be9dd4d23032d37277a0b3d  raw/fbr/export_sros/export_sro_download_manifest.json
65a104817870d15f3a27d8fa2495241b3d1027793787057498c3f7252575f656  raw/fbr/export_sros/export_sro_download_report.md
404294414e899329cdf294c292f68370b131130341c9ec548154cb05056f16ae  raw/fbr/export_sros/not_used_in_textile_mvp/sro_646_i_2018_ioco_composition_functions_jurisdiction_and_powers.pdf
b1892fc3e86d7515d67c30b3f89107f63fab6bcc243432dea4ef4388d137e8ec  raw/fbr/export_sros/not_used_in_textile_mvp/sro_805_i_2009_wheat_products_export_regulatory_duty_rescission.pdf
9dda92f232e50535b513a75e0b37509d8aee2f19a1fdc2d4bbf177c579bb70b8  raw/fbr/export_sros/regulatory_duty/sro_645_i_2018_export_regulatory_duty.pdf
92589fd4d99b7ec82b9334a4858c1596bb555b216ea80aaea4685283a04592e8  raw/fbr/export_sros/sro_2335_i_2025_export_development_surcharge_exemption.pdf
cac6f7611b43cbc8e7aa7e94139870d7b21f5510af78715c0f3bc918f402dfb6  raw/fbr/export_sros/textile_duty_drawback/sro_209_i_2009_textile_duty_drawback_rates.pdf
d6b1279c8ef60c7f3e8c997fafa2ffad93c9e2dfbb992b32c0a64f31dc44c1be  raw/fbr/export_sros/textile_duty_drawback/sro_576_i_2017_textile_duty_drawback_pct_code_amendments.pdf
cb6f1435bbd8e4329055d742660725a4212a3028857a53cb21defc4526d49c44  raw/fbr/export_sros/textile_duty_drawback/sro_579_i_2012_textile_duty_drawback_pct_code_amendments.pdf
b99939264329aadb1c413bdf2f6b25d38d19b5890b7be4b496515b22f9fef388  raw/fbr/export_sros/textile_duty_drawback/sro_754_i_2014_textile_duty_drawback_schedule_xxxviii.pdf
10659a7fd47fd52d3e883397071a962b1771caea99559e65ad09d01ad321a293  raw/fbr/export_sros/textile_duty_drawback/sro_979_i_2015_revised_duty_drawback_rates.pdf
ce55a5d541874c05b83fb4903a2be0a9b012aa04ad9df510f299d24f8c81fbbc  raw/fbr/export_sros/textile_mvp_sro_classification.md
50f3f0f20abe808dd3071024c1867fd7ac5c5cf27a6ef097b872fdcd339f88b4  raw/fbr/pct/historical/pakistan_customs_tariff_fy_2017_18_chapters_01_99.pdf
7fd91a8b358f50d1ea83301716cabcc2029ff5a1ad6c465e3c6265e97d979fea  raw/fbr/pct/pakistan_customs_tariff_fy_2025_26.pdf
184c0f3c3058c44360e928f2b7eb715e6960d6d6e03a0c49375c966501dd2aa5  raw/fbr/sales_tax_sros/not_used_in_textile_mvp/sro_551_i_2008_sales_tax_exemptions_for_specified_goods.pdf
8a852c31284fc8a8c9711ff0061a5e324fa1bc2fd9c4327af5e5af2e77d4fecc  raw/ministry_of_finance/finance_bills/superseded/finance_bill_2026.pdf
49ad283594c4d1cb015fe224d3a5a126b0742bb1e416cf11089aef01df5cb124  raw/national_assembly/finance_acts/finance_act_2026_act_xliii_of_2026.pdf
4a07710113506c1e0b926ce1fb9897db837ecb88197b7f2a2b6dbe56e1bd3785  raw/psw/user_manuals/tdap/psw_tdap_electronic_certificate_of_origin_form_issuance_traders_process_user_manual.pdf
da6825b95ee29540575c099025e68f331cccedebfda45a25bfe2fd325bb329c5  raw/tdap/export_document_guides/tdap_new_exporters_guide_part_a_export_procedures_2020.pdf
```

## Approved-run completion state

The dry run was approved for exactly eight sources. Six valid PDFs were downloaded to previously absent destinations and checksum-verified; two Commerce sources failed with HTTP 403 and now require manual access. No existing file was overwritten or deleted, no duplicate was added, and no OCR, embeddings, database insertion, PDF text extraction, or rule creation was performed. See `official_source_download_completion_report.md` for the final execution record.
