# Phase 1 Deterministic Compliance Engine — Code Review

Review date: 2026-07-24
Review scope: Phase 1 implementation only
Implementation changes made during this review: none
Test changes made during this review: requested edge-case tests only

## Executive summary

Phase 1 proves the basic idea successfully:

1. FastAPI receives structured shipment data.
2. Pydantic validates and normalizes it.
3. JSON files provide the five supported products and some product requirements.
4. Python runs deterministic arithmetic, weight and document checks.
5. No LLM decides whether a check passes or fails.

The engine is suitable as a proof of concept, but it should not yet be described
as a legal-grade compliance engine.

The most important safety problem is that `null` and unverified regulatory
values can be treated as `not_applicable`. When all other checks pass, this can
produce an overall `passed` result. The engine also ignores regulatory
verification status and does not attach complete legal provenance to every
legal check.

Overall review result: **refactor and provenance hardening recommended before
adding more products or calling the result a legal compliance pass.**

## 1. Current architecture

### Files and their jobs

| File | Simple purpose | Current size |
|---|---|---:|
| `app/api/routes/compliance.py` | Defines `POST /api/v1/compliance/check`. It receives the validated request, gets the rule engine and returns its response. | 30 lines |
| `app/schemas/compliance.py` | Defines the request model, result statuses, individual check result and complete response. It also normalizes PCT codes during request validation. | 60 lines |
| `app/services/compliance/rule_loader.py` | Finds and reads the two JSON rule sources, normalizes their PCT codes, checks that every configured product has metadata and requirements, and caches the resulting rule set. | 121 lines |
| `app/services/compliance/rule_engine.py` | Orchestrates every check and also contains arithmetic rules, document aliases, general document checks, product checks, China certificate-of-origin logic, raw-cotton logic, result creation and final-status calculation. | 839 lines |
| `app/tests/test_compliance_rules.py` | Creates sample shipment payloads and checks normal, failing, unsupported and edge-case behavior. | Expanded during this review |
| `app/main.py` | Registers the compliance router with the FastAPI application. | Existing application file |

### Runtime relationship

```text
HTTP request
    |
    v
ShipmentComplianceInput
    |
    |-- parse dates and Decimal values
    |-- normalize PCT code
    v
compliance route
    |
    v
cached DeterministicComplianceRuleEngine
    |
    |-- cached JSON rule set
    |-- general checks
    |-- arithmetic checks
    |-- general document checks
    |-- product checks
    |-- raw-cotton checks, when applicable
    v
overall status
    |
    v
ComplianceCheckResponse
    |
    v
JSON HTTP response
```

## 2. Important functions: input and output

### API and schema functions

| Function or model | Input | Output | What it does |
|---|---|---|---|
| `check_shipment_compliance(payload)` | `ShipmentComplianceInput` | `ComplianceCheckResponse` | Calls the cached engine. Converts a rule-source loading error into HTTP 500. |
| `ShipmentComplianceInput.model_validate(data)` | Request dictionary/JSON | Validated input model | Parses numbers as `Decimal`, parses dates and runs PCT normalization. |
| `normalize_input_pct_code(value)` | PCT string or `None` | Eight-digit PCT string, `None`, or validation error | Calls `normalize_pct_code`. FastAPI returns HTTP 422 if the PCT format is invalid. |

### Rule-loader functions

| Function | Input | Output | What it does |
|---|---|---|---|
| `normalize_pct_code(value)` | String such as `5201.0090` or `52010090` | `52010090` | Accepts exactly eight digits, with an optional dot after the first four digits. Rejects other formats. |
| `_load_json(path)` | Filesystem path | Python dictionary | Reads a JSON object. Raises `RuleSourceError` if missing, malformed or not an object. |
| `load_compliance_rules()` | No arguments | Cached `ComplianceRuleSet` | Loads supported codes, product metadata, product requirements, common clearance data and certificate-of-origin data. |

