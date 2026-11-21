---
id: 019ab100-0000-7000-8000-000000000082
---

# MCP Server Tool Surface

PROVIDES Model Context Protocol tool wrappers for exact tariff lookup, FY2026-27 cascading duty calculation, deterministic textile export checklists, and hybrid regulatory evidence search
SO THAT MCP-compatible external AI clients
CAN invoke CACE's authoritative deterministic engines directly without custom HTTP glue.

Governs tool-contract fidelity and deterministic isolation under [spx/15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md) and [spx/18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md).

## Assertions

### Conformance

- The MCP server registers and executes all four declared tools (`lookup_pakistan_tariff`, `calculate_pakistan_customs_duties`, `get_textile_export_checklist`, `search_pakistan_customs_regulations`) returning structured dictionary contracts matching CACE domain schemas ([test](tests/test_mcp_tools.conformance.l1.py))
