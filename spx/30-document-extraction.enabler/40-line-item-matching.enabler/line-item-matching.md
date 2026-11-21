---
id: 019ab100-0000-7000-8000-000000000033
---

# Multi-Line Invoice and Packing Matching

PROVIDES deterministic priority-ordered one-to-one pairing between commercial invoice lines and packing list items
SO THAT cross-document quantity and weight verification
CAN pair corresponding shipment items without guessing ambiguous keys.

Governs deterministic pairing under [15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md).

## Assertions

### Mappings

- Unique key pairs across all four matching tiers (`LINE_REFERENCE`, `SKU_CODE`, `PCT_CODE`, and `PRODUCT_NAME`) map strictly in priority order to `MATCHED` `ItemMatch` pairs ([test](tests/test_item_matching.mapping.l1.py))
- Duplicate or absent matching keys across remaining items map deterministically to `INVOICE_ONLY` and `PACKING_ONLY` statuses without speculative pairing ([test](tests/test_item_matching.mapping.l1.py))
