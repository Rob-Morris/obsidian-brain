"""Table-driven unit tests for resolve_brain_target.

Covers the full precedence ladder (rungs 1–5), the stale-terminates hazard
(wrong-brain hazard), and a purity assertion (no side effects on os.environ
or the vault registry).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from _bootstrap.workspace_binding import (
    BrainTarget,
    WorkspaceBindingError,
    resolve_brain_target,
)
import vault_registry


# ---------------------------------------------------------------------------
# Helpers
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


def _make_workspace_no_brain_key(parent: Path, name: str = "workspace") -> Path:
    """Create a workspace with a manifest that has no 'brain' key (MISSING state)."""
    ws = parent / name
    ws.mkdir(parents=True, exist_ok=True)
    manifest_dir = ws / ".brain" / "local"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "workspace.yaml").write_text("slug: ws\n")
    return ws


def _make_workspace_brain_only(parent: Path, name: str = "workspace", *, brain: str) -> Path:
    """Create a workspace whose manifest has a 'brain' key but no 'slug'.

    Brain resolution keys only on 'brain'; a missing 'slug' must still be VALID
    (an intentional divergence from the old require_workspace_binding, which
    raised on a missing slug).
    """
    ws = parent / name
    ws.mkdir(parents=True, exist_ok=True)
    manifest_dir = ws / ".brain" / "local"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "workspace.yaml").write_text(f"brain: {brain}\n")
    return ws


def _write_default(home: Path, brain_id: str) -> None:
    """Write the default Brain ID file directly (bypassing validation)."""
    default_dir = home / ".config" / "brain"
    default_dir.mkdir(parents=True, exist_ok=True)
    (default_dir / "default").write_text(brain_id + "\n")


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


@pytest.fixture
def vaults(tmp_path):
    """Return a simple factory that creates vault roots under tmp_path."""
    created: list[Path] = []

    def factory(name: str = "vault") -> Path:
        v = _make_vault(tmp_path, name)
        created.append(v)
        return v

    return factory


# ---------------------------------------------------------------------------
# Rung 1 — BRAIN_WORKSPACE_DIR set
# ---------------------------------------------------------------------------

class TestRung1VaultSelf:
    """Rung 1: BRAIN_WORKSPACE_DIR points at a vault root → vault_self short-circuit."""

    def test_rung1_vault_root_gives_vault_self_source(self, tmp_path, isolated_home):
        """BRAIN_WORKSPACE_DIR=<vault-root> resolves to source=vault_self by path.

        No registry entry, no default, no workspace.yaml — proves the short-circuit
        bypasses binding lookup entirely (a missing binding would fall through to
        rung 3/4/5 and raise, so resolving without that means the check is working).
        """
        vault = _make_vault(tmp_path, "myvault")
        # No registry entry, no default, no workspace.yaml — pure path resolution.

        result = resolve_brain_target(
            workspace_env=str(vault),
            vault_root_env=None,
            start_dir=tmp_path,
        )
        assert result.source == "vault_self"
        assert result.vault_root == str(vault)
        assert result.workspace_dir is None

    def test_rung1_vault_self_workspace_dir_is_none(self, tmp_path, isolated_home):
        """vault_self resolution returns workspace_dir=None."""
        vault = _make_vault(tmp_path, "myvault")

        result = resolve_brain_target(
            workspace_env=str(vault),
            vault_root_env=None,
            start_dir=tmp_path,
        )
        assert result.workspace_dir is None

    def test_rung1_vault_root_resolves_by_path_not_binding(self, tmp_path, isolated_home):
        """vault-self bypasses the binding classification path entirely.

        A registered binding exists for the vault, but vault_self must not go
        through the binding path — it returns vault_self, not workspace_env.
        """
        vault = _make_vault(tmp_path, "myvault")
        brain_id = vault_registry.register(str(vault))
        # Also write a workspace.yaml in the vault root (should be ignored).
        manifest_dir = vault / ".brain" / "local"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "workspace.yaml").write_text(f"brain: {brain_id}\nslug: ws\n")

        result = resolve_brain_target(
            workspace_env=str(vault),
            vault_root_env=None,
            start_dir=tmp_path,
        )
        assert result.source == "vault_self"
        assert result.workspace_dir is None

    def test_rung1_non_vault_dir_still_uses_binding_path(self, tmp_path, isolated_home):
        """A real (non-vault) workspace dir still uses the binding path → workspace_env."""
        vault = _make_vault(tmp_path, "myvault")
        brain_id = vault_registry.register(str(vault))
        ws = _make_workspace(tmp_path, "ws", brain=brain_id)

        result = resolve_brain_target(
            workspace_env=str(ws),
            vault_root_env=None,
            start_dir=tmp_path,
        )
        assert result.source == "workspace_env"
        assert result.workspace_dir == str(ws)

    def test_rung1_agents_md_only_dir_is_not_vault_self(self, tmp_path, isolated_home):
        """BRAIN_WORKSPACE_DIR at an AGENTS.md-only dir (a real workspace such as
        the dev repo) must NOT resolve as vault_self — is_brain_vault keys on
        .brain-core/VERSION, so it uses the binding path, never vault-self."""
        vault = _make_vault(tmp_path, "thebrain")
        brain_id = vault_registry.register(str(vault))
        ws = _make_workspace(tmp_path, "devrepo", brain=brain_id)
        (ws / "AGENTS.md").write_text("# bootstrap\n")  # present, but NOT a vault

        result = resolve_brain_target(
            workspace_env=str(ws),
            vault_root_env=None,
            start_dir=tmp_path,
        )
        assert result.source == "workspace_env"
        assert result.vault_root == str(vault)


class TestRung1WorkspaceEnv:
    """Rung 1: BRAIN_WORKSPACE_DIR pinned."""

    def test_rung1_valid(self, tmp_path, isolated_home):
        """Rung 1 valid → return vault via workspace_env."""
        vault = _make_vault(tmp_path, "myvault")
        brain_id = vault_registry.register(str(vault))
        ws = _make_workspace(tmp_path, "ws", brain=brain_id)

        result = resolve_brain_target(
            workspace_env=str(ws),
            vault_root_env=None,
            start_dir=tmp_path,
        )
        assert result.vault_root == str(vault)
        assert result.workspace_dir == str(ws)
        assert result.source == "workspace_env"

    def test_rung1_stale_raises_not_falls_through(self, tmp_path, isolated_home):
        """HAZARD: Rung 1 STALE — must raise, never fall through to any lower rung.

        A valid BRAIN_VAULT_ROOT and a valid registry default are both present to
        confirm there is no fall-through — a bug would return one of them instead.
        """
        # Create a vault that IS registered but whose directory is then removed.
        vault = _make_vault(tmp_path, "stalevault")
        brain_id = vault_registry.register(str(vault))
        vault_registry.set_default(brain_id)
        ws = _make_workspace(tmp_path, "ws", brain=brain_id)

        # Also create a healthy 'fallback' vault reachable via BRAIN_VAULT_ROOT.
        fallback_vault = _make_vault(tmp_path, "fallback")

        # Now destroy the registered vault to make it stale.
        import shutil
        shutil.rmtree(str(vault))

        with pytest.raises(WorkspaceBindingError) as exc_info:
            resolve_brain_target(
                workspace_env=str(ws),
                vault_root_env=str(fallback_vault),
                start_dir=tmp_path,
            )
        assert exc_info.value.code == "stale_binding"

    def test_rung1_missing_skips_walk_goes_to_rung3(self, tmp_path, isolated_home):
        """Rung 1 MISSING (no brain key) skips rung 2 entirely.

        A valid workspace reachable by the cwd walk is present, but rung 1
        MISSING must NOT cross-resolve it.  The result must come from
        BRAIN_VAULT_ROOT (rung 3), not the cwd workspace.
        """
        # Workspace 'pinned' has a manifest but no brain key (MISSING).
        pinned_ws = _make_workspace_no_brain_key(tmp_path, "pinned")

        # Create a separate valid vault and workspace reachable via cwd walk.
        # If the walk ran, the cwd=ws_reachable_dir would find it.
        cwd_vault = _make_vault(tmp_path, "cwdvault")
        cwd_brain_id = vault_registry.register(str(cwd_vault))
        cwd_ws = _make_workspace(tmp_path, "cwd_ws", brain=cwd_brain_id)

        # The rung-3 vault: this should win.
        rung3_vault = _make_vault(tmp_path, "rung3vault")

        result = resolve_brain_target(
            workspace_env=str(pinned_ws),
            vault_root_env=str(rung3_vault),
            start_dir=cwd_ws,  # rung 2 would find cwd_ws if the walk ran
        )
        assert result.vault_root == str(rung3_vault)
        assert result.source == "vault_root_env"

    def test_rung1_missing_no_lower_rung_falls_to_rung4(self, tmp_path, isolated_home):
        """Rung 1 MISSING + no BRAIN_VAULT_ROOT → fall to rung 4 default."""
        pinned_ws = _make_workspace_no_brain_key(tmp_path, "pinned")
        default_vault = _make_vault(tmp_path, "defaultvault")
        brain_id = vault_registry.register(str(default_vault))
        vault_registry.set_default(brain_id)

        result = resolve_brain_target(
            workspace_env=str(pinned_ws),
            vault_root_env=None,
            start_dir=tmp_path,
        )
        assert result.vault_root == str(default_vault)
        assert result.source == "registry_default"

    def test_rung1_empty_string_treated_as_unset(self, tmp_path, isolated_home):
        """workspace_env="" must be treated as unset — fall through to the walk.

        An empty BRAIN_WORKSPACE_DIR is the same as not set.  It must NOT be
        treated as a pin on the current directory; that would silently suppress
        the rung-2 walk that would otherwise find the correct workspace.
        """
        vault = _make_vault(tmp_path, "vault")
        # start_dir is the vault root — the walk resolves it as vault_self.
        result = resolve_brain_target(
            workspace_env="",
            vault_root_env=None,
            start_dir=vault,
        )
        assert result.source == "vault_self"
        assert result.vault_root == str(vault)

    def test_rung1_valid_brain_without_slug(self, tmp_path, isolated_home):
        """Rung 1 valid even when the manifest omits 'slug' — resolution keys
        only on the 'brain' key."""
        vault = _make_vault(tmp_path, "myvault")
        brain_id = vault_registry.register(str(vault))
        ws = _make_workspace_brain_only(tmp_path, "ws", brain=brain_id)

        result = resolve_brain_target(
            workspace_env=str(ws),
            vault_root_env=None,
            start_dir=tmp_path,
        )
        assert result.vault_root == str(vault)
        assert result.source == "workspace_env"

    def test_rung1_nonexistent_path_is_missing_goes_to_rung3(self, tmp_path, isolated_home):
        """A BRAIN_WORKSPACE_DIR pointing at a nonexistent directory has no
        manifest → MISSING → falls to rung 3 (not an error)."""
        rung3_vault = _make_vault(tmp_path, "rung3vault")
        result = resolve_brain_target(
            workspace_env=str(tmp_path / "does-not-exist"),
            vault_root_env=str(rung3_vault),
            start_dir=tmp_path,
        )
        assert result.vault_root == str(rung3_vault)
        assert result.source == "vault_root_env"


# ---------------------------------------------------------------------------
# Rung 2 — cwd walk (only when workspace_env is unset)
# ---------------------------------------------------------------------------

class TestRung2Walk:
    """Rung 2: upward walk from start_dir."""

    def test_rung2_vault_self_from_vault_root(self, tmp_path, isolated_home):
        """Rung 2 vault_self: start_dir IS the vault root."""
        vault = _make_vault(tmp_path, "vault")

        result = resolve_brain_target(
            workspace_env=None,
            vault_root_env=None,
            start_dir=vault,
        )
        assert result.vault_root == str(vault)
        assert result.workspace_dir is None
        assert result.source == "vault_self"

    def test_rung2_vault_self_from_subdir(self, tmp_path, isolated_home):
        """Rung 2 vault_self: start_dir is a subdirectory of the vault root."""
        vault = _make_vault(tmp_path, "vault")
        subdir = vault / "deep" / "subdir"
        subdir.mkdir(parents=True)

        result = resolve_brain_target(
            workspace_env=None,
            vault_root_env=None,
            start_dir=subdir,
        )
        assert result.vault_root == str(vault)
        assert result.workspace_dir is None
        assert result.source == "vault_self"

    def test_rung2_vault_root_wins_over_colocated_workspace(self, tmp_path, isolated_home):
        """Rung 2: vault root takes precedence over a co-located workspace.yaml."""
        vault = _make_vault(tmp_path, "vault")
        # Add a workspace manifest in the same directory as the vault root.
        # This is the vault-root-wins tiebreak scenario.
        brain_id = vault_registry.register(str(vault))
        manifest_dir = vault / ".brain" / "local"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "workspace.yaml").write_text(
            f"brain: {brain_id}\nslug: ws\n"
        )

        result = resolve_brain_target(
            workspace_env=None,
            vault_root_env=None,
            start_dir=vault,
        )
        # vault_self wins — workspace_dir is None, source is vault_self.
        assert result.vault_root == str(vault)
        assert result.workspace_dir is None
        assert result.source == "vault_self"

    def test_rung2_workspace_valid(self, tmp_path, isolated_home):
        """Rung 2: workspace manifest with valid binding → workspace_binding."""
        vault = _make_vault(tmp_path, "vault")
        brain_id = vault_registry.register(str(vault))
        ws = _make_workspace(tmp_path, "ws", brain=brain_id)

        result = resolve_brain_target(
            workspace_env=None,
            vault_root_env=None,
            start_dir=ws,
        )
        assert result.vault_root == str(vault)
        assert result.workspace_dir == str(ws)
        assert result.source == "workspace_binding"

    def test_rung2_workspace_stale_raises(self, tmp_path, isolated_home):
        """HAZARD: Rung 2 STALE binding — must raise, never fall through.

        A valid BRAIN_VAULT_ROOT and valid registry default are present to confirm
        there is no fall-through.
        """
        vault = _make_vault(tmp_path, "stalevault")
        brain_id = vault_registry.register(str(vault))
        ws = _make_workspace(tmp_path, "ws", brain=brain_id)

        fallback_vault = _make_vault(tmp_path, "fallback")
        fallback_id = vault_registry.register(str(fallback_vault))
        vault_registry.set_default(fallback_id)

        # Destroy the registered vault to make the workspace binding stale.
        import shutil
        shutil.rmtree(str(vault))

        with pytest.raises(WorkspaceBindingError) as exc_info:
            resolve_brain_target(
                workspace_env=None,
                vault_root_env=str(fallback_vault),
                start_dir=ws,
            )
        assert exc_info.value.code == "stale_binding"

    def test_rung2_missing_manifest_no_brain_key_continues(self, tmp_path, isolated_home):
        """Rung 2 MISSING — manifest present but no brain key → fall to rung 3."""
        ws = _make_workspace_no_brain_key(tmp_path, "ws")
        rung3_vault = _make_vault(tmp_path, "rung3vault")

        result = resolve_brain_target(
            workspace_env=None,
            vault_root_env=str(rung3_vault),
            start_dir=ws,
        )
        assert result.vault_root == str(rung3_vault)
        assert result.source == "vault_root_env"

    def test_rung2_no_manifest_at_all_continues(self, tmp_path, isolated_home):
        """Rung 2 MISSING — no manifest found at all → fall to rung 3."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        rung3_vault = _make_vault(tmp_path, "rung3vault")

        result = resolve_brain_target(
            workspace_env=None,
            vault_root_env=str(rung3_vault),
            start_dir=empty_dir,
        )
        assert result.vault_root == str(rung3_vault)
        assert result.source == "vault_root_env"

    def test_rung2_agents_md_only_dir_does_not_resolve_as_vault_self(
        self, tmp_path, isolated_home
    ):
        """REGRESSION: AGENTS.md-only dir must NOT be treated as vault_self.

        is_vault_root() returns True for any directory with AGENTS.md — which
        includes non-vault dirs like the repo root. The rung-2 vault-self guard
        must only trigger on dirs with .brain-core/VERSION, not bare AGENTS.md.
        A valid registry default is set to confirm the walk falls through to
        rung 4 rather than misidentifying the AGENTS.md dir as vault_self.
        """
        # Create a dir with AGENTS.md but no .brain-core/VERSION (not a real vault).
        agents_only_dir = tmp_path / "agentsonly"
        agents_only_dir.mkdir()
        (agents_only_dir / "AGENTS.md").write_text("# AGENTS\n")

        # start_dir is a subdir of the AGENTS.md-only dir.
        subdir = agents_only_dir / "workspace" / "subdir"
        subdir.mkdir(parents=True)

        # Set a valid registry default — if rung 2 correctly skips agents_only_dir,
        # the resolver falls through to rung 4 and returns this vault.
        default_vault = _make_vault(tmp_path, "defaultvault")
        brain_id = vault_registry.register(str(default_vault))
        vault_registry.set_default(brain_id)

        result = resolve_brain_target(
            workspace_env=None,
            vault_root_env=None,
            start_dir=subdir,
        )
        # Must NOT resolve as vault_self to the AGENTS.md-only dir.
        assert result.source == "registry_default", (
            f"Expected registry_default but got source={result.source!r}, "
            f"vault_root={result.vault_root!r}. "
            "AGENTS.md-only dirs must not be treated as vault_self."
        )
        assert result.vault_root == str(default_vault)


