# Textile Export Compliance MVP — Readiness and Minimum Missing Documents

**Assessment date:** 2026-07-22
**Verdict:** The collection is enough to continue building ingestion, storage, search, RAG, and rule-engine infrastructure. It is **not enough for current, production-grade compliance decisions**.

## Coverage after this upload

| MVP capability | Readiness | Reason |
| --- | --- | --- |
| PCT-code existence and description checks | **Partial** | FY 2025–26 tariff is searchable, but current FY 2026–27 changes must be applied from Finance Act 2026/current tariff material |
| Prohibited/restricted export checks | **Blocked** | Export Policy Order 2022 base is present; its amendment set is missing |
| Export Policy conditions | **Blocked** | Same missing cumulative amendment layer |
| Licence, permit, certificate and approval checks | **Blocked** | A Certificate-of-Origin workflow manual is present, but PCT-specific PSW/TIPP LPCO requirements are not |
| Export SRO applicability | **Partial** | Several relevant SROs are present, but effective-date and amendment chains are incomplete |
| Export Development Surcharge | **Covered for the present source set** | S.R.O. 2335(I)/2025 exemption is present |
| Export regulatory duty | **Blocked** | S.R.O. 645(I)/2018 is image-only and its later amendments/current effect are not assembled |
| Textile duty drawback | **Blocked** | Base 209 and several amendments are now present, but the chain, transposition, OCR and current-effect validation are incomplete |
| Customs legal/procedural RAG | **Good foundation, not current-complete** | Act, Finance Act and updated Rules are present; relevant post-cutoff amendments still need an audit |
| Export Facilitation Scheme | **Blocked for current rules** | Original S.R.O. 957(I)/2021 and FAQ are present; consolidated EFS as on 17 Jun 2026 is missing |
| Currency conversion | **Blocked** | No authoritative dated rate feed/history is stored locally |
| Required export documents | **Partial** | TDAP and E-CO guidance are present, but current Single Declaration and product-specific requirements are missing |

## Minimum documents/data still required

These are the minimum gaps for the current textile MVP, not a future-completeness wish list.

1. **Current FY 2026–27 PCT/tariff update layer.** Keep the FY 2025–26 tariff as the baseline, but obtain the official consolidated FY 2026–27 Pakistan Customs Tariff when FBR publishes it. Until then, OCR and legally validate the enacted Finance Act 2026 customs schedules already stored locally. FBR currently exposes FY 2025–26 as its consolidated tariff: [FBR Customs Tariff](https://www.fbr.gov.pk/categ/customs-tariff/51149/70853/131188).

2. **Every Export Policy Order 2022 amendment through the development cutoff.** The official list shows at least S.R.O. 1021(I)/2024, 705(I)/2025, 1727(I)/2025, 1902(I)/2025 and 2486(I)/2025 after the local base order. Download and apply them cumulatively: [Ministry of Commerce SROs](https://www.commerce.gov.pk/sros/).

3. **Current consolidated Export Facilitation Scheme 2021.** Download FBR's S.R.O. 957(I)/2021 consolidation **as on 17 June 2026**; the local original notification and FAQ do not replace it: [FBR Export Facilitation Schemes](https://www.fbr.gov.pk/export-facilitaion-schemes/51149/132200).

4. **Remaining S.R.O. 209(I)/2009 textile drawback chain.** At minimum obtain the true **S.R.O. 551(I)/2020**, the official notification that inserted Schedule XXXVII, and any later applicable amendment/transposition documents. The file formerly named `Sro 2015.pdf` is S.R.O. 551(I)/2008 and cannot substitute for it. Official S.R.O. 551(I)/2020: [FBR PDF](https://download1.fbr.gov.pk/SROs/20206241463635573SRO-551.pdf).

5. **S.R.O. 645(I)/2018 amendment/current-status chain.** Obtain S.R.O. 1011(I)/2018, S.R.O. 192(I)/2019 and a documented current-effect interpretation. FBR still lists 645(I)/2018 on its active export SRO page, but the local base document alone contains an original 30 June 2019 rescission date and cannot be executed safely: [FBR Active Export SROs](https://fbr.gov.pk/ActiveSrosExport).

6. **PCT-specific textile product requirements from PSW/Tradeverse (TIPP).** Capture a dated, source-linked dataset for the selected MVP PCT codes: responsible agency, LPCO type, legal basis, conditions, supporting documents and effective dates. The local E-CO manual covers workflow, not product applicability: [PSW Trade Information Portal](https://psw.gov.pk/trade-information-portal).

7. **Current export-declaration and exchange-rate sources.** Add the current PSW Single Declaration — Export manual/schema and an authoritative dated exchange-rate feed with history. FBR currently directs users to NBP for the latest customs exchange rates: [PSW Single Declaration — Export](https://www.psw.gov.pk/public/single-declaration-export) and [FBR Exchange Rates](https://www.fbr.gov.pk/exchange-rates/51149/131194).

8. **Customs Rules amendment delta after 31 August 2025.** The new 634-page local compilation is verified through that date. Audit and store only later amendments relevant to export declarations, documents, drawback and EFS before setting a current legal cutoff: [FBR Customs Rules listing](https://www.fbr.gov.pk/categ/customs-tariff/51149/70853/131188).

## Required processing of files already present

No additional download is needed for these tasks, but they must be completed before automated rule use:

- OCR and validate Finance Act 2026 pages 77–255.
- OCR and validate S.R.O. 576(I)/2017, S.R.O. 645(I)/2018 and S.R.O. 979(I)/2015.
- Parse tariff and SRO tables into structured rows, preserving source page, effective date, amendment parent and supersession links.
- Define an explicit legal cutoff date for every decision and never mix FY 2017–18, FY 2025–26 and FY 2026–27 PCT versions without a verified transposition.

## Short answer

You now have a strong development dataset, including the missing S.R.O. 209(I)/2009 base and the enacted Finance Act 2026. You can build the software and test extraction/RAG now. Do not release automated prohibited/restricted, LPCO, regulatory-duty, current-PCT, EFS or textile-drawback decisions until the eight minimum gaps above are closed.
