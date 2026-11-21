---
id: 019ab100-0000-7000-8000-000000000032
---

# Supporting Document Classification and Extraction

PROVIDES deterministic-first parsing, field normalization, and bounded snippet extraction for Form-E and Certificate of Origin supporting documents
SO THAT compliance evaluators
CAN verify supporting instrument consistency without accepting unverified text claims.

Governs non-authoritative extraction boundaries under [15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md).

## Assertions

### Scenarios

- Given a Form-E or Certificate of Origin text bundle with controlled labels, when deterministic supporting extraction runs offline, then normalized metadata fields resolve with source spans and zero compliance status verdicts ([test](tests/test_supporting_hybrid.scenario.l1.py))
- Given unresolved supporting fields, when bounded gap-fill snippet selection runs, then only compact label-adjacent fragments are prepared and conflicting field labels never overwrite resolved deterministic values ([test](tests/test_supporting_hybrid.scenario.l1.py))