# ---------------------------------------------------------------------------
# Rung 3 — BRAIN_VAULT_ROOT env var
# ---------------------------------------------------------------------------

class TestRung3VaultRootEnv:
    """Rung 3: BRAIN_VAULT_ROOT direct vault path."""

    def test_rung3_valid_vault_root(self, tmp_path, isolated_home):
        """Rung 3: BRAIN_VAULT_ROOT is set and is a vault root → vault_root_env."""
        vault = _make_vault(tmp_path, "vault")
        empty_dir = tmp_path / "start"
        empty_dir.mkdir()

        result = resolve_brain_target(
            workspace_env=None,
            vault_root_env=str(vault),
            start_dir=empty_dir,
        )
        assert result.vault_root == str(vault)
        assert result.workspace_dir is None
        assert result.source == "vault_root_env"

    def test_rung3_non_vault_root_falls_through_to_rung4(self, tmp_path, isolated_home):
        """Rung 3: BRAIN_VAULT_ROOT is set but not a vault root → fall to rung 4."""
        not_a_vault = tmp_path / "notavault"
        not_a_vault.mkdir()
        default_vault = _make_vault(tmp_path, "default")
        brain_id = vault_registry.register(str(default_vault))
        vault_registry.set_default(brain_id)
        empty_dir = tmp_path / "start"
        empty_dir.mkdir()

        result = resolve_brain_target(
            workspace_env=None,
            vault_root_env=str(not_a_vault),
            start_dir=empty_dir,
        )
        assert result.vault_root == str(default_vault)
        assert result.source == "registry_default"