### Main engine functions

| Function | Input | Output | What it does |
|---|---|---|---|
| `DeterministicComplianceRuleEngine.__init__(rules=None)` | Optional `ComplianceRuleSet` | Engine instance | Uses supplied rules or loads the cached rule set. |
| `check(shipment)` | `ShipmentComplianceInput` | `ComplianceCheckResponse` | Runs all applicable checks and calculates the final status. |
| `_canonical_document_type(value)` | Document-name string | Canonical document key | Converts names such as `Form-E`, `Form E` and `forme` to `form_e`. |
| `_check_required_fields(...)` | Shipment and PCT | One check result | Reports missing base fields. |
| `_check_pct_support(...)` | PCT and supported flag | One check result | Passes supported products; sends unsupported products to manual review. |
| `_check_positive_quantity(...)` | Shipment and PCT | One check result | Checks quantity is greater than zero. |
| `_check_positive_unit_price(...)` | Shipment and PCT | One check result | Checks unit price is greater than zero. |
| `_check_line_total(...)` | Shipment and PCT | One check result | Compares `quantity × unit_price` with the declared line total. |
| `_check_invoice_total(...)` | Shipment and PCT | One check result | Compares the single line total with the invoice total. |
| `_check_weights(...)` | Shipment and PCT | One check result | Checks gross weight is not below net weight. |
| `_check_general_documents(...)` | PCT and normalized document set | Three check results | Checks commercial invoice, packing list and Form-E. |
| `_check_product_requirements(...)` | Shipment, product rule dictionary and document set | Product check results | Runs licence, permit, certificate and certificate-of-origin checks. Adds raw-cotton checks for `52010090`. |
| `_check_certificate_of_origin(...)` | Shipment, product and document set | One check result | Requires a certificate for China; passes a supplied certificate for other destinations; otherwise requests manual review. |
| `_check_raw_cotton_rules(...)` | Shipment and document set | Four check results | Checks SBP deposit proof, SBP confirmation, irrevocable LC and the 180-day period. Phytosanitary certification is checked separately by `_check_certificate_requirement`. |
| `_result(...)` | Check fields | `ComplianceCheckResult` | Creates results with a consistent shape. |
| `_overall_status(checks)` | List of check results | Status enum | Any failure wins; otherwise manual review wins; otherwise the result passes. |
| `get_compliance_rule_engine()` | No arguments | Cached engine | Reuses one engine instance and its loaded rules. |

## 3. Complete request flow

### Step 1 — FastAPI receives the request

The request reaches:

```text
POST /api/v1/compliance/check
```

FastAPI reads the JSON body and asks Pydantic to create
`ShipmentComplianceInput`.

### Step 2 — Pydantic parses the values

Pydantic:

- trims surrounding whitespace from strings;
- parses money and quantity fields as `Decimal`;
- parses shipment and letter-of-credit dates;
- accepts optional fields so missing data can be reported by the engine;
- normalizes a valid PCT code to eight digits.

An invalid PCT such as `6109.100` fails here. FastAPI returns HTTP 422 and the
engine does not run.

### Step 3 — The route obtains the engine

`check_shipment_compliance()` calls `get_compliance_rule_engine()`.

On the first call:

1. `load_compliance_rules()` reads both JSON files.
2. It creates lookup dictionaries by normalized PCT code.
3. It checks that all five configured products have tariff metadata and product
   requirements.
4. It returns a `ComplianceRuleSet`.
5. The rule set and engine are cached.

Later requests reuse the cached engine.

### Step 4 — Document names are normalized

The engine converts document names to lowercase snake-style keys and applies
aliases. It stores them in a `set`, so duplicates are removed.

Examples:

```text
Commercial Invoice -> commercial_invoice
invoice            -> commercial_invoice
Form-E             -> form_e
COO                -> certificate_of_origin
```

### Step 5 — General checks run

The engine always runs:

