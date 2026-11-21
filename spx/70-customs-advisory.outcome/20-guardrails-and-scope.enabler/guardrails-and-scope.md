---
id: 019ab100-0000-7000-8000-000000000071
---

# Input Guard, Two-Signal Scope Router, and Output Guard

PROVIDES Layer 1 Unicode-normalized injection and length enforcement, Layer 2 deterministic two-signal customs scope routing, and Layer 4 forbidden-claim, ungrounded-number, and prompt-leak verification
SO THAT the customs advisory pipeline
CAN reject adversarial inputs and unsafe model outputs deterministically before trusting or returning generated text.

Enforces safety boundaries from [spx/15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md).

## Assertions

### Compliance

- ALWAYS: every injection probe in the curated golden evaluation dataset and zero-width obfuscated variants triggers `injection_detected` in Layer 1 input checks ([test](tests/test_golden_scope_and_injection.compliance.l1.py))
- ALWAYS: every fast-resolved in-scope and out-of-scope probe in the golden dataset routes to `in_scope=True` or `in_scope=False`, while conflicting customs-anchor and out-of-domain-intent prompts escalate to `None` instead of auto-accepting ([test](tests/test_golden_scope_and_injection.compliance.l1.py))
- ALWAYS: Layer 4 output validation refuses system-prompt leaks and scope drift, rejects general-mode numbers, ungrounded grounded-mode digits, clearance guarantees, and banned legalese, and escalates failed retries from `regenerate` to `downgrade` ([test](tests/test_golden_scope_and_injection.compliance.l1.py))
