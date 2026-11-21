---
id: 019ab100-0000-7000-8000-000000000062
---

# Audit Agent Safety Guards and Dossier Export

PROVIDES untrusted-text injection scanning, strict Broker/Auditor report validation against retrieved SRO citations, and structured JSON/PDF audit dossier rendering
SO THAT customs reviewers and external audit consumers
CAN rely on tamper-evident audit packets that cannot override deterministic compliance or fabricate SRO citations.

Enforces compliance rules declared in [spx/15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md).

## Assertions

### Compliance

- ALWAYS: prompt-injection phrases inside untrusted document or evidence strings are flagged explicitly and never alter deterministic compliance decisions ([test](tests/test_safety_and_dossier.compliance.l1.py))
- ALWAYS: broker and auditor validation rejects any report whose observed deterministic status diverges from the engine or cites an SRO absent from retrieved evidence ([test](tests/test_safety_and_dossier.compliance.l1.py))
- ALWAYS: JSON and PDF audit dossier generation preserves the frozen verdict, shipment summary, line-item duty estimates, reviewer sign-off, and hash-chained event trail in valid PyMuPDF-readable output ([test](tests/test_safety_and_dossier.compliance.l1.py))
