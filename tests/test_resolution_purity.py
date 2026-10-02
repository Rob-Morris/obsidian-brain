"""Resolving a Brain writes nothing under the test tree and takes no file lock.

``HOME`` and the config, state and cache homes are all redirected into
``tmp_path``, beside every vault and workspace manifest. The whole tree is
digested before and after each call, so a created file or directory, including
a lock file, counts as a write as surely as a changed byte. Lock acquisition is
also made to raise during the call, which catches a lock on a file that already
existed. Writes outside ``tmp_path`` (an absolute path not derived from these
homes) are not observed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from brain_test_support import folder_tree

from _bootstrap.workspace_binding import (
    WorkspaceBindingError,
    resolve_brain_target,
    resolve_local_brain_alias,
)
import vault_registry


def _vault(root: Path, name: str) -> Path:
    vault = root / name
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("1.0.0\n")
    return vault


def _workspace(root: Path, name: str, brain: str | None = None) -> Path:
    workspace = root / name
    workspace.mkdir()
    if brain is not None:
        manifest = workspace / ".brain" / "local" / "workspace.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(f"brain: {brain}\nslug: {name}\n")
    return workspace


@pytest.fixture(autouse=True)
def _machine_homes_in_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


@pytest.fixture
def forbid_locks(monkeypatch):
    """Make every file-lock entry point in the resolution closure raise."""
    from _bootstrap import file_lock
    from _common import _file_lock

    def refuse(*_args, **_kwargs):
        raise AssertionError("resolution must not take a file lock")

    def apply():
        for module in (file_lock, _file_lock, vault_registry):
            monkeypatch.setattr(module, "exclusive_file_lock", refuse)
        monkeypatch.setattr(vault_registry, "_locked", refuse)

    return apply


def _anchor_valid(root):
    vault = _vault(root, "vault")
    workspace = _workspace(root, "ws", vault_registry.register(str(vault)))
    return {"workspace_env": str(workspace), "vault_root_env": None, "start_dir": root}, "workspace_env"


def _anchor_stale(root):
    vault = _vault(root, "vault")
    workspace = _workspace(root, "ws", vault_registry.register(str(vault)))
    shutil.rmtree(vault)
    fallback = _vault(root, "fallback")
    return {"workspace_env": str(workspace), "vault_root_env": str(fallback), "start_dir": root}, "stale_binding"


def _anchor_missing_with_env_root(root):
    vault = _vault(root, "vault")
    workspace = _workspace(root, "ws")
    return {"workspace_env": str(workspace), "vault_root_env": str(vault), "start_dir": root}, "vault_root_env"


def _anchor_missing(root):
    workspace = _workspace(root, "ws")
    return {"workspace_env": str(workspace), "vault_root_env": None, "start_dir": root}, "no_brain"


def _walk_vault_self(root):
    vault = _vault(root, "vault")
    start = vault / "Notes"
    start.mkdir()
    return {"workspace_env": None, "vault_root_env": None, "start_dir": start}, "vault_self"


def _walk_binding(root):
    vault = _vault(root, "vault")
    workspace = _workspace(root, "ws", vault_registry.register(str(vault)))
    start = workspace / "src"
    start.mkdir()
    return {"workspace_env": None, "vault_root_env": None, "start_dir": start}, "workspace_binding"


def _env_root(root):
    vault = _vault(root, "vault")
    start = _workspace(root, "elsewhere")
    return {"workspace_env": None, "vault_root_env": str(vault), "start_dir": start}, "vault_root_env"


def _registry_default(root):
    vault = _vault(root, "vault")
    vault_registry.set_default(vault_registry.register(str(vault)))
    start = _workspace(root, "elsewhere")
    return {"workspace_env": None, "vault_root_env": None, "start_dir": start}, "registry_default"


def _dangling_default(root):
    vault = _vault(root, "vault")
    vault_registry.set_default(vault_registry.register(str(vault)))
    shutil.rmtree(vault)
    start = _workspace(root, "elsewhere")
    return {"workspace_env": None, "vault_root_env": None, "start_dir": start}, "stale_binding"


def _nothing(root):
    start = _workspace(root, "elsewhere")
    return {"workspace_env": None, "vault_root_env": None, "start_dir": start}, "no_brain"


@pytest.mark.parametrize("arrange", [
    _anchor_valid, _anchor_stale, _anchor_missing_with_env_root, _anchor_missing,
    _walk_vault_self, _walk_binding, _env_root, _registry_default, _dangling_default, _nothing,
], ids=lambda arrange: arrange.__name__.lstrip("_"))
def test_every_rung_outcome_leaves_state_byte_identical(tmp_path, arrange, forbid_locks):
    arguments, outcome = arrange(tmp_path)
    forbid_locks()
    before = folder_tree(tmp_path)

    try:
        observed = resolve_brain_target(**arguments).source
    except WorkspaceBindingError as exc:
        observed = exc.code

    assert observed == outcome
    assert folder_tree(tmp_path) == before


@pytest.mark.parametrize("registered", [True, False])
def test_local_alias_lookup_leaves_state_byte_identical(tmp_path, registered, forbid_locks):
    vault = _vault(tmp_path, "vault")
    if registered:
        vault_registry.register(str(vault), brain_id="named")
    forbid_locks()
    before = folder_tree(tmp_path)

    assert resolve_local_brain_alias(vault) == ("named" if registered else None)
    assert folder_tree(tmp_path) == before
