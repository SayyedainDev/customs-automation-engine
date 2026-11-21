---
id: 019ab100-0000-7000-8000-000000000072
---

# Advisory Pipeline and Conversation Memory

PROVIDES end-to-end orchestration of input checks, scope classification, deterministic tariff and textile checklist tools, regulatory retrieval, conversation persistence, and message feedback
SO THAT HTTP advisory consumers
CAN converse with CACE safely with deterministic tool outputs, citation tracking, and honest unavailability handling when no LLM credential is configured.

Governs fail-closed generation degradation under [spx/15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md).

## Assertions

### Scenarios

- Given a prompt-injection or out-of-scope question, when posted to the customs advisory pipeline without external credentials, then CACE returns `REFUSAL` or `OUT_OF_SCOPE` deterministically with persisted conversation history ([test](tests/test_advisory_pipeline.scenario.l1.py))
- Given a customs tariff or textile export checklist question with seeded SQLite tariff data and absent Groq credentials, when posted without streaming, then deterministic tariff lookup or checklist data populates alongside honest generation-unavailable handling and feedback persistence succeeds ([test](tests/test_advisory_pipeline.scenario.l1.py))
