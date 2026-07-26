# Hybrid extraction layer

Deterministic regex/coordinate extraction runs first and resolves most fields
for free. Only what it genuinely cannot resolve goes to Groq, in one small
targeted call per document.

## Why

The legacy path sent every document's full text to Groq, and on failure
escalated to a staged ladder (header → row discovery → one call per line item).
A single failing document could cost 5–10 calls, which exhausted a
200,000-token daily budget in a handful of test runs.

## Pipeline

```
PDF ─► text (PyMuPDF) ──────────────────────────► local, free
      └─ no text layer? ─► Tesseract OCR ───────► local, free
text ─► regex_extractor  ─► resolved fields ────► local, free
      └─ word coordinates ─► line-item table ───► local, free
unresolved fields ─► llm_gapfill ─► ONE Groq call, ≤2000 chars context
result ─► existing MultiLineInvoiceCandidates schema (unchanged)
```

## Measured coverage

Regex-only, across the 15-document fixture corpus, **zero tokens**:

| Variant | Present-field coverage |
|---|---|
| Text layer | **139/139 (100%)** |
| Scanned (real Tesseract OCR) | **139/139 (100%)** |

Line items reconstruct from PDF word coordinates — all six fields per row,
including 3-row multi-line invoices — with no LLM call at all.

Two coverage numbers are reported and the difference matters:

- **schema coverage** — resolved / all 20 schema fields. Low (~46%) on this
  corpus simply because these invoices do not print an NTN, ports, B/L, GD or
  Form-E number. A poor tuning signal.
- **present-field coverage** — resolved / fields whose label actually appears
  in the document. **Tune against this one.** A gap here is a pattern that
  failed on text that was really there.

## Running the coverage report

```bash
cd backend
python -m scripts.extraction_coverage_report            # text layer
python -m scripts.extraction_coverage_report --scanned  # runs real OCR first
python -m scripts.extraction_coverage_report --json     # machine-readable
```

Reading it:

```
field                         resolved  present  status
invoice_number                      15       15  OK
exporter_ntn                         0        0  not printed on any document
number_of_packages                   0        1  ** PATTERN GAP: 1 missed **
```

- `OK` — every document that prints this field had it read.
- `not printed on any document` — nothing to fix; the corpus lacks the field.
- `** PATTERN GAP **` — a real miss. This is the actionable line.

## Adding a field pattern

1. Add patterns to `FIELD_PATTERNS` in `app/services/extraction/patterns.py`.
   Prefer `_label_value(labels, value_shape, note)`, which builds all three
   layouts (value on next line, value after `:`, value after whitespace).
2. Add a normaliser to `FIELD_NORMALISERS` if the value needs cleaning. **The
   normaliser is the field's shape contract** — it is also what validates
   anything the LLM returns for that field.
3. If the field is a legal or financial figure, add it to
   `LABEL_REQUIRED_FIELDS` so an unlabelled match can never resolve it.
4. If it is definitionally digits-only, add it to `DIGITS_ONLY_FIELDS`.
5. Add a presence probe in `scripts/extraction_coverage_report.py` so the
   report can tell "absent" from "missed".
6. Re-run the coverage report. It costs nothing.

## Rules that are not negotiable

**A pattern may never guess.** A wrong value silently corrupts a compliance
verdict; `None` correctly escalates to the LLM. Concretely:

- **Labels are consumed atomically.** The label group uses `(?>...)`. This is
  load-bearing: without it the engine consumes "Buyer / Consignee", fails the
  same-line form at the newline, backtracks to "Buyer", and captures
  "/ Consignee" as the consignee name. That exact false value was observed
  against real fixtures, as was `country_of_destination = "Country"` from
  "Destination Country".
- **OCR tolerance widens matching, never values.** An NTN captured as
  `123456O-7` is reported as needing confirmation, not rewritten to `0`.
  Rewriting would invent a legal identifier the page does not show.
