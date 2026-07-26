# Supporting-Document Verification

Closes the gap where a caller could satisfy a required-document rule by putting
the document's *name* in `additional_uploaded_document_types`. A supporting
document must now be uploaded, read, classified and cross-checked before it can
contribute to a positive compliance outcome.

## 1. Request schema

`POST /api/v1/compliance/check-documents/multi-line` and
`POST /api/v1/customs-audit/workflows` now accept typed document UUIDs:

```json
{
  "commercial_invoice_document_id": "<uuid>",
  "packing_list_document_id": "<uuid>",
  "supporting_documents": [
    { "document_type": "form_e", "document_id": "<uuid>" },
    { "document_type": "certificate_of_origin", "document_id": "<uuid>" }
  ],
  "shipment_date": "2026-07-22",
  "letter_of_credit_date": null
}
```

`additional_uploaded_document_types` is still accepted for backward
compatibility, but it is now treated as a **claim**, not as evidence.

Supported types (`SupportingDocumentType`): `form_e_or_psw_export_declaration`,
`certificate_of_origin`, `sbp_deposit_proof`, `sbp_confirmation`,
`irrevocable_letter_of_credit`, `phytosanitary_certificate`,
`importing_country_permit`, `goods_declaration`, `bill_of_lading`,
`export_contract`. Legacy aliases (`form_e`, `coo`, `lc`, `import_permit`, …)
map onto these canonical values.

## 2. Extraction

`app/services/supporting_document_service.py` reuses the **existing** pipeline —
document-upload records, PDF text extraction, Tesseract OCR fallback, Groq
structured output, Pydantic validation, field provenance. No duplicate upload
path was created.

One flat `SupportingDocumentCandidates` model covers every type, carrying
`detected_document_type`, `document_number`, `issue_date`, `expiry_date`,
`exporter_or_applicant`, `buyer_or_beneficiary`, `invoice_reference`,
`contract_reference`, `pct_code`, `product_or_commodity`, `destination_country`,
`issuing_authority`, `bank_name`, `amount`, `currency`, `percentage`,
`quantity`, `shipment_deadline`, `related_reference`, `treatment_or_inspection`
— each as a provenance-carrying field with source page, confidence and
validation status.

A deliberately flat request shape is the lesson from DEF-001: one small, flat
model is far more reliable than a differently-shaped deeply-nested model per
document type. Type-specific *requirements* are then enforced deterministically
in Python via `REQUIRED_FIELDS`.

## 3. Deterministic cross-checks

Every comparison and every resulting status is computed in Python. The model
only reads the page.

| Check | Applies to |
|---|---|
| Detected type matches claimed type | all |
| Required fields present for the type | all |
| Exporter matches the invoice | all |
| Invoice reference matches | Form-E, SBP deposit/confirmation, goods declaration |
| Destination matches | certificate of origin, phytosanitary, import permit |
| PCT code matches | any document that prints one |
| Beneficiary matches exporter | letter of credit |
| Certified commodity matches product | phytosanitary certificate |
| Deposit percentage equals the 1% SRO requirement | SBP deposit proof |
| Not expired | any document printing an expiry date |

## 4. Verification states

`claimed_only` → `uploaded` → `type_verified` → `fields_verified` →
`shipment_matched`, plus `unreadable` and `type_mismatch`.

`authenticity_status` is always `not_externally_verified`. The system verifies
readable content and internal consistency only; it never claims a document was
confirmed with the issuing bank or agency, because no such integration exists.
This is reported as a stated limitation, not as a failure.

## 5. Status behaviour

| Situation | Result |
|---|---|
| Claimed type string, **no UUID** | **failed** — "a document name on its own is not evidence" |
| Required document not uploaded | **failed** |
| PDF uploaded but unreadable / zero confidence | **manual_review** |
| Uploaded document is the wrong type | **failed**, upload preserved as evidence |
| Correct type, some required fields uncertain | **manual_review** |
| Content conflicts with the invoice | **failed** |
| Content matches, authenticity unconfirmable | **passed** + `not_externally_verified` |

`verified_document_types()` returns only documents that were uploaded, not
failed, and reached at least `type_verified`. That set — **not** the caller's
strings — is what feeds the existing document-presence rules.

