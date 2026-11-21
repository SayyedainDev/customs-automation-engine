# Authoritative Compliance and Fail-Closed AI Safety

Deterministic engines own every shipment compliance verdict, document-sufficiency judgment, and statutory customs duty calculation in CACE. Language models operate strictly as bounded extraction gap-fillers and non-authoritative narrative assistants whose outputs fail closed into refusal, downgrade, or human review whenever safety, provenance, or grounding checks fail.

## Rationale

Customs export compliance cannot tolerate nondeterministic legal verdicts, hallucinated duty rates, or prompt-injection overrides hidden inside uploaded shipping documents. Separating deterministic rule evaluation from bounded language-model prose ensures audit repeatability, exact statutory `Decimal` arithmetic, and cryptographic traceability across human reviews.

## Product properties

1. No language model, agent narration layer, or user prompt override alters a deterministic compliance status or bypasses a pending human review requirement.
2. Every statutory customs duty, tax base, and tariff rate resolves deterministically with exact `Decimal` rounding or refuses resolution when an HS/PCT code is unconfirmed or flagged for review.
3. Advisory responses, agent audit notes, and extracted supporting fields reject prompt-injection markers, leaked system prompts, unretrieved SRO citations, and ungrounded numeric claims.

## Verification

### Testing

- ALWAYS: prompt-injection markers hidden in chat questions or uploaded documents are detected after Unicode normalization and fail closed before reaching generation or changing deterministic outcomes ([compliance])
- ALWAYS: customs advisory outputs refuse prompt leaks and scope drift, strip ungrounded numbers, and escalate failed regeneration into downgrade or refusal ([compliance])
- ALWAYS: broker and auditor validation rejects any agent report that contradicts the frozen deterministic status or cites an SRO absent from retrieved evidence ([compliance])
- NEVER: missing or unconfirmed HS/PCT rates without caller-supplied customs duty percentages fall back to guessed rates in duty calculation ([compliance])

### Audit

- ALWAYS: compliance status transitions in human-review workflows recompute deterministically from updated shipment inputs rather than accepting free-text overrides ([audit:deterministic-review-recomputation])
- ALWAYS: finalized customs-audit workflows append SHA-256 hash-chained audit events preserving actor identity, node name, and prior state hashes ([audit:immutable-audit-hash-chain])
