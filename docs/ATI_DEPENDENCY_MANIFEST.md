# ATI Dependency Manifest

## Profiles

| File | Scope | Packages |
|------|-------|----------|
| `requirements.txt` | **Core runtime** — observation → context → rule-based decision → paper execution | fastapi, uvicorn, pydantic, pydantic-settings, python-dotenv, websockets, pyyaml, httpx, numpy |
| `requirements-ai.txt` | Optional: PydanticAI structured-output LLM reasoning (ADR 0011) | pydantic-ai[openai] |
| `requirements-ml.txt` | Optional: FinBERT sentiment (torch + transformers) | torch, transformers |
| `requirements-research.txt` | Optional: portfolio-level risk management (HRP + CVaR) | pandas, cvxpy, riskfolio-lib |
| `requirements-data.txt` | Optional: SEC EDGAR insider/13F filings | edgartools |
| `requirements-venue.txt` | Optional: CCXT exchange observation/execution adapters | ccxt |
| `requirements-dev.txt` | Dev tooling (includes core via `-r`) | pytest, pytest-asyncio, mypy, types-PyYAML, ruff |
| `requirements-all.txt` | Full research install (core + every profile + dev) | all of the above |

## Design Rationale

**numpy is core** because `RegimeFeature` — a core context feature in `ALL_FEATURES` — eagerly imports `regime_detector` which requires numpy at module level. Moving numpy to a research group would require lazy-importing regime, adding unnecessary complexity to a core pipeline path.

**httpx is core** because the OmniRoute reasoner (V1 default brain, ADR 0005/0006) imports it at module level, and the core context pipeline imports bootstrap which imports the reasoner. It is a lightweight HTTP client (~5MB) with no heavy transitive dependencies.

**All other heavy integrations are lazy-imported** inside their respective factory functions in `bootstrap.py`, and the feature modules (`features/sentiment.py`, `features/insider.py`) use `TYPE_CHECKING`-only imports for service classes. This means `import backend.main` succeeds without torch, transformers, cvxpy, riskfolio, pandas, edgar, ccxt, or pydantic-ai installed.

## How to Verify

```bash
# Core-only install (fresh environment):
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Full research install:
pip install -r requirements-all.txt
```

## Import Smoke Test

`tests/integrity/test_dependency_manifest.py` contains three tests:

1. **`TestManifestCoverage.test_all_imports_declared`** — AST-scans every `.py` file in `backend/` and `tests/`, collects all third-party imports, and asserts each is declared in at least one requirements file.

2. **`TestCoreProfileLightweight.test_no_heavy_packages_in_core`** — Parses `requirements.txt` and asserts no heavy optional packages (torch, transformers, cvxpy, riskfolio, pandas, edgar, ccxt, pydantic_ai, openai) appear in core.

3. **`TestCoreImportSmoke.test_core_imports_without_optional_packages`** — Spawns a subprocess with a meta-path finder that raises `ImportError` for blocked optional top-level module names, then imports `backend.main` and core builders. Proves the core chain loads without optional packages.

## Adding a New Dependency

1. Identify the import name and PyPI distribution name.
2. Choose the appropriate profile (core, ai, ml, research, data, venue).
3. Add the `distname>=version` line to the matching `requirements-*.txt`.
4. If the import name differs from the dist name, add an entry to `_DIST_TO_IMPORT` in `tests/integrity/test_dependency_manifest.py`.
5. Run `py -3 -m pytest tests/integrity/` to confirm the contract test passes.