## 6. Can a claimed-only document still pass?

**No.** Enforced at three levels:

1. `verify_supporting_document()` returns `state=claimed_only`,
   `content_status=failed` when `document_id is None`.
2. `verified_document_types()` excludes anything not uploaded.
3. `_build_item_shipment_input()` builds `uploaded_documents` from that verified
   set instead of `request.additional_uploaded_document_types`.

Regression tests: `test_11_claimed_string_without_uuid_does_not_count_as_present`,
`test_12_required_document_uuid_missing_is_reported_as_failure`, and
`assert_claimed_only_documents_do_not_count` in the multi-line suite.

This change is **observable**: five existing multi-line tests began failing
because they claimed `form_e` and `certificate_of_origin` without uploading
them. That is the unsafe behaviour this work removes, so those tests were
retargeted onto the matching/arithmetic behaviour they actually exercise, and
the new document contract is asserted explicitly.

## 7. Readable report

`build_audit_report()` emits a `supporting_documents` section per document:
required type, uploaded yes/no, detected type, document number, invoice/exporter/
destination/PCT match, extraction confidence, OCR confidence, verification
state, content result, source page, external authenticity, required action.

```
Certificate of origin
  Uploaded: Yes | Type: certificate_of_origin | No: COO-TEST-001
  Exporter match: Yes | Destination: Yes
  Confidence: 96% | Result: Passed | External authenticity: Not verified

form e or psw export declaration
  Uploaded: No | Result: Failed
  Action: Upload the form e PDF.

Phytosanitary certificate
  Uploaded: Yes | Confidence: 70% | OCR: 62%
  Result: Needs human review
  Action: Confirm the certificate number on page 1.
```

Clients never compute this status; the deterministic engine owns it.

## 8. Safety invariants preserved

- The deterministic Python engine remains the only authority for
  passed/failed/manual_review/not_applicable.
- The LLM classifies and reads; it never decides a status.
- No Pydantic validation was weakened.
- No value is repaired by regex; an unreadable value stays null and becomes
  manual_review.
- Nothing is invented — every field is null unless printed on the page.

## 9. Defect found while building this (DEF-008)

Live-testing supporting-document extraction surfaced a regression introduced by
the earlier DEF-001 fix.

Pydantic models a `Decimal` as `anyOf[number, string(pattern=…), null]`. DEF-001
removed the unsupported lookahead `pattern` — which left an **unconstrained
string branch**. The provider used it, returning `confidence: "unknown"`, and
local validation correctly rejected all 20 fields.

Fix: drop the *whole* unsupported branch rather than just its pattern, so a JSON
number is the only numeric option offered. This is strictly tighter than the
original schema, and local validation is still untouched.

Live evidence — the identical request, before and after:

```
before: 400 Generated JSON does not match the expected schema
        → 20 validation errors, confidence='unknown'
after : LIVE EXTRACTION OK (4120 tokens)
        detected    : CERTIFICATE OF ORIGIN
        number      : COO-TEST-001
        exporter    : Lahore Cotton Garments (Pvt.) Ltd.
        confidence  : 1
```

That run is also the first live confirmation of **DEF-003**: the exporter value
is `Lahore Cotton Garments (Pvt.) Ltd.` with no `Exporter` label absorbed into
it, produced by an explicit prompt rule rather than by string repair.

## 10. Synthetic supporting-document fixtures

`backend/scripts/generate_supporting_documents.py` extends the factory with
**128 PDFs** across 21 bundles — every one of the ten supported types, in both a
text and a rasterized/scanned variant.

- **6 valid bundles** attached to the existing clean scenarios. Every printed
  field is derived from that scenario's own invoice, so exporter, buyer, invoice
  number, product, PCT code, destination, amount, currency and dates agree by
  construction. China textile shipments carry a CPFTA certificate of origin; the
  Germany scenario carries the general non-preferential version instead; raw
  cotton carries the full SRO 2486(I)/2025 set.
- **15 error bundles**, one controlled defect each: claimed-without-upload,
  wrong type uploaded, four certificate-of-origin content mismatches, missing
  certificate number, expired permit, wrong SBP percentage, wrong deposit
  reference, wrong LC beneficiary, expired LC shipment deadline, wrong
  phytosanitary commodity, degraded scan, unreadable document.

