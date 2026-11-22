# Contributing to CACE

Thank you for your interest in improving the Customs & Automated Compliance Engine (CACE).

## Non-Negotiable Engineering Invariants

1. **Deterministic Verdicts First**: Compliance statuses (`passed`, `failed`, `manual_review`) and cascading duty calculations must remain deterministic Python (`Decimal` arithmetic). LLMs and RAG outputs may explain or fill bounded extraction gaps, never decide legal compliance.
2. **Fail-Closed Defaults**: Missing statutory rates, conflicting regulatory evidence, unverified citations, or obfuscated prompt injections must escalate or refuse—never silently pass.
3. **Immutable Revisions**: Human corrections recompute a new revision rather than mutating Revision 1.
4. **Zero-Key Reproducibility**: Every automated test added to `backend/tests/` or `spx/` must run offline without live network calls or paid API keys.

## Local Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --index-url https://download.pytorch.org/whl/cpu torch
pip install -r backend/requirements-dev.txt
npm --prefix frontend ci
```

## Verification Checklist Before Opening a PR

Run the exact gates enforced in [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

```bash
# Backend + Spec Tree tests (offline CI mode)
REGULATORY_ENABLE_REAL_MODELS=false LANGGRAPH_CHECKPOINT_BACKEND=memory pytest -c pyproject.toml

# Lint & static typecheck
ruff check --config pyproject.toml backend/cace_testing spx
mypy --config-file pyproject.toml backend/app
mypy --config-file pyproject.toml backend/cace_testing spx

# Outcome Engineering Spec Tree validation
npx --yes @outcomeeng/spx@0.9.0 config validate
npx --yes @outcomeeng/spx@0.9.0 validation markdown spx/

# Frontend strict TypeScript check & production build
npm --prefix frontend run build
```

## Branch & Commit Hygiene

- Branch off `main` using descriptive prefixes (`feat/...`, `fix/...`, `docs/...`, `refactor/...`).
- Keep commits focused, with concise imperative subjects (`<= 50` characters) and clear summaries of behavior changes and verification evidence.
- Add targeted regression tests for every bug fix or guardrail change.