# ---------------------------------------------------------------------------
# Rung 4 — registry default
# ---------------------------------------------------------------------------

class TestRung4RegistryDefault:
    """Rung 4: machine-wide registry default."""

    def test_rung4_default_resolves(self, tmp_path, isolated_home):
        """Rung 4: default resolves → registry_default."""
        vault = _make_vault(tmp_path, "vault")
        brain_id = vault_registry.register(str(vault))
        vault_registry.set_default(brain_id)
        empty_dir = tmp_path / "start"
        empty_dir.mkdir()

        result = resolve_brain_target(
            workspace_env=None,
            vault_root_env=None,
            start_dir=empty_dir,
        )
        assert result.vault_root == str(vault)
        assert result.workspace_dir is None
        assert result.source == "registry_default"

    def test_rung4_dangling_default_raises(self, tmp_path, isolated_home):
        """Rung 4: default is set but the vault directory is gone → raise."""
        vault = _make_vault(tmp_path, "vault")
        brain_id = vault_registry.register(str(vault))
        vault_registry.set_default(brain_id)

        import shutil
        shutil.rmtree(str(vault))

        empty_dir = tmp_path / "start"
        empty_dir.mkdir()

        with pytest.raises(WorkspaceBindingError) as exc_info:
            resolve_brain_target(
                workspace_env=None,
                vault_root_env=None,
                start_dir=empty_dir,
            )
        assert exc_info.value.code == "stale_binding"

    def test_rung4_dangling_default_written_directly(self, tmp_path, isolated_home):
        """Rung 4: default file written directly points to non-existent brain → raise."""
        _write_default(isolated_home, "ghost-brain")
        empty_dir = tmp_path / "start"
        empty_dir.mkdir()

        with pytest.raises(WorkspaceBindingError) as exc_info:
            resolve_brain_target(
                workspace_env=None,
                vault_root_env=None,
                start_dir=empty_dir,
            )
        assert exc_info.value.code == "stale_binding"


