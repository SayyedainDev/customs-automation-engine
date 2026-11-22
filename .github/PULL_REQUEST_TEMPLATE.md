## Summary

- What problem does this change solve?
- Key design decisions or trade-offs:

## Safety & Architectural Impact

- [ ] Deterministic compliance verdicts and `Decimal` duty math remain strictly non-LLM.
- [ ] Fail-closed guardrails (citation checks, prompt-injection filters, upload/path containment) are preserved or strengthened.
- [ ] Any new external dependency or environment flag is documented in `backend/.env.example`.

## Verification Evidence

- [ ] `REGULATORY_ENABLE_REAL_MODELS=false LANGGRAPH_CHECKPOINT_BACKEND=memory pytest -c pyproject.toml`
- [ ] `ruff check --config pyproject.toml backend/cace_testing spx`
- [ ] `mypy --config-file pyproject.toml backend/app && mypy --config-file pyproject.toml backend/cace_testing spx`
- [ ] `npx --yes @outcomeeng/spx@0.9.0 config validate && npx --yes @outcomeeng/spx@0.9.0 validation markdown spx/`
- [ ] `npm --prefix frontend run build`