Every page is watermarked `SYNTHETIC TEST DOCUMENT - NOT VALID FOR TRADE,
CUSTOMS OR PAYMENT`. Every identifier is an obviously synthetic `SYN-…` string
derived from the invoice number. No real bank account, signature, government
certificate number, company registration number or personal identifier appears
anywhere. The one bank named is the fictional "Synthetic Test Bank Limited".

Expectations are hand-declared beside each specification and merged into
`scenario_manifest.json` *without* importing the verifier, then cross-checked
against the literal text of each generated PDF: **625/625 supporting-document
field expectations verified against PDF bytes** (alongside 236/236 shipment
expectations).

Two expectations are deliberately left unasserted, and say so in the manifest:
how much a 55-DPI scan recovers is not deterministic, so the degraded-scan
scenario asserts only the safety property — the result must be `manual_review`,
never a pass and never a legal failure.

## 11. Defects found by the live run

Both were invisible to the unit tests and only appeared once real PDFs went
through the real HTTP API.

### DEF-009 — every real supporting document was reported unreadable

`SupportingDocumentCandidates` is one flat 20-field model covering ten document
types, so a certificate of origin legitimately prints no deposit percentage, no
LC shipment deadline and no bank name. Those come back null at confidence 0.
`document_confidence` took the **minimum across all 20 fields**, so it was 0 for
every real document — and confidence 0 is the signal for "this PDF could not be
read".

The unit tests missed it because they build extractions with one uniform
confidence across every field, which no real extraction ever produces.

```
live before: state=unreadable  content_status=manual_review  confidence=None
             (while the extraction had correctly returned
              "Certificate of Origin" and "SYN-COO-LCGINV2026002")
live after : state=shipment_matched  content_status=passed  confidence=1  page=1
```

Fix: confidence describes what was *recovered*. A field that is not printed is
not evidence that the page is illegible; recovering nothing is. Which absent
fields actually matter is a separate per-type question that `REQUIRED_FIELDS`
already answers deterministically.

### DEF-010 — a correct Form-E was rejected as the wrong document type

Live, the model read the declaration's own heading as *"Pakistan Single Window
export declaration"*. That is not an exact alias, so it resolved to `UNKNOWN`
and the document failed as a type mismatch. A real Form-E **is** printed under
that heading, so this was the system being wrong, not the fixture.

Fix: resolve a printed description in two deterministic stages — exact alias
match, then a **unique** whole-token containment match. The relaxation is
bounded and cannot launder a wrong document:

- a description naming two different types (`"Bill of Lading and Certificate of
  Origin"`) → `UNKNOWN` → manual review, never a guess;
- an unrecognised description → `UNKNOWN`;
- a genuinely wrong document (a bill of lading uploaded as a certificate of
  origin) still fails as a type mismatch.

## 12. Auditor supporting-document review

`audit_supporting_documents()` re-derives every comparison from the *extracted
field values*, deliberately not importing the verifier's comparison helpers — an
auditor that shares the code path it is auditing cannot disagree with it. It
reports `confirmed_supporting_documents`, `challenged_supporting_documents`,
`document_type_disagreements`, `field_mismatches`, `missing_document_fields`,
`low_confidence_documents` and `authenticity_limitations`.

Consensus compares the Broker's `verified_supporting_documents` against the
Auditor's independent re-check. A document the Broker reports as verified but
the Auditor disputes is recorded as a disagreement and routed to a human. The
Auditor's strongest available action remains `human_review`; it never writes a
status.

## 13. Still outstanding

| Item | Status |
|---|---|
| Full 28-scenario shipment rerun + 42 supporting-document entries | **Not completed.** A full pass needs roughly 1M Groq tokens; the free tier caps 8,000 tokens per *minute*, so one supporting bundle takes ~4 minutes of wall clock. |
| Scanned-variant supporting-document run | **Not run.** |
| LangGraph workflow leg of the supporting-document scenarios | **Not run** — the live runs used `--no-workflow` to keep the token cost inside the per-minute cap. |