# ---------------------------------------------------------------------------
# Rung 5 — nothing resolved
# ---------------------------------------------------------------------------

class TestRung5Nothing:
    """Rung 5: no brain can be resolved at all."""

    def test_rung5_no_cues_raises_with_setup_message(self, tmp_path, isolated_home):
        """Rung 5: no env vars, no markers, no default → raise with setup cue."""
        empty_dir = tmp_path / "start"
        empty_dir.mkdir()

        with pytest.raises(WorkspaceBindingError) as exc_info:
            resolve_brain_target(
                workspace_env=None,
                vault_root_env=None,
                start_dir=empty_dir,
            )
        msg = str(exc_info.value).lower()
        assert "brain" in msg
        assert "setup" in msg or "bind" in msg or "default" in msg


# ---------------------------------------------------------------------------
# Purity assertion
# ---------------------------------------------------------------------------

class TestPurity:
    """resolve_brain_target must not mutate os.environ or the vault registry."""

    def test_no_side_effects_on_environ_or_registry(self, tmp_path, isolated_home):
        """Calling resolve_brain_target produces no writes to os.environ or registry."""
        vault = _make_vault(tmp_path, "vault")
        brain_id = vault_registry.register(str(vault))
        ws = _make_workspace(tmp_path, "ws", brain=brain_id)

        # Snapshot state before call.
        env_before = dict(os.environ)
        registry_before = dict(vault_registry.load_registry_entries())
        default_before = vault_registry.get_default()

        resolve_brain_target(
            workspace_env=str(ws),
            vault_root_env=None,
            start_dir=tmp_path,
        )

        # Snapshot state after call.
        env_after = dict(os.environ)
        registry_after = dict(vault_registry.load_registry_entries())
        default_after = vault_registry.get_default()

        assert env_before == env_after, "os.environ was mutated"
        assert registry_before == registry_after, "vault registry was mutated"
        assert default_before == default_after, "registry default was mutated"

    def test_no_side_effects_on_stale_path(self, tmp_path, isolated_home):
        """Purity holds even when resolve_brain_target raises (stale path)."""
        vault = _make_vault(tmp_path, "stalevault")
        brain_id = vault_registry.register(str(vault))
        ws = _make_workspace(tmp_path, "ws", brain=brain_id)

        import shutil
        shutil.rmtree(str(vault))

        env_before = dict(os.environ)
        registry_before = dict(vault_registry.load_registry_entries())
        default_before = vault_registry.get_default()

        with pytest.raises(WorkspaceBindingError):
            resolve_brain_target(
                workspace_env=str(ws),
                vault_root_env=None,
                start_dir=tmp_path,
            )

        env_after = dict(os.environ)
        registry_after = dict(vault_registry.load_registry_entries())
        default_after = vault_registry.get_default()

        assert env_before == env_after, "os.environ was mutated on error path"
        assert registry_before == registry_after, "vault registry was mutated on error path"
        assert default_before == default_after, "registry default was mutated on error path"


# ---------------------------------------------------------------------------
# BrainTarget dataclass
# ---------------------------------------------------------------------------

class TestBrainTargetDataclass:
    """BrainTarget is a frozen dataclass — field access and immutability."""

    def test_brain_target_fields(self):
        t = BrainTarget(vault_root="/a/b", workspace_dir="/c/d", source="vault_self")
        assert t.vault_root == "/a/b"
        assert t.workspace_dir == "/c/d"
        assert t.source == "vault_self"

    def test_brain_target_workspace_dir_none(self):
        t = BrainTarget(vault_root="/a/b", workspace_dir=None, source="vault_root_env")
        assert t.workspace_dir is None

    def test_brain_target_is_frozen(self):
        t = BrainTarget(vault_root="/a/b", workspace_dir=None, source="vault_self")
        with pytest.raises((AttributeError, TypeError)):
            t.vault_root = "/other"  # type: ignore[misc]
