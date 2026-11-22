# Security & AI Safety Policy

## Trust Boundaries & Built-In Controls

CACE is engineered around a **deterministic-first, fail-closed** trust model for trade compliance:

1. **Authoritative Verdicts Are Non-LLM**: Compliance statuses (`passed`, `failed`, `manual_review`) and FY2026–27 Pakistan cascading customs duty calculations are computed solely in deterministic Python (`Decimal` arithmetic, validated rule tables). Language models, LangGraph agent narratives, and retrieved PDF text cannot mutate or override deterministic compliance verdicts.
2. **Prompt-Injection & Output Guardrails**: User messages, retrieved regulatory passages, and uploaded document text are normalized with Unicode NFKC and zero-width character stripping before prompt-injection detection, scope enforcement, SRO citation verification, and numeric grounding validation.
3. **Upload & Filesystem Safety**: Uploaded PDFs enforce bounded streaming size limits (`CUSTOMS_MAX_UPLOAD_SIZE_MB`) and strict canonical path containment within the configured upload root before reading or preview rendering.
4. **Immutable Audit Trail**: Human-in-the-loop review corrections modify structured shipment inputs and append a new immutable audit revision (`Revision N+1`) recorded on a SHA-256 hash-chained audit ledger rather than overwriting prior revisions.
5. **Secrets Hygiene**: Provider keys (`GROQ_API_KEY`) and database credentials are read from environment configuration (`SecretStr`), never committed to Git, and never echoed in API responses or logs.

## Known Prototype Security Boundary

- **Authentication & Multi-Tenancy**: CACE currently operates in **single-user prototype mode**. Account-based authentication, multi-tenant authorization (RBAC), and per-user document isolation are intentionally not implemented yet. Do not expose an unauthenticated instance directly to the public internet without placing it behind an authenticating reverse proxy / IAM layer.

## Reporting a Vulnerability

If you discover a security vulnerability or guardrail bypass:

1. **Do not** open a public GitHub issue with exploit details.
2. Contact the repository maintainer privately via GitHub Security Advisories (`Security` → `Report a vulnerability`) or direct profile contact on [github.com/SayyedainDev](https://github.com/SayyedainDev).
3. Include reproduction steps, affected files/endpoints, and expected vs. observed behavior.
