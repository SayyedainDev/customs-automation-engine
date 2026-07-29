# CACE narrow final verification

Verification date: 2026-07-29

Legal cutoff: 2026-07-22

Base commit: `3aaa95c8e524f22bafbe051f9daa8dac387ce2ee`

Executable rule-data version: `sha256:3258d244212f77ef4f81f6039733fca689b3ac49d2fd09d5252d1ffbcfc43340`

## Scope and limits

CACE is a textile-focused capstone. Deterministic compliance supports exactly
17 configured PCT codes. Ask CACE searches accepted indexed regulatory sources;
blocked amendments are not treated as accepted law. Official-source preference
is query aware, while curated summaries remain searchable and supplemental.
CACE does not provide customs clearance or legal advice, and this verification
does not claim complete coverage of Pakistani textile regulation.

## Executable 17-code ruleset

Every code requires Commercial Invoice, Packing List and Form-E through
`xr_common_commercial_invoice`, `xr_common_packing_list` and
`xr_common_form_e`. Every non-raw-cotton code also has
`xr_coo_china` (COO required for China) and
`xr_coo_other_destinations` (conditional/manual review elsewhere). Each code
has the four exact identifiers `xr_<PCT>_export_status`,
`xr_<PCT>_licence_required`, `xr_<PCT>_permit_required` and
`xr_<PCT>_approval_required`.

| PCT | Product | Category | Tariff page | Additional/conditional rules | Current manual-review boundaries |
|---|---|---|---:|---|---|
| 52010090 | Raw cotton, other | raw material | 133 | Import permit conditional; phytosanitary certificate, SBP deposit proof, SBP confirmation and irrevocable LC required; shipment within 180 days | Conditional importing-country permit; missing dates/evidence; uncertain extraction |
| 52051100 | Cotton yarn | yarn | 133 | COO rules; Afghanistan duty-drawback condition | Afghanistan destination; conditional COO elsewhere; incomplete curated legal provenance |
| 52052100 | Combed cotton yarn (heavy count) | yarn | 134 | COO rules; Afghanistan duty-drawback condition | Afghanistan destination; conditional COO elsewhere; incomplete product-rule legal provenance |
| 52085200 | Printed cotton fabric (light) | woven fabric | 137 | COO rules | Conditional COO elsewhere; incomplete product-rule legal provenance |
| 52093100 | Dyed cotton fabric (heavy) | woven fabric | 137 | COO rules | Conditional COO elsewhere; incomplete product-rule legal provenance |
| 52094200 | Denim fabric | woven fabric | 137 | COO rules | Conditional COO elsewhere; incomplete curated legal provenance |
| 52114200 | Blended denim fabric | woven fabric | 138 | COO rules | Conditional COO elsewhere; incomplete product-rule legal provenance |
| 61051000 | Men's knitted cotton shirts | knitted garment | 155 | COO rules | Conditional COO elsewhere; incomplete product-rule legal provenance |
| 61061000 | Women's knitted cotton blouses | knitted garment | 155 | COO rules | Conditional COO elsewhere; incomplete product-rule legal provenance |
| 61091000 | Cotton knitted T-shirts | knitted garment | 156 | COO rules | Conditional COO elsewhere; incomplete curated legal provenance |
| 61102000 | Cotton knitted jerseys and pullovers | knitted garment | 156 | COO rules | Conditional COO elsewhere; incomplete product-rule legal provenance |
| 62034200 | Men's woven cotton trousers | woven garment | 158 | COO rules | Conditional COO elsewhere; incomplete product-rule legal provenance |
| 62046290 | Women's woven cotton trousers | woven garment | 159 | COO rules | Conditional COO elsewhere; incomplete product-rule legal provenance |
| 62052090 | Men's woven cotton shirts | woven garment | 159 | COO rules | Conditional COO elsewhere; incomplete product-rule legal provenance |
| 63013000 | Cotton blankets and travelling rugs | made-up | 161 | COO rules | Conditional COO elsewhere; incomplete product-rule legal provenance |
| 63023110 | Cotton bed sheets, mill-made | made-up | 161 | COO rules | Conditional COO elsewhere; incomplete curated legal provenance |
| 63026010 | Cotton terry towels (mill-made) | made-up | 162 | COO rules | Conditional COO elsewhere; incomplete product-rule legal provenance |

The raw-cotton-only executable identifiers are
`xr_52010090_phytosanitary_certificate`,
`xr_52010090_sbp_deposit_proof`, `xr_52010090_sbp_confirmation`,
`xr_52010090_irrevocable_letter_of_credit` and
`xr_52010090_shipment_within_180_days`. The Afghanistan yarn rule is scoped
only to 52051100/52052100 and does not leak to other products.

Sources and support classification:

- All product identities and tariff descriptions are directly verified against
  the official Pakistan Customs Tariff FY 2025-26 on the pages above.
