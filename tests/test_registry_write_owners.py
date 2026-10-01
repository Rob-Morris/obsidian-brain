"""Brain registration has one owner: only the install and the launcher reach the vault registry's writers.

The writer set is derived from ``vault_registry.py`` itself: every function that,
directly or through the module's other functions, writes a file, removes one or
takes the registry lock. Product code (``src``, ``cli``, ``tools``) may name a
writer only from the owners pinned below, and may not reach the module in a way
the scan cannot follow.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("src", "cli", "tools")
WRITER_MODULE = Path("src/brain-core/scripts/vault_registry.py")
OWNERS = {
    Path("src/brain-core/scripts/install.py"): {"register", "set_default"},
    Path("cli/_launcher/registry.py"): {
        "register_action", "unregister_action", "set_default_action", "clear_default_action", "prune_action",
    },
}
# Files that may name the direct script: the resolution runtime deploys it, and
# the shell installers are the install owner's own entry points.
SCRIPT_NAME = "vault_registry.py"
SCRIPT_NAME_ALLOWED = {
    Path("src/brain-core/scripts/_machine/resolution_runtime.py"),
    Path("install.sh"),
    Path("install.ps1"),
}
SHELL_SUFFIXES = {".sh", ".ps1", ".cmd", ".bash"}

_WRITE_CALLS = {"safe_write", "exclusive_file_lock"}
_OS_WRITE_CALLS = {"unlink", "remove", "rename", "replace"}
# Path-style writers, matched on any receiver; ``replace`` stays os-only because str.replace shares it.
_METHOD_WRITE_CALLS = {"write_text", "write_bytes", "touch", "unlink", "mkdir", "rename"}


def derive_writers(source: str) -> set[str]:
    """Top-level functions that write, directly or through other top-level functions."""
    functions = {node.name: node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}
    direct, calls = set(), {}
    for name, node in functions.items():
        calls[name] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in functions:
                calls[name].add(child.id)
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name) and func.id in _WRITE_CALLS:
                    direct.add(name)
                if (isinstance(func, ast.Attribute) and func.attr in _OS_WRITE_CALLS
                        and isinstance(func.value, ast.Name) and func.value.id == "os"):
                    direct.add(name)
                if isinstance(func, ast.Attribute) and func.attr in _METHOD_WRITE_CALLS:
                    direct.add(name)
    writers = set(direct)
    while True:
        grown = {name for name, called in calls.items() if called & writers} | writers
        if grown == writers:
            return writers
        writers = grown


def scan(source: str, writers: set[str]) -> tuple[set[str], list[str]]:
    """Return the writer names a module reaches and any access the scan cannot follow."""
    tree = ast.parse(source)
    aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names if alias.name == "vault_registry"
    }
    reached, opaque = set(), []
    followed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "vault_registry":
            for alias in node.names:
                if alias.name == "*":
                    opaque.append("star import from vault_registry")
                elif alias.name in writers:
                    reached.add(alias.name)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            followed.add(id(node.value))
            if node.attr in writers:
                reached.add(node.attr)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            first = node.args[0] if node.args else None
            if name in {"import_module", "__import__"} and isinstance(first, ast.Constant) \
                    and str(first.value).split(".")[-1] == "vault_registry":
                opaque.append("dynamic import of vault_registry")
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id in aliases and isinstance(node.ctx, ast.Load)
                and id(node) not in followed):
            opaque.append(f"vault_registry module object used directly at line {node.lineno}")
    return reached, opaque


def _has_shebang(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(2) == b"#!"


def _writers() -> set[str]:
    return derive_writers((REPO_ROOT / WRITER_MODULE).read_text(encoding="utf-8"))


def _product_files():
    for top in SCANNED:
        yield from sorted((REPO_ROOT / top).rglob("*"))
    yield REPO_ROOT / "install.sh"
    yield REPO_ROOT / "install.ps1"


def test_vault_registry_writers_are_reached_only_from_their_owners():
    writers = _writers()
    reached, opaque, named_script = {}, {}, set()
    for path in _product_files():
        relative = path.relative_to(REPO_ROOT)
        if not path.is_file() or relative == WRITER_MODULE:
            continue
        if path.suffix == ".py":
            source = path.read_text(encoding="utf-8")
            names, problems = scan(source, writers)
            if names:
                reached[relative] = names
            if problems:
                opaque[relative] = problems
        elif path.suffix in SHELL_SUFFIXES or _has_shebang(path):
            source = path.read_text(encoding="utf-8")
        else:
            continue
        if SCRIPT_NAME in source:
            named_script.add(relative)

    assert opaque == {}
    assert reached == OWNERS
    assert named_script - SCRIPT_NAME_ALLOWED == set()


def test_derived_writers_cover_the_write_primitives_and_spare_the_readers():
    writers = _writers()
    assert {
        "_locked", "_save_registry_entries", "_write_default_unlocked", "_clear_default_unlocked",
        "register", "register_action", "unregister", "unregister_action",
        "set_default", "set_default_action", "clear_default", "clear_default_action",
        "prune", "prune_action", "main",
    } <= writers
    assert not writers & {
        "load_registry_entries", "get_default", "resolve", "list_entries",
        "brain_id_for_path", "preview_register_action", "register_guidance",
    }


def test_writer_derivation_follows_a_wrapper():
    module = (
        "def _save():\n    safe_write('x', 'y')\n"
        "def register(path):\n    _save()\n"
        "def backfill(path):\n    return register(path)\n"
        "def touch_default(path):\n    Path(path).write_text('x')\n"
        "def lookup(path):\n    return path.replace('a', 'b')\n"
    )
    assert derive_writers(module) == {"_save", "register", "backfill", "touch_default"}


@pytest.mark.parametrize(("snippet", "reached", "opaque"), [
    ("import vault_registry\nvault_registry.backfill('p')\n", {"backfill"}, 0),
    ("import vault_registry as vr\nvr.register('p')\n", {"register"}, 0),
    ("from vault_registry import register as add\nadd('p')\n", {"register"}, 0),
    ("from vault_registry import brain_id_for_path\n", set(), 0),
    ("import vault_registry\nvault_registry.main()\n", {"main"}, 0),
    ("from vault_registry import *\n", set(), 1),
    ("import vault_registry\ngetattr(vault_registry, 'register')('p')\n", set(), 1),
    ("import vault_registry\nregistry = vault_registry\n", set(), 1),
    ("import importlib\nimportlib.import_module('vault_registry').register('p')\n", set(), 1),
    ("__import__('vault_registry')\n", set(), 1),
])
def test_scan_follows_aliases_and_rejects_opaque_access(snippet, reached, opaque):
    writers = {"register", "backfill", "main"}
    names, problems = scan(snippet, writers)
    assert names == reached
    assert len(problems) == opaque
