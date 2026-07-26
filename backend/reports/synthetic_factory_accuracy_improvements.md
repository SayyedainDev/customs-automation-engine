# Synthetic Factory — Accuracy Improvements

This report covers a live evaluation of all 14 synthetic scenarios in both
variants (28 runs) against the real HTTP API, the defects that run exposed, and
the remediation applied.

**Verification honesty note.** The Groq account exhausted its **daily token
quota (TPD 200,000; 198,829 used)** during the baseline run. 11 of the 28
baseline runs were therefore never served by the model and are recorded as
`BLOCKED`, not as defects. A full post-fix live rerun is blocked until quota
resets. Where a fix could not yet be confirmed end-to-end through the API, that
is stated explicitly below rather than implied.

---

## 1. Baseline (pre-fix, live)

`reports/synthetic_factory_baseline.json` / `.md`

| | Text | Scanned |
|---|---:|---:|
| Scenarios fully correct | **6 / 14** | **0 / 14** |
| Technical failures | 7 | 8 |
| Overall status accuracy | 100.0% | 16.67% |
| Line-item count accuracy | 100.0% | 16.67% |
| Exact field accuracy | 100.0% | 61.9% |
| Missing field rate | 0.0% | 31.75% |
| PCT-code accuracy | 100.0% | 16.67% |
| Item-match accuracy | 100.0% | 16.67% |
| Human-review accuracy | 85.71% | 16.67% |
| **False legal passes** | **0** | **0** |

Of the 28 runs: 6 fully correct, 11 `BLOCKED` (provider quota), 11 genuine
failures.

Note that where extraction *did* run on text PDFs, field accuracy was already
100% — the text-variant failures were whole-document losses, not wrong values.

---

## 2. Defects found

`reports/synthetic_factory_defect_register.json` / `.md`

| Family | Title | Category | Severity | Runs affected |
|---|---|---|---|---:|
| DEF-001 | Provider schema rejection collapses the whole document | `LLM_line_item_extraction` | critical | 4 |
| DEF-002 | OCR silently drops invoice product rows | `OCR` | high | 5 |
| DEF-003 | Field label absorbed into the extracted value | `LLM_header_extraction` | medium | 5 |
| DEF-007 | Upstream quota reported as malformed model output | `API_or_infrastructure` | high | all quota runs |
| BLOCKED | Not measured — provider refused the request | `API_or_infrastructure` | blocker | 11 |

### DEF-001 — root cause

Pydantic emits a JSON-Schema `pattern` containing a **negative lookahead**
(`^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$`) for the *string* branch of every `Decimal`
field. Groq's constrained decoder cannot compile lookarounds, so it rejected
every Phase 2C strict schema with HTTP 400 `pattern_unsupported_feature`
(**76 occurrences in one baseline run**). The request then fell back to
*unconstrained* `json_object` mode, where the model intermittently emitted empty
strings between array objects:

```
"line_items": [ {...}, "", {...}, "", {...} ]
```

Local Pydantic validation correctly refused that payload — but the cost was the
**entire document**, including the three lines extracted perfectly. Measured
directly against live Groq: 3 of 5 attempts produced this shape.

The defect was **not** limited to multi-line documents: `clean_raw_cotton`
(single line) failed the same way.

### DEF-002 — root cause

Tesseract ran with **PSM 6** ("assume a single uniform block of text"). An
invoice page is not a uniform block: it is a label–value header, a wide sparse
table, then a footer. PSM 6 discarded product rows whose columns are separated
by large horizontal gaps, so the model received a table header with **no rows
beneath it** and returned zero line items — while OCR confidence stayed **0.94**,
so the confidence gate could not see the loss.

The rendered image was verified visually: the row `1  Raw cotton, other
5201.0090  1000 KG  2.00  2000.00 …` is fully legible. This was configuration,
not image quality.

---

## 3. Fixes applied

| Defect | Fix | Regression test | Verification status |
|---|---|---|---|
| DEF-001 | Strip only lookaround/backref `pattern` keywords from the **provider-facing transport schema**, so strict `json_schema` mode is usable again. Local Pydantic validation unchanged. Plus staged per-line extraction as a fallback. | `tests/unit/test_groq_schema_compatibility.py` (6), `tests/unit/test_staged_multi_line_extraction.py` (10) | **Root cause verified fixed live** (controlled A/B below); full end-to-end rerun blocked by quota |
| DEF-002 | `ocr_page_segmentation_mode` 6 → **4** in `app/core/config.py`, `.env`, `.env.example` | `tests/integration/test_ocr_table_rows.py` (4, real Tesseract) | **Verified live** against real Tesseract on all 14 scanned fixtures: **62/62** expected tokens recovered vs 52/62 |
| DEF-007 | New `StructuredExtractionProviderUnavailableError` (subclass, backward compatible); 429/5xx → HTTP 503 with an accurate message instead of "malformed structured data" | `tests/unit/test_provider_error_classification.py` (7) | **Verified live** — API now returns `503 … provider is unavailable or rate limited; no extraction was performed` |