- The common-document and COO procedures are executable curated configuration
  derived from official TIPP/TDAP procedure records; their page locators are
  web-procedure locators rather than PDF pages.
- 52010090, 52051100, 52094200, 61091000 and 63023110 have product-requirement
  records sourced from curated TIPP commodity data.
- The other product status/licence/permit/approval records were reconstructed
  against Export Policy Order 2022 paragraph 4(1) and the absence of their
  code/parent heading from Schedules I-III and Appendices A-J. They remain
  configuration-driven and fail closed where full legal provenance is absent.
- Raw-cotton financial/deadline rules are directly supported by the manually
  validated SRO 2486(I)/2025 page 1. The raw-cotton EPO context is Schedule II
  serial 9 and Appendix J page 92. The yarn/Afghanistan condition is EPO page
  18, paragraph 7(3)/Schedule III item 3.

## Seven blocked amendments

All seven complete source PDFs are present. None is accepted RAG evidence and
none can overwrite an active rule.

| Amendment | Published | Extraction/validation | Blocked reason | Unclear page/value | Predecessor |
|---|---|---|---|---|---|
| SRO 561(I)/2023 | 2023-05-15 | Tesseract; operative page OCR-verified | Not in accepted registry; operative page not manually validated | None | EPO paragraph 7(5) |
| SRO 1087(I)/2023 | 2023-08-18 | Tesseract plus manual line validation | Consolidated chain not approved; not indexed | None | EPO paragraph 7(6)-(7), Schedule II serial 8 |
| SRO 629(I)/2024 | 2024-04-30 | Mixed manual/OCR validation | Two printed-code conflicts need authoritative correction | p2 2303.4910/2903.4910; p6 4413.8200/4418.8200 | Schedule I; Appendices A/B/C/G/H/J |
| SRO 1021(I)/2024 | 2024-07-11 | Operative page manually validated | Omitted SRO 433(I)/2024 predecessor wording not visually validated | p1 old serial-19 column (4) | SRO 433(I)/2024; Schedule I serial 19 |
| SRO 705(I)/2025 | 2025-04-18 | Tesseract; OCR-verified only | Page not manually legally validated; not indexed | None | Schedule I serial 12 |
| SRO 1727(I)/2025 | 2025-09-08 | Amendment manual; predecessor OCR-verified | Consolidated chain not approved; not indexed | None | Appendix G serial ranges |
| SRO 1902(I)/2025 | 2025-10-02 | Tesseract; OCR-verified only | “Respective headings” and page not manually validated; not indexed | None | Schedule I and Appendix G serial 170 |

Checksums, exact file paths, OCR details and authority metadata are recorded in
`regulatory_data/processed/commerce/export_policy/blocked_amendment_impact.json`.
No separate effective date was printed/validated for these seven, so the value
is recorded as unknown rather than inferred.

## Amendment-to-PCT impact matrix

| Amendment | Blocked reason | Possible affected PCTs | Possible affected configured rules | Evidence page | Confidence | Action |
|---|---|---|---|---|---|---|
| SRO 561(I)/2023 | Operative page only OCR-verified | All 17 only if an Afghanistan land route were in scope | None; CACE has no route verdict | SRO p3; EPO p3 | High | No detected impact |
| SRO 1087(I)/2023 | Chain not approved | All 17 only under EPZ/manufacturing-bond/export-oriented/agency regimes | None; yarn rule remains paragraph 7(3) | SRO p1; EPO pp4,15 | High | No detected impact |
| SRO 629(I)/2024 | Unresolved printed codes | None | None | pp1-7 | High | Unrelated to supported textile scope |
| SRO 1021(I)/2024 | Predecessor wording unresolved | None | None | p1; EPO p14 | High | Unrelated to supported textile scope |
| SRO 705(I)/2025 | OCR-verified only | None | None | p1 | High | Unrelated to supported textile scope |
| SRO 1727(I)/2025 | Chain not approved | None | None | p1; EPO pp40-42 | High | Unrelated to supported textile scope |
| SRO 1902(I)/2025 | Broad heading/OCR-only | None | None | p1; EPO p40 | High | Unrelated to supported textile scope |

The two broad paragraph amendments have possible operational textile relevance,
but neither changes a field or decision CACE is configured to make. Therefore
no blanket amendment-driven manual-review rule was added. Existing manual-review
behavior remains unchanged.

Searches performed for every amendment:

- normalized eight-digit codes with/without dots;
- every six-digit subheading and four-digit supported parent heading;
- Chapters 50-63 and broader textile/material/product descriptions;
- destinations, routes, licences, permits, prohibitions, restrictions,
  duty-drawback, COO, Form-E, SBP and date terms;
- schedule, appendix and paragraph references against predecessor text;
- structured affected-code arrays and unresolved OCR conflict records.

## Query-aware official-source priority

The existing retriever remains one pipeline:

