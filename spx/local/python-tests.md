# Python Test Standards Local Overlay

This overlay routes Python test authoring and auditing skills to CACE's Python test infrastructure and verification commands.

- Governing Architecture and Evidence Decision Record: [spx/18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md)
- Governing Safety Product Decision Record: [spx/15-authoritative-safety.pdr.md](spx/15-authoritative-safety.pdr.md)
- Reusable harnesses: `backend/cace_testing/harnesses/`
- Hypothesis domain generators: `backend/cace_testing/generators/`
- Discovery-only pytest root: `spx/conftest.py`
- Spec Tree Python test suite: `pytest -c pyproject.toml spx`
- Combined legacy and Spec Tree suite: `pytest -c pyproject.toml`