- base required fields;
- supported PCT check;
- positive quantity;
- positive unit price;
- line calculation;
- line total versus invoice total;
- gross versus net weight;
- commercial invoice;
- packing list;
- Form-E.

Unsupported products still receive these checks, but the PCT support result is
`manual_review`. They cannot receive an overall pass unless this behavior is
changed incorrectly later.

### Step 6 — Product checks run

For a supported product, the engine loads that product's requirement dictionary
and evaluates:

- licence;
- permit;
- certificate;
- destination-based certificate of origin.

For China, a certificate of origin is required. For another destination, the
absence of a certificate currently gives `manual_review`; presence of a
certificate gives `passed`.

### Step 7 — Raw-cotton checks run

For `52010090`, the engine additionally checks:

- proof of the 1% SBP deposit;
- SBP confirmation;
- irrevocable letter of credit;
- shipment date from 0 through 180 days after the LC date;
- phytosanitary certificate.

The permit rule is also evaluated. If the destination-dependent import permit
is absent, the result is `manual_review`.

### Step 8 — Final status is calculated

```text
one or more failed checks        -> failed
no failure but manual review     -> manual_review
only passed/not-applicable       -> passed
```

`is_compliant` is `true` only when the overall status is `passed`.

### Step 9 — FastAPI serializes the response

Every `ComplianceCheckResult` currently contains these keys:

- `check_id`
- `check_name`
- `status`
- `message`
- `pct_code`
- `required_document`
- `source_document`
- `source_url`
- `source_page`

Some provenance values are `null`; the schema does not yet contain SRO number
or legal cutoff date.

## 4. Why `rule_engine.py` is 839 lines

The file is long because it performs too many different jobs:

1. document-name normalization;
2. required-field checks;
3. arithmetic checks;
4. weight checks;
5. general document checks;
6. generic product checks;
7. destination logic;
8. raw-cotton-specific legal checks;
9. legal-source selection;
10. result construction;
11. overall-status calculation;
12. engine caching.

It also repeats the full `_result(...)` call structure in almost every branch.
Many simple checks therefore take 25–45 lines.

The line count itself is not a functional error, but the concentration of
responsibilities raises maintenance risk. A change to raw-cotton law, document
aliases or result provenance all require editing the same large file.

Risk level: **medium now; high when more products are added.**

## 5. Should the engine be separated?

Yes, after approval.

The proposed module boundaries match the existing responsibilities well:

| Proposed file | Responsibility |
|---|---|
| `general_checks.py` | Required fields, PCT support, weight and other basic non-arithmetic checks. |
| `arithmetic_checks.py` | Decimal-based quantity, price, line-total and invoice-total checks. |
| `document_checks.py` | Document normalization, aliases and common document-presence checks. |
| `product_checks.py` | Generic licence, permit, certificate and destination-origin checks driven by normalized rule records. |
| `raw_cotton_checks.py` | Only the temporary raw-cotton special checks until those rules become declarative data. |
| `result_builder.py` | Consistent result creation, provenance validation and status aggregation. |
| `rule_engine.py` | Small orchestrator that selects and runs check groups. |
| `rule_loader.py` | Loading, validation, versioning and cache policy. |

The refactor should not simply move the same hardcoded rules into smaller files.
It should first define validated internal rule models so each module consumes
clear data rather than arbitrary dictionaries.

## 6. Which rules are loaded from JSON?

### From `textile_mvp_pct_codes.json`

The loader uses:

- the five supported PCT codes;
- product metadata indexed by PCT;
- tariff source page for the PCT-support result.

The loader reads product metadata dictionaries, but the engine does not use:

- simple product name for product-name matching;
- official tariff description;
- tariff source document;
- validation status.

### From `textile_product_requirements.json`

The engine uses:

- per-product `licence_required.value`;
- per-product `permit_required.value`;
- per-product `certificate_required.value`;
- per-product `source_urls`;
- common export-clearance source URL;
- PCT codes covered by conditional certificates of origin;
- consolidated certificate-of-origin measure URL;
- destination procedure URL containing “China”.

