"""Import-closure checks for the tier-strict application contract package."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_ROOT = REPO_ROOT / "src" / "brain-core" / "scripts" / "_application"
LOWER_LEVEL_PACKAGES = (
    "_common",
    "_portable",
    "_lifecycle",
    "_search",
    "_bootstrap",
    "_machine",
    "_semantic",
)
FORBIDDEN_APPLICATION_IMPORTS = {
    "argparse",
    "mcp",
    "pydantic",
    "numpy",
    "torch",
    "transformers",
    "brain_mcp",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".", 1)[0])
    return imported


def test_application_contracts_have_no_adapter_or_managed_dependency_imports():
    offenders = {}
    for path in APPLICATION_ROOT.glob("*.py"):
        forbidden = _imports(path) & FORBIDDEN_APPLICATION_IMPORTS
        if forbidden:
            offenders[path.name] = sorted(forbidden)

    assert offenders == {}


def test_package_initializer_does_not_eagerly_collapse_dependency_tiers():
    tree = ast.parse((APPLICATION_ROOT / "__init__.py").read_text(encoding="utf-8"))

    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))


def test_lower_level_packages_do_not_import_back_into_application():
    offenders = []
    scripts_root = APPLICATION_ROOT.parent
    for package in LOWER_LEVEL_PACKAGES:
        for path in (scripts_root / package).rglob("*.py"):
            if "_application" in _imports(path):
                offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_contract_modules_import_in_isolated_interpreter_without_runtime_dependencies():
    scripts_root = APPLICATION_ROOT.parent
    source = (
        "import sys; "
        f"sys.path.insert(0, {str(scripts_root)!r}); "
        "import _application.types, _application.receipts, _application.context, "
        "_application.results, _application.requests, _application.catalogue, "
        "_application.resolver, _application.versions, _application.availability, "
        "_application.application; "
        "forbidden={'argparse','mcp','pydantic','numpy','torch','transformers','brain_mcp'}; "
        "loaded=forbidden.intersection(sys.modules); "
        "assert not loaded, loaded"
    )

    result = subprocess.run(
        [sys.executable, "-I", "-c", source],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
