# tests/integrity/test_dependency_manifest.py
"""Dependency manifest tests (P0-001).

These tests enforce the contract that:
1. Every third-party import in backend/ or tests/ is declared in at least one
   requirements profile.
2. Core requirements (requirements.txt) do not contain heavy optional packages.
3. The core import chain can be loaded without optional packages installed
   (subprocess smoke test with blocking meta-path finder).
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Mapping: importable module name -> PyPI distribution name
# Only includes packages where the import name differs from the dist name.
# ---------------------------------------------------------------------------
_DIST_TO_IMPORT: dict[str, str] = {
    "pyyaml": "yaml",
    "python-dotenv": "dotenv",
    "pydantic-settings": "pydantic_settings",
    "riskfolio-lib": "riskfolio",
    "edgartools": "edgar",
    "pydantic-ai": "pydantic_ai",
    "pydantic-ai-slim": "pydantic_ai",
    "pytest-asyncio": "pytest_asyncio",
    "types-PyYAML": "__skip__",
}

_IMPORT_TO_DIST: dict[str, str] = {v: k for k, v in _DIST_TO_IMPORT.items() if v != "__skip__"}

# Heavy optional packages that must NOT appear in core requirements.
_HEAVY_OPTIONAL: set[str] = {
    "torch",
    "transformers",
    "cvxpy",
    "riskfolio",
    "pandas",
    "edgar",
    "ccxt",
    "pydantic_ai",
    "openai",
}


def _parse_requirements(path: Path) -> set[str]:
    """Parse a requirements file and return declared importable names."""
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # strip extras  e.g. pydantic-ai[openai]
        dist = line.split("[")[0].split(">=")[0].split("==")[0].split("<=")[0]
        imp_name = _DIST_TO_IMPORT.get(dist, dist.replace("-", "_"))
        names.add(imp_name)
    return names


def _scan_imports(root: Path) -> dict[str, set[str]]:
    """AST-scan all .py files under *root* and collect third-party imports.

    Returns {import_name: {file, ...}}.
    """
    import sys as _sys

    stdlib = set(_sys.stdlib_module_names)
    local_top = {"backend", "tests"}
    third_party: dict[str, set[str]] = {}

    for py_path in root.rglob("*.py"):
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in stdlib and top not in local_top and not top.startswith("_"):
                        third_party.setdefault(top, set()).add(str(py_path))
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                top = node.module.split(".")[0]
                if top not in stdlib and top not in local_top and not top.startswith("_"):
                    third_party.setdefault(top, set()).add(str(py_path))
    return third_party


class TestManifestCoverage:
    """Every third-party import is declared in at least one requirements file."""

    def test_all_imports_declared(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        req_dir = repo_root
        req_files = sorted(req_dir.glob("requirements*.txt"))

        # Aggregate all declared imports across profiles.
        all_declared: set[str] = set()
        for rf in req_files:
            all_declared |= _parse_requirements(rf)

        # Scan source trees.
        scanned = {}
        for subdir in ("backend", "tests"):
            scanned |= _scan_imports(repo_root / subdir)

        # Filter to known third-party (not stdlib, not local).
        undeclared: dict[str, set[str]] = {}
        for mod, files in sorted(scanned.items()):
            dist = _IMPORT_TO_DIST.get(mod, mod)
            # Check both the import name and the dist name are declared.
            if mod not in all_declared and dist not in all_declared:
                undeclared[mod] = files

        assert not undeclared, "Undeclared third-party imports found:\n" + "\n".join(
            f"  {mod}: {sorted(files)[0]}" for mod, files in sorted(undeclared.items())
        )


class TestCoreProfileLightweight:
    """Core requirements do not contain heavy optional packages."""

    def test_no_heavy_packages_in_core(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        core = _parse_requirements(repo_root / "requirements.txt")

        violations = core & _HEAVY_OPTIONAL
        assert not violations, (
            f"Heavy optional packages found in core requirements: {sorted(violations)}"
        )


class TestCoreImportSmoke:
    """Core import chain loads without optional packages (subprocess test)."""

    def test_core_imports_without_optional_packages(self) -> None:
        """Simulate absence of optional packages via a blocking meta-path finder."""
        repo_root = Path(__file__).resolve().parents[2]
        script = textwrap.dedent(f"""\
            import sys

            BLOCKED = {{"torch", "transformers", "cvxpy", "riskfolio", "pandas",
                         "edgar", "ccxt", "pydantic_ai", "openai"}}

            class _BlockOptional:
                def find_module(self, fullname, path=None):
                    top = fullname.split(".")[0]
                    if top in BLOCKED:
                        raise ImportError(f"optional package blocked: {{top}}")
                    return None

                def find_spec(self, fullname, path=None, target=None):
                    top = fullname.split(".")[0]
                    if top in BLOCKED:
                        raise ImportError(f"optional package blocked: {{top}}")
                    return None

            # Must be inserted before the default path-based finders.
            sys.meta_path.insert(0, _BlockOptional())

            # Add repo root to path so 'backend' and 'tests' are importable.
            sys.path.insert(0, {str(repo_root)!r})

            # --- Core surface: these must import cleanly ---
            import backend.main
            from backend.application.context.bootstrap import (
                build_context_pipeline,
                build_context_pipeline_from_config,
                build_decision_pipeline,
                build_memory_pipeline,
                build_supervisor,
                build_reflection_service,
                build_ofi_tracker,
            )
            print("CORE_IMPORT_OK")
        """)

        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(repo_root),
        )
        assert result.returncode == 0, (
            f"Core imports failed (exit {result.returncode}):\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert "CORE_IMPORT_OK" in result.stdout
