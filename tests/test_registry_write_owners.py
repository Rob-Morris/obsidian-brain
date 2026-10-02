"""Each register has its owners: only they reach its module's writers.

Brain registration (``vault_registry``) is written by the install and the
launcher; the linked workspace registry (``workspace_registry``) by the commands
that make, remove or repair a link, plus the historical migration that first
wrote it. Each writer set is derived from the module itself: every function
that, directly or through the module's other functions, writes a file, removes
one or takes a lock. Product code (``src``, ``cli``, ``tools``) may name a writer
only from the owners pinned below, and may not reach the module in a way the
scan cannot follow.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("src", "cli", "tools")
SCRIPTS = Path("src/brain-core/scripts")
WRITER_MODULE = SCRIPTS / "vault_registry.py"
OWNERS = {
    SCRIPTS / "install.py": {"register", "set_default"},
    Path("cli/_launcher/registry.py"): {
        "register_action", "unregister_action", "set_default_action", "clear_default_action", "prune_action",
    },
}
REGISTERS = {
    "vault_registry": (WRITER_MODULE, OWNERS),
    "workspace_registry": (SCRIPTS / "workspace_registry.py", {
        SCRIPTS / "_application/workspace/setup.py": {"register_workspace"},
        SCRIPTS / "_application/workspace/unregister.py": {"unregister_workspace"},
        SCRIPTS / "_portable/registry_maintenance.py": {"save_registry"},
        SCRIPTS / "migrations/migrate_to_0_31_0.py": {"save_registry"},
        # MCP configuration derives the row its manifest implies (DD-083 item 5).
        SCRIPTS / "_bootstrap/mcp_registration.py": {"stage_link_row"},
        SCRIPTS / "_bootstrap/mcp_migration.py": {"stage_canonical_rows"},
    }),
}
# Functions that both name the linked workspace registry's file and write
# something, accepted because the registry write itself goes elsewhere.
REGISTRY_PATH_WRITERS = {
    (SCRIPTS / "_application/workspace/setup.py", "execute"):
        "writes the row through register_workspace; the path names the effect subject",
    (SCRIPTS / "_bootstrap/mcp_migration.py", "resume_migration"):
        "replays a journalled MCP migration whose registry change stage_canonical_rows staged",
    (SCRIPTS / "migrations/migrate_to_0_16_0.py", "migrate"):
        "historical migration that moved the file into .brain/local",
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

_WRITE_CALLS = {"safe_write", "safe_write_json", "exclusive_file_lock"}
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


def scan(source: str, writers: set[str], module: str = "vault_registry") -> tuple[set[str], list[str]]:
    """Return the writer names a source reaches in ``module`` and any access the scan cannot follow."""
    tree = ast.parse(source)
    aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names if alias.name == module
    }
    reached, opaque = set(), []
    followed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            for alias in node.names:
                if alias.name == "*":
                    opaque.append(f"star import from {module}")
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
                    and str(first.value).split(".")[-1] == module:
                opaque.append(f"dynamic import of {module}")
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id in aliases and isinstance(node.ctx, ast.Load)
                and id(node) not in followed):
            opaque.append(f"{module} module object used directly at line {node.lineno}")
    return reached, opaque


def registry_path_writers(source: str) -> list[str]:
    """Functions that name ``workspaces.json`` and also call a write primitive."""
    tree = ast.parse(source)

    def literal(node):
        return any(isinstance(item, ast.Constant) and isinstance(item.value, str) and "workspaces.json" in item.value
                   for item in ast.walk(node))

    constants = {target.id for node in tree.body if isinstance(node, ast.Assign) and literal(node.value)
                 for target in node.targets if isinstance(target, ast.Name)}
    writes = _WRITE_CALLS | _METHOD_WRITE_CALLS | {"_write_json", "delete", "replace", "copy2", "move"}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = literal(node) or any(
            isinstance(item, ast.Name) and item.id in constants | {"REGISTRY_REL"}
            or isinstance(item, ast.Attribute) and item.attr in {"REGISTRY_REL", "REGISTRY_FILE", "_registry_path"}
            and isinstance(item.value, ast.Name) and item.value.id == "workspace_registry"
            for item in ast.walk(node))
        writer = any(
            isinstance(item, ast.Call) and (
                isinstance(item.func, ast.Name) and item.func.id in writes
                or isinstance(item.func, ast.Attribute) and item.func.attr in writes)
            for item in ast.walk(node))
        if names and writer:
            found.append(node.name)
    return found


def _has_shebang(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(2) == b"#!"


def _writers(module_path: Path = WRITER_MODULE) -> set[str]:
    return derive_writers((REPO_ROOT / module_path).read_text(encoding="utf-8"))


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
    assert reached == REGISTERS["vault_registry"][1]
    assert named_script - SCRIPT_NAME_ALLOWED == set()


def test_linked_workspace_registry_writers_are_reached_only_from_the_link_owners():
    module_path, owners = REGISTERS["workspace_registry"]
    writers = _writers(module_path)
    reached, opaque = {}, {}
    for path in _product_files():
        relative = path.relative_to(REPO_ROOT)
        if path.suffix != ".py" or not path.is_file() or relative == module_path:
            continue
        names, problems = scan(path.read_text(encoding="utf-8"), writers, "workspace_registry")
        if names:
            reached[relative] = names
        if problems:
            opaque[relative] = problems

    assert opaque == {}
    assert reached == owners


def test_no_function_outside_the_owners_writes_the_linked_workspace_registry_by_path():
    module_path = REGISTERS["workspace_registry"][0]
    found = set()
    for path in _product_files():
        relative = path.relative_to(REPO_ROOT)
        if path.suffix == ".py" and path.is_file() and relative != module_path:
            found.update((relative, name) for name in registry_path_writers(path.read_text(encoding="utf-8")))

    assert found == set(REGISTRY_PATH_WRITERS)


@pytest.mark.parametrize(("snippet", "found"), [
    ("def f(root):\n    safe_write_json(root / '.brain/local/workspaces.json', {})\n", ["f"]),
    ("PATH = ('.brain', 'local', 'workspaces.json')\ndef f(root):\n    (root.joinpath(*PATH)).write_text('{}')\n", ["f"]),
    ("import workspace_registry\ndef f(plan, root):\n    plan.write_text(workspace_registry._registry_path(root), '{}')\n", ["f"]),
    ("def f(root):\n    return (root / '.brain/local/workspaces.json').read_text()\n", []),
])
def test_path_scan_sees_literal_constant_and_module_paths(snippet, found):
    assert registry_path_writers(snippet) == found


def test_linked_workspace_registry_script_no_longer_writes():
    writers = _writers(REGISTERS["workspace_registry"][0])
    assert {"save_registry", "register_workspace", "unregister_workspace"} <= writers
    assert "main" not in writers
    assert not writers & {"load_registry", "load_registry_strict", "resolve_workspace", "list_workspaces",
                          "canonical_path"}


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
