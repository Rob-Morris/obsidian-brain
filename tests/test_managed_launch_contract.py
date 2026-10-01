"""Every managed-interpreter launch in `src/brain-core` and `cli/` goes through the owner.

Managed interpreter paths come from too many places (`sys.executable`,
`command_python`, `state["python"]`, ...) for a static rule to follow them, so
the contract is structural: a launch primitive may be referenced only inside
`ManagedCommand`, or in a function allowlisted here with a one-line reason.
References, not calls, are matched, through module aliases and literal
`getattr`, so aliases and injected runners cannot slip past; annotations are
ignored.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TREES = ("src/brain-core", "cli")
EXCLUDED_PARTS = {"tests", "tools", "__pycache__"}

_SUBPROCESS = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
_ASYNCIO = {"create_subprocess_exec", "create_subprocess_shell"}

OWNER = {
    "src/brain-core/scripts/_common/_venv.py::ManagedCommand.run",
    "src/brain-core/scripts/_common/_venv.py::ManagedCommand.popen",
    "src/brain-core/scripts/_common/_venv.py::ManagedCommand.exec",
}

ALLOWED = {
    "src/brain-core/scripts/_common/_venv.py::_python_tag_for_launcher": "base launcher version probe",
    "src/brain-core/scripts/_common/_venv.py::ensure_central_venv": "base launcher creates the venv",
    "src/brain-core/scripts/_common/_venv.py::_role_probe_passes": "materialisation probe executes the staged candidate",
    "src/brain-core/scripts/upgrade.py::_validate_compile": "self-contained upgrade replaces _common mid-run",
    "src/brain-core/scripts/upgrade.py::_run_repair_scope_after_upgrade": "self-contained upgrade replaces _common mid-run",
    "src/brain-core/scripts/_bootstrap/mcp_readiness.py::verify_command": "emulates the client: runs whatever the persisted config names",
    "src/brain-core/scripts/_bootstrap/mcp_transport.py::_register_claude_via_cli": "claude CLI",
    "src/brain-core/scripts/_bootstrap/mcp_transport.py::delegate_machine_command": "installed brain binary",
    "src/brain-core/scripts/_bootstrap/workspace_scaffold.py::_run_git_rev_parse": "git",
    "src/brain-core/scripts/_skill_library/git_source.py::_run_bytes": "git",
    "src/brain-core/scripts/_skill_library/git_source.py::_run_archive": "git",
    "src/brain-core/scripts/_machine/topology.py::scan_processes": "ps",
    "src/brain-core/scripts/_machine/process_footprint.py::_darwin_footprint": "footprint",
    "src/brain-core/scripts/shape_presentation.py::_render_pdf": "marp",
    "src/brain-core/scripts/shape_presentation.py::_launch_preview": "marp",
    "src/brain-core/scripts/shape_printable.py::_render_pdf": "pandoc",
    "cli/_launcher/approval_management.py::_client_version": "client binary version probe",
}


def _is_launch(module: str, name: str) -> bool:
    if module == "os":
        return name.startswith(("exec", "spawn", "posix_spawn")) or name in {"system", "popen"}
    if module == "subprocess":
        return name in _SUBPROCESS
    if module == "asyncio":
        return name in _ASYNCIO
    return False


_LAUNCH_MODULES = ("os", "subprocess", "asyncio")


def _module_aliases(tree: ast.AST) -> dict[str, str]:
    """Local names bound to launch modules anywhere in the file, e.g. `import subprocess as sp`."""
    aliases = {module: module for module in _LAUNCH_MODULES}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _LAUNCH_MODULES:
                    aliases[alias.asname or alias.name] = alias.name
    return aliases


class _LaunchReferences(ast.NodeVisitor):
    def __init__(self, path: str, aliases: dict[str, str]):
        self.path = path
        self.aliases = aliases
        self.scope: list[str] = []
        self.found: list[tuple[str, str, int]] = []

    def _record(self, what: str, line: int) -> None:
        self.found.append((f"{self.path}::{'.'.join(self.scope) or '<module>'}", what, line))

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self.scope.append(node.name)
        for field, value in ast.iter_fields(node):
            if field == "returns":
                continue
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, ast.AST):
                    self.visit(child)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_arg(self, node):
        pass  # only an annotation lives here

    def visit_AnnAssign(self, node):
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_Attribute(self, node):
        module = self.aliases.get(node.value.id) if isinstance(node.value, ast.Name) else None
        if module is not None and _is_launch(module, node.attr):
            self._record(f"{module}.{node.attr}", node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node):
        if (isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and isinstance(node.args[1], ast.Constant)):
            module = self.aliases.get(node.args[0].id)
            if module is not None and isinstance(node.args[1].value, str) and _is_launch(module, node.args[1].value):
                self._record(f"{module}.{node.args[1].value}", node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if _is_launch(node.module or "", alias.name):
                self._record(f"from {node.module} import {alias.name}", node.lineno)


def launch_references(source: str, path: str) -> list[tuple[str, str, int]]:
    """Return `(path::qualname, reference, line)` for every launch primitive reference."""
    tree = ast.parse(source, path)
    visitor = _LaunchReferences(path, _module_aliases(tree))
    visitor.visit(tree)
    return visitor.found


def _tree_references() -> list[tuple[str, str, int]]:
    found = []
    for tree in TREES:
        for path in sorted((REPO_ROOT / tree).rglob("*.py")):
            relative = path.relative_to(REPO_ROOT)
            if EXCLUDED_PARTS.intersection(relative.parts):
                continue
            found.extend(launch_references(path.read_text(encoding="utf-8"), relative.as_posix()))
    return found


def test_aliases_and_injected_runners_are_references_too():
    source = (
        "import subprocess, os, asyncio\n"
        "runner = subprocess.run\n"
        "from os import execve\n"
        "class Invoker:\n"
        "    default = subprocess.Popen\n"
        "    async def go(self):\n"
        "        await asyncio.create_subprocess_exec('x')\n"
    )
    assert [(key, what) for key, what, _ in launch_references(source, "m.py")] == [
        ("m.py::<module>", "subprocess.run"),
        ("m.py::<module>", "from os import execve"),
        ("m.py::Invoker", "subprocess.Popen"),
        ("m.py::Invoker.go", "asyncio.create_subprocess_exec"),
    ]


def test_module_aliases_and_getattr_are_references_too():
    source = (
        "import subprocess as sp\n"
        "def launch():\n"
        "    import os as _os\n"
        "    sp.run(['x'])\n"
        "    _os.execv('x', ['x'])\n"
        "    getattr(sp, 'Popen')(['x'])\n"
        "    getattr(sp, 'DEVNULL')\n"
    )
    assert [(key, what) for key, what, _ in launch_references(source, "m.py")] == [
        ("m.py::launch", "subprocess.run"),
        ("m.py::launch", "os.execv"),
        ("m.py::launch", "subprocess.Popen"),
    ]


def test_annotations_are_ignored():
    source = (
        "import subprocess\n"
        "child: subprocess.Popen | None = None\n"
        "def wait(process: subprocess.Popen) -> subprocess.CompletedProcess:\n"
        "    result: subprocess.CompletedProcess = process.wait()\n"
        "    return result\n"
    )
    assert launch_references(source, "m.py") == []


def test_every_launch_reference_is_the_owner_or_allowlisted():
    references = _tree_references()
    keys = {key for key, _, _ in references}
    unowned = sorted(
        f"{key} ({what}, line {line})"
        for key, what, line in references if key not in OWNER and key not in ALLOWED
    )
    assert unowned == [], "launch managed interpreters through ManagedCommand, or allowlist with a reason"
    assert OWNER <= keys, "the owner moved; update OWNER"
    assert sorted(set(ALLOWED) - keys) == [], "stale allowlist entries"
