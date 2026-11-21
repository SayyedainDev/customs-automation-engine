---
id: 019ab100-0000-7000-8000-000000000031
---

# Hybrid Invoice and Packing Extraction

PROVIDES zero-token deterministic header and coordinate-table extraction for commercial invoices and packing lists with bounded optional gap-fill
SO THAT multi-line shipment evaluation
CAN materialize structured candidate fields while keeping language-model calls optional and auditable.

Governs fail-closed abstention under [15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md).

## Assertions

### Scenarios

- Given an embedded-text commercial invoice containing labeled headers and line items, when hybrid extraction runs without a Groq client, then header fields and table rows resolve locally with regex provenance and zero LLM calls ([test](tests/test_hybrid_extraction.scenario.l1.py))
- Given an embedded-text packing list with labeled totals and package items, when hybrid extraction runs without a Groq client, then packing totals and items populate deterministically while missing fields remain marked for manual review ([test](tests/test_hybrid_extraction.scenario.l1.py))
