---
id: 019ab100-0000-7000-8000-000000000063
---

# Historical Shipment Semantic Search

PROVIDES dense vector indexing of finalized audit summaries and deterministic semantic search with recency fallback on embedding mismatch
SO THAT compliance analysts and advisory tools
CAN locate comparable past shipments offline without relying on external vector databases or LLM synthesis.

Governs deterministic degradation and injectable embedding providers under [spx/18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md).

## Assertions

### Scenarios

- Given no indexed shipment summary rows, when shipment search runs, then status returns `no_shipments_indexed` with an empty result list ([test](tests/test_shipment_search.scenario.l1.py))
- Given finalized shipments indexed under a matching embedding provider, when search executes with that provider, then results return ranked by cosine similarity with explicit `semantic` or `semantic_degraded` retrieval mode ([test](tests/test_shipment_search.scenario.l1.py))
- Given indexed vectors generated under an incompatible model or dimension, when search runs, then results degrade deterministically to `recency_fallback` instead of comparing incompatible vectors ([test](tests/test_shipment_search.scenario.l1.py))