The JSON contains more information than the engine uses. Currently ignored
fields include:

- `export_status`;
- `export_status.verification_status`;
- most `verification_status` fields;
- `approval_required`;
- `legal_basis`;
- the full `conditions` list;
- responsible agencies;
- fee and processing-time data;
- top-level and per-product retrieval dates;
- common supporting-document list;
- destination procedure document lists;
- source-local legal paths.

Risk level: **high**, because the engine can appear data-driven while ignoring
important legal qualifiers already present in the data.

## 7. Which rules are hardcoded in Python?

The following behavior is hardcoded:

- `52010090` is the raw-cotton special product;
- the 0.01 arithmetic tolerance;
- the base required-field list;
- document aliases;
- commercial invoice, packing list and Form-E requirements;
- generic document keys for licences, permits and certificates;
- null and `false` mean `not_applicable`;
- conditional permit behavior;
- China country aliases;
- China always requires certificate of origin;
- a certificate supplied for another destination is enough to pass that check;
- absence of origin certificate for a non-China destination means manual review;
- raw-cotton SBP deposit proof;
- raw-cotton SBP confirmation;
- raw-cotton irrevocable LC;
- raw-cotton 180-day calculation from LC date;
- raw-cotton source document, URL selection and page;
- status precedence;
- `is_compliant` means overall status equals `passed`.

Some of these rules also exist as prose in JSON, but the engine does not
compile or interpret that prose. The executable version remains the Python
branch.

## 8. Is the design data-driven enough for more products?

Only partly.

Adding a product with the same simple fields—licence, permit, certificate and
certificate of origin—may work after adding JSON records.

Adding a product with any new condition, such as:

- a percentage deposit;
- a shipment deadline;
- a destination-specific prohibition;
- a quantity ceiling;
- a required government approval;
- a different calculation;
- a conditional document with a precise trigger;

requires new Python code.

The current JSON is descriptive regulatory data, not an executable rule
definition. A safer next design would have validated rule records such as:

```json
{
  "rule_id": "raw_cotton_sbp_confirmation",
  "applies_to_pct_codes": ["52010090"],
  "check_type": "required_document",
  "required_document": "sbp_confirmation",
  "when": {"export_status": "conditional"},
  "provenance": {
    "source_document": "SRO 2486(I)/2025",
    "sro_number": "2486(I)/2025",
    "source_url": "...",
    "source_page": 1,
    "legal_cutoff_date": "..."
  }
}
```

That schema should be approved separately; it was not implemented during this
review.

## 9. Decimal and money review

### What is safe

- Monetary and quantity input fields use `Decimal`, not binary `float`.
- Arithmetic uses `Decimal × Decimal`.
- Difference comparison uses `Decimal("0.01")`.
- Test payloads use strings, preserving exact decimal values.
- The new rounding test confirms that `3 × 0.333 = 0.999` is accepted against
  `1.00` because the difference is below one cent.

### Remaining issues

- Values are not quantized to a currency-specific number of decimal places.
- No rounding mode is defined.
- The same `0.01` tolerance is used regardless of currency.
- A difference exactly equal to `0.01` passes because comparison uses `<=`.
- JSON clients may send numeric literals that have already lost precision
  before reaching Pydantic; string decimal input is safer.
- The model supports only one invoice line, so “line totals match invoice total”
  currently means one line total equals the invoice total.

Risk level: **low for the current demonstration; medium for financial use.**

## 10. PCT normalization review

The regular expression is:

```text
^\d{4}\.?\d{4}$
```

Accepted:

- `5201.0090`
- `52010090`
- surrounding whitespace, which is stripped

Rejected:

- fewer or more than eight digits;
- letters;
- misplaced dots;
- multiple dots;
- hyphens and other punctuation.

