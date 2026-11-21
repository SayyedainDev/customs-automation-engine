# Architecture and Verification Evidence

CACE isolates business domains behind typed Pydantic contracts, SQLAlchemy ORM sessions, and Protocol or callable dependency seams so all deterministic compliance, extraction, retrieval, and workflow behavior executes in-process without framework mocks. Reusable Python test harnesses and Hypothesis generators live outside `spx/` under the repository test-infrastructure package, while every behavioral predicate and `assert` lives directly inside co-located Spec Tree test suites.

## Rationale

Executing real SQLite schemas, FastAPI routers, PyMuPDF rendering, and deterministic rule engines in `l1` tests prevents mock-induced drift while keeping execution fast and offline. Separating resource lifecycle harnesses (`cace_testing`) from test assertions (`spx/**/tests/`) preserves an unambiguous predicate seam and prevents duplicated fixture setup across nodes.

## Invariants

- Every Python test file under `spx/**/tests/` obeys `test_<subject>.<evidence>.<level>.py` naming and carries complete function parameter and return type annotations.
- Every property-based test delegates seed, run-count, deadline, and replay configuration to the harness while keeping the invariant lexically visible in the linked test function.
- Every domain constant, status enum, and schema vocabulary in tests imports from its owning application module rather than duplicating literals locally.

## Verification

### Audit

- ALWAYS: dependencies across external tool, database, embedding, reranker, and workflow boundaries accept dependency-injected callables or provider interfaces rather than requiring `unittest.mock.patch`, `Mock`, or `MagicMock` ([audit:no-framework-mocks])
- ALWAYS: test harnesses under `cace_testing` manage resource setup and teardown and return handles or raw observations without calling `assert` or exposing verdict helpers ([audit:predicate-seam-enforced])
- ALWAYS: configuration inputs and extracted candidate payloads validate through typed Pydantic models at package boundaries ([audit:typed-boundary-contracts])
- NEVER: test files declare local Hypothesis `@settings(...)`, hardcoded API credentials, or `sys.path` mutations ([audit:no-local-policy-or-syspath])
