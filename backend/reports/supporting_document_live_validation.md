# Supporting-Document Live Validation

Every figure below is counted from an actual completed run against the real HTTP API (real Groq, real Tesseract, real PostgreSQL). Scenarios that did not run are reported as not run, never estimated.

## Coverage

- Supporting-document entries in the manifest: **42**
- Entries completed live: **2** (text: 2)
- Entries fully correct: **1/2**
- Entries provider-blocked (never assessed): **19**

## The claim this run exists to test

| Property | Result |
| --- | --- |
| Uploaded UUID is the UUID actually processed | 6/6 (100.0%) |
| Document type read from the page, correct | 6/6 (100.0%) |
| Deterministic content status matches the manifest | 7/7 (100.0%) |
| Claimed-only type earned nothing | 1/1 (100.0%) |
| External authenticity always `not_externally_verified` | 6/6 (100.0%) |
| **False legal passes** | **0** |

No claimed-only document type satisfied any requirement in any completed run.

## Per scenario

### `clean_cotton_yarn_supporting` [text]

- Deterministic status: **failed** (expected `passed`)
- Fully correct: **no**

| Document | Uploaded | Detected | State | Result | Expected | Authenticity |
| --- | --- | --- | --- | --- | --- | --- |
| `form_e_or_psw_export_declaration` | Yes | Form E Export Declaration | shipment_matched | passed | passed | not_externally_verified |
| `certificate_of_origin` | Yes | CERTIFICATE OF ORIGIN | shipment_matched | passed | passed | not_externally_verified |
| `goods_declaration` | Yes | Goods Declaration | shipment_matched | passed | passed | not_externally_verified |
| `bill_of_lading` | Yes | Bill of Lading | shipment_matched | passed | passed | not_externally_verified |
| `export_contract` | Yes | EXPORT CONTRACT | shipment_matched | passed | passed | not_externally_verified |

### `supporting_form_e_claimed_without_upload` [text]

- Deterministic status: **failed** (expected `failed`)
- Fully correct: **yes**
- Claimed as bare strings (no UUID): `form_e`

| Document | Uploaded | Detected | State | Result | Expected | Authenticity |
| --- | --- | --- | --- | --- | --- | --- |
| `certificate_of_origin` | Yes | Certificate of Origin | shipment_matched | passed | passed | not_externally_verified |
| `form_e_or_psw_export_declaration` | No | - | claimed_only | failed | failed | not_externally_verified |