1. metadata filters;
2. persistent PostgreSQL full-text candidates and cached dense-matrix
   candidates;
3. RRF;
4. existing reranker;
5. query-aware source compatibility among already-relevant candidates;
6. currentness preference;
7. evidence gate;
8. parent context.

The change is a final deterministic ordering feature, not a global
“official always wins” rule. An irrelevant official passage still fails the
relevance gate; a highly relevant curated summary remains available as
supplemental evidence. Explicit historical questions may prefer a historical
source, while otherwise comparable current sources outrank historical or
superseded sources. Blocked sources remain excluded.

### Before

The real 6,756-child PostgreSQL corpus showed:

- exact tariff 62034200 and 63026010: evidence not found;
- exact tariff 61051000: curated summary first;
- Customs Act seizure/confiscation: Customs Act first;
- Customs Rules goods declarations: evidence not found;
- Export Facilitation Scheme: curated summary first;
- EPO raw-cotton/Appendix-J questions: curated summary first;
- PSW Single Declaration: current official manual first;
- electronic COO: historical TDAP guide first;
- configured checklist: curated configuration first.

Cold latency was approximately 938 ms; warmed queries were approximately
18-82 ms.

### After

The fixed evaluation set is
`backend/tests/fixtures/query_aware_source_priority_eval.json`. The detailed
diagnostic records lexical candidates, dense candidates, RRF/reranker scores,
source kind, currency, accepted passages and latency in
`backend/reports/query_aware_source_priority_evaluation.json`.

Verified category results:

- 62034200/61051000/63026010 tariff questions: official tariff first;
- seizure/confiscation: Customs Act first;
- goods declarations/EFS procedure: Customs Rules first;
- raw-cotton schedule/Appendix J: current EPO first;
- Single Declaration/eCOO: matching current PSW/TDAP manual first;
- configured checklist: deterministic configuration primary, with official
  passages supporting and curated wording clearly supplemental;
- explicit historical questions: historical source remains available.

Measured end-to-end latency on the fixed local PostgreSQL evaluation was
33-47 ms for exact-tariff queries, 56-101 ms for EPO/configured-guidance
queries, 174-268 ms for PSW queries, and 256-742 ms for the broader Customs
Act/Rules queries. All queries remained below one second. The broader queries
now return the intended official evidence but are slower than the old 18-82 ms
warmed baseline, which often returned no accepted evidence or the wrong
primary source. Exact-tariff latency improved. Persistent lexical search
remains active and BM25 is not rebuilt per request. A real
sentence-transformer/cross-encoder diagnostic was unavailable locally because
`sentence-transformers` is not installed; deterministic hashing embeddings and
the lexical reranker were used without downloads or external calls.

## Three fixture families and outcomes

Each family contains four embedded-text PDFs plus checksum metadata:

- 62034200: Men's woven cotton trousers (woven garment);
- 61051000: Men's knitted cotton shirts (knitted garment);
- 63026010: Cotton terry towels, mill-made (made-up).

Every PDF is visibly synthetic and contains consistent exporter/consignee
identity and addresses, invoice/date, destination, currency, PCT/description,
quantity/unit/unit price/line and invoice totals, package/net/gross weights,
Pakistan origin and shipment reference.

| Scenario | Outcome for all three families |
|---|---|
| Clean documents | All uploads, extraction, matching, supporting-document verification and COO checks pass; final deterministic status remains `manual_review` solely because existing product-requirement provenance is incomplete |
| Missing COO for China | `failed`; exact missing destination COO finding |
| Packing gross-weight +25 KG | `failed`; exact invoice and packing values preserved in the mismatch evidence |
| Germany without COO | `manual_review`; China hard requirement does not leak, and the other-destination condition remains conditional |
| Ambiguous invoice quantity containing letter O | `manual_review`; value is null, original page text is retained, and no digit is guessed |

For each clean family, API-level tests upload all four documents, persist and
extract them, run deterministic matching/compliance, index shipment chunks,
execute deterministic Broker and Auditor nodes, produce consensus, freeze
revision 1 and open the final report. Shipment chat answers all nine required
questions from structured extraction, shipment-document chunks, frozen audit
state or accepted regulatory evidence. Eighteen chat messages per fixture are
persisted without changing workflow status, deterministic status, updated time
or frozen report.

No real Groq call occurs in generation, tests, retrieval evaluation or audit
execution. External extraction/evidence boundaries use deterministic test
providers.

## Remaining limitations

- Product-level negative requirement statements still lack complete official
  page/effective-date provenance, so clean synthetic products correctly remain
  manual review.
- The seven blocked amendments remain legally unavailable; their impact result
  is a narrow non-overlap finding for the present executable rules, not a legal
  consolidation.
- Local real embedding/reranker models were unavailable.
- Browser automation is not installed; backend routes are exercised directly
  and the frontend is build-verified.
- CACE remains a single-user capstone prototype, not a production customs or
  legal system.
