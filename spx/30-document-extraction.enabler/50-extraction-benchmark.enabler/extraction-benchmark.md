---
id: 019ab100-0000-7000-8000-000000000034
---

# Extraction Approach Benchmark CLI

PROVIDES reproducible multi-approach extraction accuracy, token-budget, and scanned-OCR evaluation over labeled shipment bundles
SO THAT extraction quality changes
CAN be measured offline and in paced live runs against shared Pydantic field schemas.

Governs offline repeatability and CLI contract conformance under [18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md).

## Assertions

### Conformance

- The benchmark argument parser accepts `--variant` (`text`, `scanned`, `both`), `--estimate-tokens`, `--dry-run`, `--verify-offline`, and `--delay-seconds` flags cleanly ([test](tests/test_benchmark_cli.conformance.l1.py))
- Deterministic document loading, Approach B dry-run execution, token budget estimation, and scanned OCR cache path resolution produce structured scores over repository synthetic bundles without API credentials ([test](tests/test_benchmark_cli.conformance.l1.py))
