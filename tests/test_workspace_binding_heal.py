"""Tests for heal_legacy_config, resolve_and_heal, and Phase-3 invariants.

Covers:
- Each heal branch: first-run mutates, second-run is a no-op (idempotent).
- CO-FIRE: vault_self source + vault_root_env set + no workspace_env
  → BOTH backfill and default-seed fire.
- SEED SOURCE: cd'd into a different bound vault → default is vault_root_env's brain.
- DANGLING guard: vault_root_env not a vault root → no register/seed.
- HAZARD (Phase-3 negative): stale binding + valid env → resolve_and_heal RAISES
  and registry/workspace.yaml are byte-identical afterwards.
- build_mcp_config output has no BRAIN_VAULT_ROOT key.
- Write-ordering in apply_mcp_transport_action: binding converges before registration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import call, patch

import pytest

from _bootstrap.workspace_binding import (
    WORKSPACE_ERROR_FILESYSTEM_ACCESS,
    BrainTarget,
    WorkspaceBindingError,
    heal_legacy_config,
    load_workspace_manifest_state,
    resolve_and_heal,
)
from _bootstrap.mcp_state import build_mcp_config
import vault_registry


# ---------------------------------------------------------------------------
# Shared helpers — mirror test_resolve_brain_target.py helpers
# ---------------------------------------------------------------------------

def _make_vault(parent: Path, name: str = "vault") -> Path:
    """Create a minimal vault root directory."""
    vault = parent / name
    bc = vault / ".brain-core"
    bc.mkdir(parents=True)
    (bc / "VERSION").write_text("1.0.0\n")
    return vault


def _make_workspace(
    parent: Path,
    name: str = "workspace",
    *,
    brain: str | None = None,
    slug: str = "ws",
) -> Path:
    """Create a workspace directory with an optional workspace.yaml manifest."""
    ws = parent / name
    ws.mkdir(parents=True, exist_ok=True)
    if brain is not None:
        manifest_dir = ws / ".brain" / "local"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "workspace.yaml").write_text(
            f"brain: {brain}\nslug: {slug}\n"
        )
    return ws


def _registry_path(home: Path) -> Path:
    return home / ".config" / "brain" / "vaults"


def _default_path(home: Path) -> Path:
    return home / ".config" / "brain" / "default"


def _read_bytes_or_none(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Redirect HOME and unset XDG_CONFIG_HOME so all registry reads/writes
    go to a clean temporary directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


# ---------------------------------------------------------------------------
# Section 1: Trigger (1) SELF-REGISTER — vault_self source
# ---------------------------------------------------------------------------

class TestSelfRegister:
    """Trigger (1): heal_legacy_config backfills vault_self into the registry."""

    def test_self_register_first_run_mutates(self, tmp_path, isolated_home):
        """First call with vault_self → vault appears in registry."""
        vault = _make_vault(tmp_path, "myvault")
        target = BrainTarget(
            vault_root=str(vault),
            workspace_dir=None,
            source="vault_self",
        )

        heal_legacy_config(target, workspace_env=None, vault_root_env=None)

        entries = vault_registry.load_registry_entries()
        paths = {e.value for e in entries.values()}
        assert str(vault.resolve()) in paths

    def test_self_register_second_run_noop(self, tmp_path, isolated_home):
        """Second call with vault_self → idempotent; no duplicate entry."""
        vault = _make_vault(tmp_path, "myvault")
        target = BrainTarget(
            vault_root=str(vault),
            workspace_dir=None,
            source="vault_self",
        )

        heal_legacy_config(target, workspace_env=None, vault_root_env=None)
        brain_id_first = vault_registry.backfill(str(vault))

        heal_legacy_config(target, workspace_env=None, vault_root_env=None)
        brain_id_second = vault_registry.backfill(str(vault))

        # Same Brain ID; exactly one entry for this path.
        assert brain_id_first == brain_id_second
        entries = vault_registry.load_registry_entries()
        matching = [e for e in entries.values() if e.value == str(vault.resolve())]
        assert len(matching) == 1

    def test_non_vault_self_source_does_not_self_register(self, tmp_path, isolated_home):
        """Other sources must NOT trigger the self-register backfill."""
        vault = _make_vault(tmp_path, "myvault")
        target = BrainTarget(
            vault_root=str(vault),
            workspace_dir=None,
            source="vault_root_env",  # NOT vault_self
        )

        heal_legacy_config(target, workspace_env=None, vault_root_env=None)

        entries = vault_registry.load_registry_entries()
        assert not entries


# ---------------------------------------------------------------------------
# Section 2: Trigger (2) PROJECT REG — workspace_env + vault_root_env
# ---------------------------------------------------------------------------

class TestProjectReg:
    """PROJECT REG: workspace_env set + source==vault_root_env → write binding."""

    def test_project_reg_first_run_creates_binding(self, tmp_path, isolated_home):
        """First call → registers vault and writes workspace.yaml."""
        vault = _make_vault(tmp_path, "myvault")
        ws = _make_workspace(tmp_path, "myws")  # no brain key
        target = BrainTarget(
            vault_root=str(vault),
            workspace_dir=None,
            source="vault_root_env",
        )

        heal_legacy_config(
            target,
            workspace_env=str(ws),
            vault_root_env=str(vault),
        )

        # Vault should be in registry.
        brain_id = vault_registry.backfill(str(vault))
        assert brain_id

        # workspace.yaml should now have the brain key.
        manifest_path = ws / ".brain" / "local" / "workspace.yaml"
        assert manifest_path.is_file()
        content = manifest_path.read_text()
        assert brain_id in content

    def test_project_reg_second_run_noop(self, tmp_path, isolated_home):
        """Second call with an already-bound workspace → no-op (allow_rebind=False)."""
        vault = _make_vault(tmp_path, "myvault")
        ws = _make_workspace(tmp_path, "myws")
        target = BrainTarget(
            vault_root=str(vault),
            workspace_dir=None,
            source="vault_root_env",
        )

        heal_legacy_config(target, workspace_env=str(ws), vault_root_env=str(vault))
        manifest_before = (ws / ".brain" / "local" / "workspace.yaml").read_bytes()

        # Second run must not raise and must leave the file unchanged.
        heal_legacy_config(target, workspace_env=str(ws), vault_root_env=str(vault))
        manifest_after = (ws / ".brain" / "local" / "workspace.yaml").read_bytes()

        assert manifest_before == manifest_after

    def test_project_reg_only_when_source_is_vault_root_env(self, tmp_path, isolated_home):
        """PROJECT REG fires only when source==vault_root_env; other sources skip it."""
        vault = _make_vault(tmp_path, "myvault")
        ws = _make_workspace(tmp_path, "myws")
        target = BrainTarget(
            vault_root=str(vault),
            workspace_dir=None,
            source="workspace_env",  # NOT vault_root_env
        )

        heal_legacy_config(target, workspace_env=str(ws), vault_root_env=str(vault))

        # No binding should have been written.
        manifest_path = ws / ".brain" / "local" / "workspace.yaml"
        assert not manifest_path.exists()


# ---------------------------------------------------------------------------
# Section 3: Trigger (2) USER REG — default-seed
# ---------------------------------------------------------------------------

class TestUserRegDefaultSeed:
    """USER REG: no workspace_env + vault_root_env set → seed machine default."""

    def test_user_reg_seeds_default_when_none_set(self, tmp_path, isolated_home):
        """First call → vault registered and set as default."""
        vault = _make_vault(tmp_path, "myvault")
        # target.vault_root is a different vault (cd'd into something else).
        other_vault = _make_vault(tmp_path, "othervault")
        target = BrainTarget(
            vault_root=str(other_vault),
            workspace_dir=None,
            source="workspace_binding",
        )

        heal_legacy_config(
            target,
            workspace_env=None,
            vault_root_env=str(vault),
        )

        default_id = vault_registry.get_default()
        assert default_id is not None
        resolved_path = vault_registry.resolve(default_id)
        assert resolved_path == str(vault.resolve())

    def test_user_reg_second_run_noop(self, tmp_path, isolated_home):
        """Second call → default already set; no-op, default unchanged."""
        vault = _make_vault(tmp_path, "myvault")
        target = BrainTarget(
            vault_root=str(vault),
            workspace_dir=None,
            source="vault_root_env",
        )

        heal_legacy_config(target, workspace_env=None, vault_root_env=str(vault))
        default_after_first = vault_registry.get_default()

        heal_legacy_config(target, workspace_env=None, vault_root_env=str(vault))
        default_after_second = vault_registry.get_default()

        assert default_after_first == default_after_second

    def test_user_reg_does_not_override_existing_default(self, tmp_path, isolated_home):
        """When a default already exists, user-reg must not overwrite it."""
        existing_vault = _make_vault(tmp_path, "existing")
        existing_id = vault_registry.register(str(existing_vault))
        vault_registry.set_default(existing_id)

        new_vault = _make_vault(tmp_path, "newvault")
        target = BrainTarget(
            vault_root=str(new_vault),
            workspace_dir=None,
            source="vault_root_env",
        )

        heal_legacy_config(target, workspace_env=None, vault_root_env=str(new_vault))

        # Default must still be the original.
        assert vault_registry.get_default() == existing_id


# ---------------------------------------------------------------------------
# Section 4: CO-FIRE — vault_self + vault_root_env + no workspace_env
# ---------------------------------------------------------------------------

class TestCoFire:
    """BOTH triggers fire when source==vault_self and vault_root_env is set."""

    def test_co_fire_self_register_and_default_seed_both_fire(self, tmp_path, isolated_home):
        """vault_self with legacy vault_root_env → backfill + default-seed both fire."""
        vault = _make_vault(tmp_path, "myvault")
        target = BrainTarget(
            vault_root=str(vault),
            workspace_dir=None,
            source="vault_self",
        )

        heal_legacy_config(target, workspace_env=None, vault_root_env=str(vault))

        # Trigger (1): vault is in registry (self-register).
        entries = vault_registry.load_registry_entries()
        paths = {e.value for e in entries.values()}
        assert str(vault.resolve()) in paths

        # Trigger (2): default is set (user-reg default-seed).
        default_id = vault_registry.get_default()
        assert default_id is not None
        assert vault_registry.resolve(default_id) == str(vault.resolve())

    def test_co_fire_with_different_vault_root_env(self, tmp_path, isolated_home):
        """vault_self (cwd inside one vault) + vault_root_env pointing to a DIFFERENT vault.

        target.vault_root == vault_self_vault (the vault the cwd is inside).
        vault_root_env == legacy_vault (the user's previous config).

        After heal:
        - vault_self_vault is backfilled (trigger 1).
        - legacy_vault is seeded as default (trigger 2) — NOT vault_self_vault.
        """
        vault_self_vault = _make_vault(tmp_path, "vault_self")
        legacy_vault = _make_vault(tmp_path, "legacy")
        target = BrainTarget(
            vault_root=str(vault_self_vault),
            workspace_dir=None,
            source="vault_self",
        )

        heal_legacy_config(target, workspace_env=None, vault_root_env=str(legacy_vault))

        # Self-register: vault_self_vault in registry.
        entries = vault_registry.load_registry_entries()
        paths = {e.value for e in entries.values()}
        assert str(vault_self_vault.resolve()) in paths

        # Default must be seeded from vault_root_env (legacy_vault), NOT target.vault_root.
        default_id = vault_registry.get_default()
        assert default_id is not None
        assert vault_registry.resolve(default_id) == str(legacy_vault.resolve())


# ---------------------------------------------------------------------------
# Section 5: SEED SOURCE correctness
# ---------------------------------------------------------------------------

class TestSeedSource:
    """The default must always come from vault_root_env, not target.vault_root."""

    def test_seed_from_vault_root_env_not_target(self, tmp_path, isolated_home):
        """cd'd into a different bound vault: default must be vault_root_env's brain."""
        cwd_vault = _make_vault(tmp_path, "cwd_vault")
        cwd_brain_id = vault_registry.register(str(cwd_vault))
        cwd_ws = _make_workspace(tmp_path, "cwd_ws", brain=cwd_brain_id)

        legacy_vault = _make_vault(tmp_path, "legacy_vault")

        # Resolution found cwd_vault (via workspace_binding), but vault_root_env
        # points to legacy_vault — the user's old machine default.
        target = BrainTarget(
            vault_root=str(cwd_vault),
            workspace_dir=str(cwd_ws),
            source="workspace_binding",
        )

        heal_legacy_config(
            target,
            workspace_env=None,
            vault_root_env=str(legacy_vault),
        )

        # Default must be legacy_vault's brain, NOT cwd_vault.
        default_id = vault_registry.get_default()
        assert default_id is not None
        resolved = vault_registry.resolve(default_id)
        assert resolved == str(legacy_vault.resolve()), (
            f"Expected default to be legacy_vault, got {resolved!r}"
        )
        assert resolved != str(cwd_vault.resolve())


# ---------------------------------------------------------------------------
# Section 6: DANGLING guard
# ---------------------------------------------------------------------------

class TestDanglingGuard:
    """vault_root_env pointing to a non-vault path → no register or seed."""

    def test_dangling_vault_root_env_no_register_no_seed(self, tmp_path, isolated_home):
        """A path that is not a vault root must be silently ignored."""
        not_a_vault = tmp_path / "notavault"
        not_a_vault.mkdir()
        # Has no .brain-core/VERSION

        target = BrainTarget(
            vault_root=str(not_a_vault),  # resolution found something elsewhere
            workspace_dir=None,
            source="vault_root_env",
        )

        heal_legacy_config(
            target,
            workspace_env=None,
            vault_root_env=str(not_a_vault),
        )

        assert not vault_registry.load_registry_entries()
        assert vault_registry.get_default() is None

    def test_nonexistent_vault_root_env_no_register_no_seed(self, tmp_path, isolated_home):
        """A non-existent path for vault_root_env must be silently ignored."""
        nonexistent = tmp_path / "does" / "not" / "exist"

        target = BrainTarget(
            vault_root=str(tmp_path),
            workspace_dir=None,
            source="vault_root_env",
        )

        heal_legacy_config(
            target,
            workspace_env=None,
            vault_root_env=str(nonexistent),
        )

        assert not vault_registry.load_registry_entries()
        assert vault_registry.get_default() is None


# ---------------------------------------------------------------------------
# Section 7: HAZARD — stale binding must not mutate registry or workspace.yaml
# ---------------------------------------------------------------------------

class TestHazardStaleBinding:
    """Phase-3 negative: stale binding raises; heal never runs; files unchanged."""

    def test_stale_workspace_env_raises_before_heal(self, tmp_path, isolated_home):
        """HAZARD: stale rung-1 binding → resolve_and_heal raises; files byte-identical."""
        import shutil

        # Create a vault, register it, bind a workspace to it, then delete the vault.
        vault = _make_vault(tmp_path, "stalevault")
        brain_id = vault_registry.register(str(vault))
        vault_registry.set_default(brain_id)
        ws = _make_workspace(tmp_path, "myws", brain=brain_id)

        # A valid BRAIN_VAULT_ROOT fallback to prove no fall-through.
        fallback = _make_vault(tmp_path, "fallback")

        # Snapshot files before making the binding stale.
        registry_file = _registry_path(isolated_home)
        workspace_yaml = ws / ".brain" / "local" / "workspace.yaml"
        registry_before = _read_bytes_or_none(registry_file)
        yaml_before = _read_bytes_or_none(workspace_yaml)

        # Destroy the vault to create a stale binding.
        shutil.rmtree(str(vault))

        with pytest.raises(WorkspaceBindingError) as exc_info:
            resolve_and_heal(
                workspace_env=str(ws),
                vault_root_env=str(fallback),
                start_dir=tmp_path,
            )

        assert exc_info.value.code == "stale_binding"

        # Files must be byte-identical — heal must not have run.
        assert _read_bytes_or_none(registry_file) == registry_before
        assert _read_bytes_or_none(workspace_yaml) == yaml_before

    def test_stale_registry_default_raises_before_heal(self, tmp_path, isolated_home):
        """HAZARD: stale registry default → resolve_and_heal raises; files byte-identical."""
        import shutil

        vault = _make_vault(tmp_path, "stalevault")
        brain_id = vault_registry.register(str(vault))
        vault_registry.set_default(brain_id)

        registry_file = _registry_path(isolated_home)
        registry_before = _read_bytes_or_none(registry_file)
        default_file = _default_path(isolated_home)
        default_before = _read_bytes_or_none(default_file)

        shutil.rmtree(str(vault))

        with pytest.raises(WorkspaceBindingError) as exc_info:
            resolve_and_heal(
                workspace_env=None,
                vault_root_env=None,
                start_dir=tmp_path / "unrelated",
            )

        assert exc_info.value.code == "stale_binding"

        # Registry and default files must be byte-identical.
        assert _read_bytes_or_none(registry_file) == registry_before
        assert _read_bytes_or_none(default_file) == default_before


# ---------------------------------------------------------------------------
# Section 8: resolve_and_heal — successful path heals then returns target
# ---------------------------------------------------------------------------

class TestResolveAndHeal:
    """resolve_and_heal succeeds → returns resolved target after heal."""

    def test_resolve_and_heal_returns_target(self, tmp_path, isolated_home):
        """Successful resolution via vault_root_env returns a BrainTarget."""
        vault = _make_vault(tmp_path, "myvault")

        result = resolve_and_heal(
            workspace_env=None,
            vault_root_env=str(vault),
            start_dir=tmp_path / "unrelated",
        )

        assert result.vault_root == str(vault.resolve())
        assert result.source == "vault_root_env"

    def test_resolve_and_heal_heal_error_does_not_propagate(self, tmp_path, isolated_home, monkeypatch):
        """A heal error is swallowed; the resolved target is still returned."""
        import _bootstrap.workspace_binding as wb_mod

        vault = _make_vault(tmp_path, "myvault")
        brain_id = vault_registry.register(str(vault))
        vault_registry.set_default(brain_id)

        def _bad_heal(*args, **kwargs):
            raise RuntimeError("synthetic heal failure")

        monkeypatch.setattr(wb_mod, "heal_legacy_config", _bad_heal)

        # Should return a target despite heal error.
        result = resolve_and_heal(
            workspace_env=None,
            vault_root_env=None,
            start_dir=tmp_path / "unrelated",
        )
        assert result.source == "registry_default"

    def test_unreadable_workspace_manifest_uses_filesystem_code(self, tmp_path, isolated_home, monkeypatch):
        """Manifest read failures carry filesystem_access so callers can give correct remediation."""
        import _bootstrap.workspace_binding as wb_mod

        ws = _make_workspace(tmp_path, "myws")
        manifest_dir = ws / ".brain" / "local"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "workspace.yaml").write_text("brain: x\nslug: myws\n")

        monkeypatch.setattr(wb_mod, "load_mapping_file", lambda _path: (_ for _ in ()).throw(PermissionError("denied")))

        with pytest.raises(WorkspaceBindingError) as exc_info:
            load_workspace_manifest_state(ws)

        assert exc_info.value.code == WORKSPACE_ERROR_FILESYSTEM_ACCESS

    def test_unreadable_registry_default_uses_filesystem_code(self, tmp_path, isolated_home, monkeypatch):
        monkeypatch.setattr(
            vault_registry,
            "get_default",
            lambda: (_ for _ in ()).throw(vault_registry.RegistryReadError("denied")),
        )

        with pytest.raises(WorkspaceBindingError) as exc_info:
            resolve_and_heal(
                workspace_env=None,
                vault_root_env=None,
                start_dir=tmp_path / "unrelated",
            )

        assert exc_info.value.code == WORKSPACE_ERROR_FILESYSTEM_ACCESS


# ---------------------------------------------------------------------------
# Section 9: build_mcp_config has no BRAIN_VAULT_ROOT
# ---------------------------------------------------------------------------

class TestBuildMcpConfigNoVaultRoot:
    """New registrations must not carry BRAIN_VAULT_ROOT in their env block."""

    def _make_vault(self, tmp_path: Path) -> Path:
        vault = tmp_path / "vault"
        bc = vault / ".brain-core"
        bc.mkdir(parents=True)
        (bc / "VERSION").write_text("1.0.0\n")
        return vault

    def test_no_brain_vault_root_without_workspace(self, tmp_path):
        """build_mcp_config(vault_only) → no BRAIN_VAULT_ROOT in env."""
        vault = self._make_vault(tmp_path)
        config = build_mcp_config("/usr/bin/python3", vault)
        assert "BRAIN_VAULT_ROOT" not in config["env"]

    def test_no_brain_vault_root_with_workspace(self, tmp_path):
        """build_mcp_config(vault + workspace_dir) → no BRAIN_VAULT_ROOT in env."""
        vault = self._make_vault(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        config = build_mcp_config("/usr/bin/python3", vault, workspace_dir=workspace)
        assert "BRAIN_VAULT_ROOT" not in config["env"]
        assert config["env"]["BRAIN_WORKSPACE_DIR"] == str(workspace)

    def test_pythonpath_and_workspace_present(self, tmp_path):
        """Ensure PYTHONPATH is still set, and BRAIN_WORKSPACE_DIR when provided."""
        vault = self._make_vault(tmp_path)
        config = build_mcp_config("/usr/bin/python3", vault)
        assert "PYTHONPATH" in config["env"]
        assert "BRAIN_WORKSPACE_DIR" not in config["env"]


# ---------------------------------------------------------------------------
# Section 10: Write-ordering in apply_mcp_transport_action
# ---------------------------------------------------------------------------

class TestWriteOrderingApplyMcpTransport:
    """Workspace binding converges BEFORE any MCP registration is written."""

    def _make_vault_with_managed_runtime(self, tmp_path: Path) -> Path:
        """Minimal vault with runtime requirements for mcp_transport."""
        vault = tmp_path / "vault"
        bc = vault / ".brain-core"
        bc.mkdir(parents=True)
        (bc / "VERSION").write_text("1.0.0\n")
        (bc / "brain_mcp").mkdir()
        (bc / "brain_mcp" / "requirements.txt").write_text("mcp>=1.0.0\n")
        brain_dir = vault / ".brain" / "local"
        brain_dir.mkdir(parents=True)
        return vault

    def test_binding_written_before_registration(self, tmp_path, isolated_home, monkeypatch):
        """apply_mcp_transport_action: binding is present before register_claude is called.

        Strategy: monkeypatch register_claude to raise on its first call. After
        the exception, the workspace.yaml must already exist — proving convergence
        happened before registration.
        """
        from _bootstrap import mcp_transport

        vault = self._make_vault_with_managed_runtime(tmp_path)
        ws = tmp_path / "myproject"
        ws.mkdir()

        # Register the vault so the manifest convergence can look up the Brain ID.
        vault_registry.register(str(vault))

        # Patch _resolve_managed_python to return a fake python path.
        monkeypatch.setattr(
            mcp_transport,
            "_resolve_managed_python",
            lambda *_args, **_kwargs: "/usr/bin/python3",
        )

        # Patch register_claude to raise before writing anything.
        def _raising_register_claude(*_args, **_kwargs):
            raise OSError("simulated registration failure")

        monkeypatch.setattr(mcp_transport, "register_claude", _raising_register_claude)

        manifest_path = ws / ".brain" / "local" / "workspace.yaml"
        assert not manifest_path.exists(), "workspace.yaml must not exist yet"

        from _bootstrap.mcp_transport import InitTransportError, apply_mcp_transport_action

        with pytest.raises(InitTransportError):
            apply_mcp_transport_action(
                vault,
                client_arg="claude",
                scope="project",
                target_dir=ws,
                remove=False,
            )

        # Binding must have been written before the registration attempt failed.
        assert manifest_path.exists(), (
            "workspace.yaml must exist after apply_mcp_transport_action raises "
            "— convergence runs before registration"
        )

    def test_binding_written_before_codex_registration(self, tmp_path, isolated_home, monkeypatch):
        """Same ordering invariant verified for the codex client path."""
        from _bootstrap import mcp_transport

        vault = self._make_vault_with_managed_runtime(tmp_path)
        ws = tmp_path / "codexproject"
        ws.mkdir()

        vault_registry.register(str(vault))

        monkeypatch.setattr(
            mcp_transport,
            "_resolve_managed_python",
            lambda *_args, **_kwargs: "/usr/bin/python3",
        )

        def _raising_register_codex(*_args, **_kwargs):
            raise OSError("simulated codex registration failure")

        monkeypatch.setattr(mcp_transport, "register_codex", _raising_register_codex)

        manifest_path = ws / ".brain" / "local" / "workspace.yaml"

        from _bootstrap.mcp_transport import InitTransportError, apply_mcp_transport_action

        with pytest.raises(InitTransportError):
            apply_mcp_transport_action(
                vault,
                client_arg="codex",
                scope="project",
                target_dir=ws,
                remove=False,
            )

        assert manifest_path.exists(), (
            "workspace.yaml must exist — convergence runs before codex registration"
        )


def test_rung4_default_resolution_writes_no_workspace_yaml(tmp_path, isolated_home):
    """Situation 4: a brain-less workspace (no anchor, no binding) resolving to
    the machine default must NOT have a workspace.yaml written by heal — this case
    is resolution-only (rung 4), never a heal mutation."""
    default_vault = _make_vault(tmp_path, "defaultvault")
    brain_id = vault_registry.register(str(default_vault))
    vault_registry.set_default(brain_id)
    ws = tmp_path / "brainless_ws"
    ws.mkdir()

    target = resolve_and_heal(workspace_env=None, vault_root_env=None, start_dir=ws)

    assert target.source == "registry_default"
    assert target.vault_root == str(default_vault)
    assert not (ws / ".brain" / "local" / "workspace.yaml").exists()
