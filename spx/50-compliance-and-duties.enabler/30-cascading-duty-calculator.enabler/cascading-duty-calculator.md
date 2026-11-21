---
id: 019ab100-0000-7000-8000-000000000052
---

# FY2026-27 Cascading Duty and Tax Calculator

PROVIDES exact `Decimal` `ROUND_HALF_UP` cascading Pakistan Customs duties (CD, ACD, RD, FED, DPV, ST, VAT, Section 148 WHT) and tariff rate resolution
SO THAT line-item cost breakdowns, API endpoints, and MCP tools
CAN guarantee exact penny-rounded tax compounding without binary floating-point drift.

Governs statutory arithmetic invariants and unconfirmed rate rejection under [15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md).

## Assertions

### Properties

- Compounded Sales Tax Base (`V + CD + ACD + RD + FED`), Compounded Income Tax Base (`DPV + ST + VAT`), Total Landed Cost conservation, and S.R.O. 1063(I)/2026 default ACD slab threshold (`2%` below `20%` CD, `6%` at or above `20%` CD) hold with exact `Decimal` `ROUND_HALF_UP` across all positive assessed values and rate combinations ([test](tests/test_cascading_duties.property.l1.py))
- Increasing assessed customs value preserves non-decreasing total duties and landed cost across the open positive domain ([test](tests/test_cascading_duties.property.l1.py))
- Arbitrary non-eight-digit strings fail closed to `None` in statutory rate resolution, while all verified textile PCT codes resolve to their exact statutory schedule entries ([test](tests/test_cascading_duties.property.l1.py))
