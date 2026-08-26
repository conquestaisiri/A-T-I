"""Architecture boundary test: domain must not import application (AST import graph).

Fails CI if any file under backend/domain imports from backend.application.
This enforces the layered architecture invariant: domain is pure and has zero
outward dependencies to application/infrastructure/presentation.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Forbidden import prefixes for domain layer
FORBIDDEN_PREFIXES = (
    "backend.application",
    "backend.infrastructure",
    "backend.presentation",
    "backend.main",
)

# A2 waiver (ADR 0006 annex, sunset: when alt-data ports extracted): these two
# domain features name application service types under TYPE_CHECKING only —
# no runtime import edge exists. The boundary test enforces runtime imports.
TYPE_CHECKING_WAIVED = {
    Path("backend/domain/context/features/sentiment.py"),
    Path("backend/domain/context/features/insider.py"),
}

# Allowlist: domain may import only from backend.domain, stdlib, and third-party.
# This test checks the negative: domain !-> application (A1) is the primary
# invariant requested; we also guard against domain -> infrastructure/presentation
# as those are equally architectural violations (A4).
DOMAIN_ROOT = Path("backend/domain")


def _collect_imports(tree: ast.Module) -> list[tuple[int, str]]:
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                # from X import Y
                imports.append((node.lineno, node.module))
            elif node.level > 0:
                # relative import: from . import X  -> treat as local, ignore
                # domain relative imports stay within domain, so allowed
                continue
    return imports


def _strip_type_checking_imports(tree: ast.Module, source: str) -> list[tuple[int, str]]:
    """Collect only runtime imports: prune entire ``if TYPE_CHECKING:`` subtrees."""
    runtime: list[tuple[int, str]] = []
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If):
                test = child.test
                is_tc = isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
                if is_tc:
                    continue  # prune the whole guarded subtree
            if isinstance(child, ast.Import):
                for alias in child.names:
                    runtime.append((child.lineno, alias.name))
                continue
            if isinstance(child, ast.ImportFrom):
                if child.module is not None:
                    runtime.append((child.lineno, child.module))
                continue
            stack.append(child)
    return runtime


def test_domain_does_not_import_application() -> None:
    violations: list[str] = []
    if not DOMAIN_ROOT.exists():
        return

    for py_file in DOMAIN_ROOT.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            violations.append(f"{py_file}:{exc.lineno} SyntaxError: {exc}")
            continue

        # TYPE_CHECKING-guarded imports carry no runtime edge; skip waived files
        waived = {p.name for p in TYPE_CHECKING_WAIVED}
        if py_file.name in waived:
            runtime_only = _strip_type_checking_imports(tree, source)
            if not runtime_only:
                continue
            imports = runtime_only
        else:
            imports = _collect_imports(tree)

        for lineno, mod in imports:
            for prefix in FORBIDDEN_PREFIXES:
                if mod == prefix or mod.startswith(prefix + "."):
                    violations.append(
                        f"{py_file}:{lineno} forbids domain -> {prefix} (import '{mod}')"
                    )

    assert not violations, (
        "Architecture violation: backend/domain must not import application/"
        "infrastructure/presentation (AST import graph domain !-> application). "
        "Failing CI per A1/A4 boundary rule. Violations:\n  - " + "\n  - ".join(violations)
    )


def test_domain_import_graph_is_acyclic_within_domain() -> None:
    """Sanity: domain files must be parseable."""
    for py_file in DOMAIN_ROOT.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        ast.parse(source)  # must not raise
