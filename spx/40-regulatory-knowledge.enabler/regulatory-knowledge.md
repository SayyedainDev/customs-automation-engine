---
id: 019ab100-0000-7000-8000-000000000040
---

# Regulatory Knowledge Retrieval

PROVIDES hybrid BM25 lexical, dense vector, Reciprocal Rank Fusion, and reranked retrieval with parent-section expansion and source provenance classification
SO THAT audit workflows and customs advisory consumers
CAN ground regulatory explanations in verifiable Pakistan Customs sources without hallucinating absent citations.

Governs fail-closed `evidence_not_found` reporting and provider injectability under [15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md) and [18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md).

## Assertions

### Scenarios

- Given indexed regulatory child and parent chunks in SQLite, when hybrid search executes with injected offline embedding and reranker providers, then verified matching parent sections return ranked with transparent `degraded_mode` telemetry ([test](tests/test_regulatory_retrieval.scenario.l1.py))
- Given an unrelated query failing the deterministic lexical relevance gate or filtered away by `verified_only`, when search runs, then retrieval returns `evidence_not_found` with zero fabricated results ([test](tests/test_regulatory_retrieval.scenario.l1.py))
- Curated rule summaries and official government instruments classify deterministically into distinct `source_kind` provenance categories ([test](tests/test_regulatory_retrieval.scenario.l1.py))
