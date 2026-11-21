---
id: 019ab100-0000-7000-8000-000000000081
---

# HTTP REST and Streaming API Surface

PROVIDES versioned FastAPI routers, request correlation and latency headers, health diagnostics, and OpenAPI schema discovery
SO THAT the web console and API integrators
CAN interact with CACE endpoints deterministically with observable request IDs and timing headers.

Governs boundary contract conformance under [spx/18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md).

## Assertions

### Conformance

- Health probes (`/health`, `/health/database`, `/health/extraction-cache`), root discovery (`/`), and `X-Request-ID` / `X-Process-Time-Ms` observability middleware conform to HTTP response header contracts ([test](tests/test_http_surface.conformance.l1.py))
- OpenAPI schema generation exposes all public CACE routers (`/documents/upload`, `/api/v1/compliance/calculate-duties`, `/api/v1/customs-audit/workflows`, `/api/v1/customs-chat/messages`, `/api/v1/regulatory-evidence/search`, `/api/v1/shipment-search/search`) while excluding internal schema-hidden routes ([test](tests/test_http_surface.conformance.l1.py))
