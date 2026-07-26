# Export Policy Manual Validation Report

Generated: 2026-07-23
Scope: Seven pages previously flagged by OCR review

## Outcome

- Flagged pages visually compared with original PDF images: **7 of 7**
- Original OCR text files modified: **0**
- Validated text derivatives created: **8**
- Handwritten/stamped SRO identifiers resolved: **3**
- Financial and shipment conditions manually recovered: **SRO 2486(I)/2025 page 1**
- Page-image fields that remain unclear for legal normalization: **2 SRO 629 code values**
- Predecessor rule text not independently validated: **SRO 433(I)/2024 text affected by SRO 1021(I)/2024**
- Embeddings, database insertion and compliance execution: **not performed**

All original OCR `.txt` files were checksum-tested after validation and remain byte-for-byte unchanged. Corrected text is stored separately in `validated_text/`.

## Page-by-page comparison

| Document | Page | Result | Corrections or findings |
|---|---:|---|---|
| SRO 1087(I)/2023 | 1 | Manually verified | Confirmed issue date `18 August 2023`, paragraph `7`, sub-paragraphs `(6)` and `(7)`, Schedule II serial `8`, column `(4)`. Restored the phrase omitted by OCR: “excluding that manufactured in manufacturing bonds and export oriented unites in case of export to Afghanistan only”. “Unites” is preserved as printed. |
| SRO 629(I)/2024 | 1 | Manually verified | Handwritten SRO is `629(I)/2024`; handwritten date is `30 April 2024`. Schedule I serials `9`, `14` and `18` and their visible PCT lists were verified. |
| SRO 629(I)/2024 | 2 | Verified source text; intended code unclear | Appendix A serials and codes were verified. Appendix B serial `2` visibly prints old `2903.3950` and new **`2303.4910`**. The page does not visually support changing it to `2903.4910`. Because `2303.4910` may be an official typographical error, automatic use is blocked pending an authoritative corrigendum or confirmation. |
| SRO 629(I)/2024 | 6 | Verified source text; one intended code unclear | Serial `340` prints `4418.9200, 4418.8100, 4418.8200, 4418.8300, 4418.8900`. Serial `342` visibly prints `4418.9200, 4418.8100, 4413.8200, 4418.8300, 4418.8900`. The `4413.8200` value may be an official typographical error and is blocked from automatic normalization. |
| SRO 1021(I)/2024 | 1 | Manually verified | Handwritten SRO is `1021(I)/2024`; date is `11 July 2024`. Confirmed Schedule I, serial `19`, column `(4)` is omitted. The removed predecessor text came from SRO 433(I)/2024, whose original notification was not in this visual-validation set. |
| SRO 1727(I)/2025 | 1 | Manually verified | Handwritten SRO is `1727(I)/2025`; date is `8 September 2025`. Confirmed Appendix G serial ranges `174–175`, `181–186`, `189–198`, and omission of columns `(2)`, `(3)` and `(4)`. |
| SRO 2486(I)/2025 | 1 | Manually verified | Confirmed SRO, date, Schedule II serial `9`, column `(4)`, **1% SBP security deposit**, SBP confirmation before Customs with shipping documents, irrevocable buyer's letter of credit, shipment within **180 days thereof**, and proportional SBP forfeiture for quantity not shipped. |

## Important source conflicts

### SRO 629 page 2

The OCR was previously assumed to have changed `2903.4910` into `2303.4910`. Direct image review does not support that assumption: the original notification itself visibly prints `2303.4910`.

The validated data therefore records:

- Printed value: `2303.4910`
- Proposed/intended value: `2903.4910`
- Validation status: `unclear`
- Rule-execution status: blocked

No silent correction was made.

### SRO 629 page 6

Appendix J serial 342 visibly contains `4413.8200` inside an otherwise 4418-based sequence. It is retained as the printed value but marked `unclear` for current legal use. No silent replacement with `4418.8200` was made.

### SRO 1021 predecessor text

SRO 1021 omits Schedule I serial 19 column (4). A local consolidated EPO states that the removed text was inserted by SRO 433(I)/2024, but the original SRO 433 notification was not part of this visual-review set. The new omission is manually verified; the exact predecessor text remains `unclear` until the original SRO 433 is validated.

## Corrected text derivatives

The following directory contains one validated derivative for each of the eight amendments:

`regulatory_data/processed/commerce/export_policy/validated_text/`

For the seven flagged pages, the derivative contains manually transcribed text and a `manually_verified` label. Other pages are explicitly labelled `OCR_verified`; they were not incorrectly represented as manually reviewed.

## Structured amendment data

`export_policy_amendments.json` was rebuilt using a field-evidence structure. Each extracted field contains:

- `value`
- `source_page`
- `validation_status`
- `validation_note`

Allowed validation statuses are `manually_verified`, `OCR_verified` and `unclear`. Unclear values are preserved but blocked from legal execution.

## Current affected rules

`current_export_policy_rules.json` combines the base Export Policy Order 2022 with the current effect of the eight specified amendments.

- Resulting amendment-affected rule records: **48**
- SRO 629 code/description substitutions: **40**
- Other amendment-effect rules: **8**
- Textile-relevant rules in this set: **Angoor Adda route (conditional)** and **cotton/SBP conditions (direct)**

This JSON is deliberately limited to rules affected by these eight amendments. It is **not** a complete transcription of every rule in the 93-page base order or every historical EPO amendment. Rule execution remains blocked pending legal review and resolution of the unclear SRO 629 values.

## Legal validation boundary

Manual image comparison confirms what the reviewed source pages visibly state. It does not correct possible errors printed by the issuing authority and does not replace legal review. No embeddings, RAG ingestion, database insertion or compliance-rule execution was performed.
