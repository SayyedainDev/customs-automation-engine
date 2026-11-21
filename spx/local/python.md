# Python Code Local Overlay

This overlay routes Python authoring and auditing workflows to CACE's governing architecture decision and toolchain configuration.

- Governing Architecture and Evidence Decision Record: [spx/18-architecture-and-evidence.adr.md](spx/18-architecture-and-evidence.adr.md)
- Root tool configuration: `pyproject.toml`
- Application package root: `backend/app/`
- Test infrastructure package root: `backend/cace_testing/`
- Lint command: `ruff check --config pyproject.toml backend/cace_testing spx`
- Application type check: `mypy --config-file backend/mypy.ini backend/app`
- Test and infrastructure type check: `mypy --config-file pyproject.toml backend/cace_testing spx`
