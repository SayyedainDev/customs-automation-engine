---
id: 019ab100-0000-7000-8000-000000000060
---

# Resumable Customs Audit Workflow

WE BELIEVE THAT checkpointed multi-agent customs audit orchestration pairing deterministic rule evaluation with targeted human review, fail-closed safety guards, exportable audit dossiers, and historical shipment indexing
WILL catch cross-document shipment discrepancies before statutory filing without sacrificing auditability or allowing language-model status overrides
WE WILL KNOW WE SUCCEEDED WHEN customs reviewers inspect evidence-backed audit findings, submit targeted corrections or missing documents through durable revisions, export complete SHA-256 hash-chained dossiers, and retrieve comparable past shipments deterministically.

Governs authoritative compliance immutability under [spx/15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md) and injected graph dependencies under [spx/18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md).

## Assertions

### Audit

- ALWAYS: checkpointed audit state revisions append new immutable evaluation versions rather than mutating prior frozen deterministic verdicts ([audit:additive-audit-revisions])
- ALWAYS: completed audit workflows trigger best-effort shipment summary indexing without allowing indexing errors to corrupt compliance outcomes ([audit:non-blocking-history-indexing])
