---
id: 019ab100-0000-7000-8000-000000000030
---

# Document Extraction

PROVIDES deterministic-first structured extraction, confidence and OCR gating, cross-document item pairing, and offline accuracy benchmarking over ingested export documents
SO THAT downstream compliance rule engines and audit workflows
CAN consume provenance-backed shipment fields without relying on unverified language-model guesses.

Governs fail-closed provenance discipline under [15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md) and typed Pydantic boundaries under [18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md).

## Assertions

### Audit

- ALWAYS: extracted values retain source page, extraction method, confidence score, and `verified` or `manual_review` status through unified candidate materialization ([audit:uniform-candidate-provenance])
- NEVER: unresolved or low-confidence extracted fields escalate into `verified` compliance inputs without explicit human confirmation or bounded source evidence ([audit:no-unverified-escalation])
