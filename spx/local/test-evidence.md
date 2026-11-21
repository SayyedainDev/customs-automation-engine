# Test Evidence Local Overlay

This overlay routes shared test-evidence verification and authoring workflows to CACE's governing decisions and test infrastructure.

- Governing Product Decision Record: [spx/15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md)
- Governing Architecture and Evidence Decision Record: [spx/18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md)
- Python test infrastructure package: `backend/cace_testing/`
- Spec Tree discovery entrypoint: `spx/conftest.py`
- Canonical test runner: `pytest -c pyproject.toml spx`
- Full repository test runner: `pytest -c pyproject.toml`
- Markdown structure validation: `spx validation markdown spx/`