### DEF-001 live verification — controlled A/B against the real provider

A schema rejection is returned by Groq *before* generation begins, so schema
acceptance can be tested without spending generation tokens. Both requests were
issued in the same second, against the same live account, under the same
exhausted quota:

| Transport schema | Live provider response |
|---|---|
| **Old** (lookahead `pattern` kept) | **HTTP 400 — `pattern_unsupported_feature`, schema rejected** |
| **New** (unsupported `pattern` stripped) | **HTTP 429 — quota** |

Reaching the quota check means the new schema **passed provider schema
validation**, whereas the old one never did. Strict `json_schema` mode is
therefore usable again, which structurally removes the unconstrained
`json_object` fallback in which the malformed `[obj, "", obj]` arrays occurred.

### Why stripping the schema pattern does not weaken validation

The `pattern` is a *hint sent to the provider*. The untouched Pydantic model
still enforces the real constraint when the response is validated locally, so
nothing is accepted that would not have been accepted before. Keeping the hint
made the provider refuse the schema outright and fall back to **unconstrained**
decoding, which is strictly less safe. Tests
`test_local_validation_is_not_weakened_by_schema_stripping` and
`test_line_items_array_of_non_objects_is_still_rejected` pin this.

### PSM 4 rather than PSM 3

Both recover 62/62 tokens. PSM 4 additionally keeps a label and its value on one
line (`Exporter Punjab Textile Exporters Consortium`), whereas PSM 3 emits the
label column and value column as separate blocks and forces the extractor to
re-pair them positionally — which is what produced DEF-003-style label bleeding.

---

## 4. Measured OCR improvement (independent of Groq quota)

Expected tokens recovered from the real scanned fixtures, real Tesseract:

| Segmentation mode | Tokens recovered | Single-line invoice row |
|---|---:|---|
| PSM 6 (baseline) | 52 / 62 | **lost entirely** |
| PSM 3 | 62 / 62 | recovered, labels split from values |
| **PSM 4 (applied)** | **62 / 62** | recovered, labels paired with values |

---

## 5. Test results

- Full suite: **227 passed, 5 failed**.
- The 5 failures are **pre-existing and unrelated**, confirmed present before
  this work: 3 assert the string `"invalid structured invoice data"`, which does
  not exist anywhere in current application code (it raises `"Groq JSON failed
  local Pydantic validation."`), and 2 exercise the RAG embedding/index stack.
- Baseline before this work was 6 pre-existing failures; one flaky
  explanation test now passes.
- New tests added: **27** (6 schema compatibility, 10 staged extraction, 7
  provider-error classification, 4 OCR integration) plus 6 manifest-independence.
- `mypy`: clean on all changed modules.
- `compileall`: clean.

---

## 6. Not fixed / blocked

| Item | Status | Reason |
|---|---|---|
| Full post-fix live rerun of 28 runs | **Blocked** | Groq daily token quota exhausted (~150k tokens needed). No application change can make an unserved request produce a result. |
| DEF-001 end-to-end live confirmation | **Blocked** | Same. Schema-level contract is test-verified. |
| DEF-003 label bleeding | **Partially addressed** | PSM 4 pairs labels with values on one line, which removes the ambiguity that caused it. Live re-measurement blocked by quota. |
| Supporting-document verification (Phase 10) | **Open — audited, not implemented** | See §7. |

---

## 7. Supporting-document handling — audit result

Audited as required. Current behaviour is **(B): the workflow only receives the
document *name*.**

`additional_document_ids` is accepted by the API, stored in workflow state and
echoed into the final report (`document_ids.additional`), but it is **never
loaded, text-extracted, classified, or cross-checked**. Every document-presence
check — Form-E, certificate of origin, SBP deposit proof, SBP confirmation,
irrevocable LC, phytosanitary certificate, import permit — is driven purely by
the `additional_uploaded_document_types` **strings**.

**Consequence:** a caller can assert `"form_e"` without uploading anything and
the engine will record the Form-E requirement as satisfied. This is a genuine
integrity gap between *document claimed* and *document verified*, and it is the
single most valuable remaining piece of work in this area.

It is reported here rather than partially implemented, because a half-built
verification path that silently accepts unverified documents would be more
dangerous than the current, clearly-documented limitation.

---

## 8. Critical safety metric

**False legal passes: 0** — across all 28 baseline runs, and across every
scenario in the offline deterministic suite. No shipment received a legal pass
it did not earn. Every failure mode observed was *fail-closed*: the engine lost
a document or routed to manual review rather than approving something unproven.
