---
id: 019ab100-0000-7000-8000-000000000080
---

# External Protocol and Operator Surfaces

PROVIDES FastAPI REST/SSE HTTP endpoints, a Model Context Protocol (MCP) tool server, and a browser-rendered React/Vite operator console over CACE domain capabilities
SO THAT human operators and external AI clients
CAN invoke customs compliance, duty calculation, extraction preview, regulatory search, and audit workflows through validated external contracts.

Governs thin boundary adapters and Pydantic validation under [spx/18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md).

## Assertions

### Audit

- ALWAYS: external API routes, MCP tool handlers, and frontend views delegate compliance verdicts and statutory calculations to domain services without embedding duplicate rule logic ([audit:thin-boundary-adapters])