A structurally valid but unknown code such as `99999999` is accepted by the
schema, then receives `manual_review` because it is outside the MVP.

Result: **format validation is correct for the stated Phase 1 requirement.**

## 11. Can unknown, null or unverified data produce a pass?

Yes.

This is the highest-risk finding.

For licence, permit and generic certificate checks, this logic is used:

```python
if value in (None, False):
    status = not_applicable
```

But the source JSON explains that many `null` values mean:

```text
no product-specific requirement identified in reviewed sources
```

That is not the same as a verified legal statement that no requirement exists.

The engine also ignores:

- `verification_status`;
- `verified_with_portal_limitation`;
- source limitations;
- approval values;
- retrieval date;
- legal cutoff;
- source confidence.

Therefore, a supported T-shirt shipment to China with its normal documents and
certificate of origin currently passes even though licence, permit and approval
fields are null/unverified.

The new null-value safety test is marked `xfail` to document the desired safe
behavior without changing the implementation. It expects null regulatory data
to cause `manual_review`; the current engine does not do that.

Recommended behavior:

- `false` plus a verified status may produce `not_applicable`;
- `null`, unknown, inaccessible or unverified must produce `manual_review`;
- missing required rule metadata must fail closed;
- an overall legal pass must require all applicable regulatory fields to be
  positively verified.

Risk level: **high.**

## 12. Legal provenance review

The requested provenance fields are not complete.

| Required field | Current state |
|---|---|
| Source document | Present as a nullable string. Sometimes it is an official title; sometimes it is the structured JSON filename or an internal label. |
| SRO number | No dedicated response field. Raw-cotton results embed `2486(I)/2025` inside `source_document`; most other results have no SRO. |
| Source URL | Nullable. Present for many TIPP/product checks but absent for arithmetic, weights and some source-backed checks. |
| Source page | Nullable. Present for tariff support and raw-cotton SRO checks; absent for many legal checks. |
| Legal cutoff date | Not present in the response schema or loaded rule set. |

The product-requirements JSON has `retrieval_date: 2026-07-23`, but retrieval
date is not a legal cutoff date and the loader ignores it.

Recommended result provenance:

```text
source_document
sro_number
source_url
source_page or source_locator
issuing_authority
effective_date
legal_cutoff_date
rule_data_version
validation_status
```

The result builder should refuse to emit `passed` for a legal check if required
provenance is missing.

Risk level: **high.**

## 13. Rule-loader cache review

Two indefinite in-process caches exist:

1. `load_compliance_rules()` caches the parsed rule set.
2. `get_compliance_rule_engine()` caches an engine holding that rule set.

If a JSON file changes while the API is running, the engine continues using
old data. Clearing only the loader cache is insufficient because the cached
engine still holds the old `ComplianceRuleSet`.

Current ways to refresh:

- restart every API worker; or
- explicitly clear both caches in the correct deployment process.

There is no:

- file modification-time check;
- content checksum/version check;
- administrative reload endpoint;
- startup rule-version log;
- response field showing which rule version was used.

Recommended approach:

- calculate a combined SHA-256/version for the loaded JSON sources;
- store that version in the rule set and every response;
- load rules once at application startup;
- provide an explicit controlled reload operation, or restart on rule release;
- validate new rules fully before replacing the active rule set;
- never reload partially while requests are executing.

Risk level: **medium operationally; high when regulations change frequently.**

## 14. Problems and risk levels

