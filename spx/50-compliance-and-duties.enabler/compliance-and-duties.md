---
id: 019ab100-0000-7000-8000-000000000050
---

# Textile Compliance Rules and Statutory Duty Engine

PROVIDES deterministic Pakistan textile compliance evaluation and FY2026-27 cascading tax calculation over validated PCT schedules
SO THAT pre-submission export reviews and external API or MCP callers
CAN obtain exact auditable pass/fail verdicts and statutory landed-cost figures without LLM inference.

Governs deterministic authority and fail-closed unknown tariff handling under [15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md).

## Assertions

### Audit

- ALWAYS: compliance evaluations and duty schedules resolve solely from validated rule JSON checksums, statutory PCT tables, or caller-supplied percentages ([audit:deterministic-compliance-sources])
- NEVER: missing or unconfirmed statutory rates infer approximate percentages outside explicit caller overrides ([audit:no-estimated-duty-rates])