- **Ambiguous dates escalate.** `05/03/2026` is valid as both DD/MM and MM/DD.
  Assuming the local convention could shift a shipment date by months and flip
  a 180-day SRO deadline check, so it is returned unresolved with a reason.
  A day > 12, or a spelled-out month, is unambiguous and is accepted.
- **Financial and legal figures require a label.** An unlabelled number that
  looks right is exactly the guess this layer exists to avoid.
- **Unmodelled table columns are retained.** Dropping them let values under
  "Net Wt (KG)" be reassigned to the nearest modelled column — "Line Total" —
  overwriting a financial figure with a weight.
- **Model output is re-validated.** Every gap-fill value goes through the same
  normaliser a regex capture must satisfy. Anything that fails is discarded and
  the field stays missing.

## Gap-fill cost control

- **One call per document**, covering all unresolved fields together.
- **Never the full document.** Context is ±400 chars around each unresolved
  field, merged and hard-capped at 2,000 characters.
- **Candidates are the cheapest path.** When regex found several possibilities,
  the model receives the short list and picks one — no document text is sent
  for that field at all.
- **The system prompt is a module-level constant** and must stay byte-identical
  so Groq's automatic prefix caching applies. Never f-string it, never include
  a field list, document name or timestamp.

`telemetry.llm_calls` should be 0 or 1 per document. Anything higher is
reported as a defect — it means the retry ladder has reappeared.

## What already existed

Two pieces of the original plan were already implemented elsewhere and were
**not** duplicated:

- **Per-document extraction cache** —
  `app/services/extraction/cache_fingerprint.py` and `cache_lock.py`, keyed on
  document text digest, model, prompt version, schema version, OCR settings,
  application code version, and (as of the hybrid wiring below) the active
  `EXTRACTION_MODE`. Wired into `multi_line_shipment_service`.
- **429 cascade guard** — `except StructuredExtractionProviderUnavailableError:
  raise` in the extraction services stops a quota refusal escalating to the
  staged ladder. Covered by `tests/unit/test_structured_extraction_cache.py`
  (28 tests).

## Live wiring: `EXTRACTION_MODE`

`app/core/config.py` exposes `EXTRACTION_MODE` (`legacy` | `hybrid`, default
`hybrid`). `multi_line_shipment_service.py`'s `_extract_invoice_candidates` /
`_extract_packing_candidates` branch on it:

- **`hybrid`** (default): regex/table extraction runs first
  (`extract_document` with PyMuPDF word coordinates, captured at ingest time
  by `pdf_extractor.extract_page_word_coordinates`). If a line-item table
  reconstructs, only the unresolved *header* fields relevant to that document
  type (`hybrid_orchestrator.invoice_gapfill_fields()` /
  `packing_gapfill_fields()`) go to one combined gap-fill call
  (`app/services/extraction/groq_gapfill.run_gapfill`, which reuses
  `structured_extraction_service.extract_structured_model_from_text` for the
  actual Groq transport/error-classification — no new client code). If no
  table reconstructs at all (unrecognized layout, or a document ingested
  before word-coordinate capture), it falls back to the one existing
  full-document call instead of forcing manual review. **The staged ladder is
  unreachable in this mode** — a 429/provider error on either path leaves
  fields unresolved rather than cascading.
- **`legacy`**: unchanged full-document single-shot call with the staged
  per-line ladder as a fallback on validation failure, kept for comparison
  and debugging.

Every hybrid-mode extraction makes **0 or 1** Groq calls per document, and
records `DocumentTelemetry` (fields_from_regex, fields_from_llm, llm_calls,
notes) alongside the cached candidates in `structured_data[...]["telemetry"]`.

Known limitation: documents ingested before word-coordinate capture shipped
have no word coordinates and will always take the full-document fallback
branch in hybrid mode until re-uploaded; there is no backfill script.

## Status

The regex layer, gap-fill assembly/validation/network call, telemetry,
coverage report, packing-list mapper and schema mapping are implemented,
wired into the live multi-line route, and unit-tested. `EXTRACTION_MODE`
defaults to `hybrid`; `legacy` remains fully available for comparison and
debugging.