| Finding | Risk | Why it matters |
|---|---|---|
| Null/unverified regulatory values can lead to overall pass | High | “Not found” is treated like “legally not required.” |
| Verification status is ignored | High | Portal limitations and uncertainty do not affect the outcome. |
| Complete legal provenance is missing | High | A decision cannot always be traced to an exact law, SRO, page and cutoff. |
| Legal cutoff date is absent | High | The user cannot tell which date's law was applied. |
| Product-specific executable rules are hardcoded | High for expansion | New product types require code changes and can drift from JSON. |
| `approval_required` is never checked | High | A populated approval rule would currently have no effect. |
| Cached engine can keep outdated rules | Medium/High | Running workers may apply superseded JSON until restarted. |
| `rule_engine.py` has too many responsibilities | Medium | Review and safe change become difficult. |
| JSON is stored as arbitrary dictionaries using `Any` | Medium | Misspelled or wrong-type fields can be silently ignored. |
| General required documents are duplicated in Python and JSON | Medium | The two sources can disagree. |
| Unknown non-China destination passes if any COO is uploaded | Medium | Presence does not prove the correct destination-specific certificate. |
| Document checks verify names only | Medium | Uploaded content, issuer, expiry and authenticity are not checked. |
| Currency scale and rounding policy are undefined | Medium | Financial tolerance may be wrong for some currencies. |
| Tests call the route function, not the full ASGI HTTP stack | Medium | Serialization, HTTP 422 shape and dependency behavior need an integration test. |
| One-line invoice model only | Low for Phase 1 | It cannot yet sum multiple invoice lines. |

## 15. Test review

### Original tests

The original seven tests covered:

- valid cotton T-shirt;
- invoice calculation mismatch;
- gross weight below net weight;
- raw cotton missing SBP deposit proof;
- raw cotton missing phytosanitary certificate;
- unsupported PCT;
- missing packing list.

### Tests added during this review

- invalid PCT code;
- zero quantity;
- negative quantity;
- decimal rounding within one-cent tolerance;
- missing Form-E;
- China shipment missing certificate of origin;
- raw cotton exactly at 180 days;
- raw cotton after 180 days;
- raw cotton missing letter-of-credit date;
- unknown destination without origin certificate;
- null regulatory requirement safety;
- duplicate and aliased document names.

### Current test result

```text
18 passed, 1 xfailed, 1 warning
```

The expected failure is intentional:

```text
null regulatory requirements currently produce not_applicable,
not manual_review
```

The warning is from the existing `langchain-community` PDF loader imported by
the application; the Phase 1 compliance engine does not use LangChain or an
LLM.

### Important tests still missing

- zero and negative unit price;
- difference exactly `0.01` and just above `0.01`;
- extremely large Decimal values;
- excessive decimal places;
- missing each required input field;
- shipment date earlier than letter-of-credit date;
- raw cotton missing SBP confirmation;
- raw cotton missing irrevocable LC document;
- conditional import permit absent and present;
- known non-China destination with and without a required preference scheme;
- wrong type inside `uploaded_document_types`;
- document content/issuer/expiry verification;
- source JSON missing, malformed or wrong top-level type;
- duplicate PCT records in JSON;
- unsupported product with an arithmetic failure;
- approval requirement populated in JSON;
- unverified `export_status`;
- missing provenance blocking a pass;
- rule cache refresh after JSON changes;
- concurrent requests during rule reload;
- FastAPI HTTP-level request/response and 422/500 tests;
- multiple invoice lines when that schema is introduced.

## 16. Recommended change sequence

No implementation change was made during this review. After approval, the
recommended order is:

1. Make null, unknown and unverified requirements fail closed to
   `manual_review`.
2. Add explicit legal provenance and legal cutoff fields to rule data and
   results.
3. Define Pydantic models for the regulatory JSON instead of using arbitrary
   dictionaries.
4. Add a rule-source version/checksum and explicit cache lifecycle.
5. Separate the 839-line engine into the proposed modules.
6. Move general document and product-specific conditions into declarative,
   validated rule records.
7. Keep raw-cotton special code temporarily, then migrate it to the same rule
   schema once the schema is proven.
8. Add HTTP integration tests and the remaining safety tests.

## 17. Approval boundary

This report does not refactor or alter the current compliance implementation.
Only the requested tests were added.

Implementation should remain unchanged until the proposed safety behavior and
file split are approved.
