---
id: 019ab100-0000-7000-8000-000000000051
---

# Deterministic Textile Compliance Rule Engine

PROVIDES repeatable checks for required fields, arithmetic consistency, weights, general export documents, and per-PCT product rules across supported textile codes
SO THAT shipment review services
CAN produce deterministic `passed`, `failed`, or `manual_review` compliance responses with regulatory rule provenance.

Governs complete rule-set coverage and legal provenance gating under [15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md).

## Assertions

### Mappings

- Every supported PCT code whose rule entry carries verified TIPP or source-page locator provenance evaluates deterministically to `passed` when arithmetic, weight bounds, and required documents hold, while entries lacking source-page or locator provenance fail closed to `manual_review` ([test](tests/test_compliance_rules.mapping.l1.py))
- Line-total mismatches and inverted net/gross weights across every supported PCT code map deterministically to `failed` status without advancing to clearance ([test](tests/test_compliance_rules.mapping.l1.py))
- Unsupported PCT codes map deterministically to `manual_review` with `supported_product` equal to `False` ([test](tests/test_compliance_rules.mapping.l1.py))
