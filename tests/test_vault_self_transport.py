"""Tests for vault-self MCP transport mode and the converge_workspace_binding refuse-guard.

Phase 4 contract:
- apply_mcp_transport_action with vault_self=True writes project-scope MCP config
  with BRAIN_WORKSPACE_DIR=<vault> and writes NO workspace.yaml.
- converge_workspace_binding raises with code='vault_root_not_workspace' on a vault root.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts"))

from _bootstrap.mcp_state import BRAIN_SERVER_NAME
from _bootstrap.mcp_transport import apply_mcp_transport_action, InitTransportError
from _bootstrap.workspace_binding import (
    WorkspaceBindingError,
    converge_workspace_binding,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Minimal vault root fixture."""
    bc = tmp_path / ".brain-core"
    bc.mkdir(parents=True)
    (bc / "VERSION").write_text("1.0.0\n")
    (tmp_path / ".brain" / "local").mkdir(parents=True)
    return tmp_path


FAKE_PYTHON = "/fake/python3"
FAKE_SERVER_CONFIG = {
    "command": FAKE_PYTHON,
    "args": ["-m", "brain_mcp.proxy", FAKE_PYTHON, "brain_mcp.server"],
    "env": {
        "PYTHONPATH": "/vault/.brain-core",
        "BRAIN_WORKSPACE_DIR": "/vault",
    },
}


def _make_apply_result(vault_root, *, vault_self, scope="project", client_arg="all"):
    """Call apply_mcp_transport_action with mocked runtime/config helpers.

    build_mcp_config is NOT mocked — it's pure (no filesystem, no network) so we
    let it run to produce a real server config.  This lets us assert that
    BRAIN_WORKSPACE_DIR is set to the actual vault path in the config.

    Mocks the direct-write path and the claude-CLI path so tests don't
    depend on whether the claude CLI is installed on the current machine.
    """
    with (
        patch(
            "_bootstrap.mcp_transport._resolve_managed_python",
            return_value=FAKE_PYTHON,
        ),
        patch(
            "_bootstrap.mcp_transport._warn_if_user_scope_exists",
        ),
        patch(
            "_bootstrap.mcp_transport.record_init_target",
        ),
        patch(
            "_bootstrap.mcp_transport.write_project_mcp_json",
        ) as mock_write_project,
        patch(
            "_bootstrap.mcp_transport.write_codex_config",
        ) as mock_write_codex,
        patch(
            "_bootstrap.mcp_transport.ensure_claude_md",
            return_value=vault_root / "CLAUDE.md",
        ),
        patch(
            "_bootstrap.mcp_transport.ensure_session_start_hook",
            return_value=vault_root / ".claude" / "settings.local.json",
        ),
        patch(
            "_bootstrap.mcp_transport.ensure_brain_ignore_rules",
            return_value=None,
        ),
        # Disable the claude CLI path so the direct-write path is always taken.
        patch("_bootstrap.mcp_transport._has_claude_cli", return_value=False),
    ):
        result = apply_mcp_transport_action(
            vault_root,
            client_arg=client_arg,
            scope=scope,
            target_dir=vault_root,
            remove=False,
            vault_self=vault_self,
        )
        return result, mock_write_project, mock_write_codex


# ---------------------------------------------------------------------------
# Refuse-guard tests
# ---------------------------------------------------------------------------

class TestConvergeWorkspaceBindingRefuseGuard:
    """converge_workspace_binding refuses vault roots."""

    def test_raises_on_vault_root(self, vault):
        """A vault root must raise with code vault_root_not_workspace."""
        with pytest.raises(WorkspaceBindingError) as exc_info:
            converge_workspace_binding(vault, brain="some-brain", allow_rebind=False)
        assert exc_info.value.code == "vault_root_not_workspace"

    def test_raises_with_allow_rebind_true(self, vault):
        """Even with allow_rebind=True, vault root must raise."""
        with pytest.raises(WorkspaceBindingError) as exc_info:
            converge_workspace_binding(vault, brain="some-brain", allow_rebind=True)
        assert exc_info.value.code == "vault_root_not_workspace"

    def test_raises_with_slug_set(self, vault):
        """Explicit slug does not bypass the refuse-guard."""
        with pytest.raises(WorkspaceBindingError) as exc_info:
            converge_workspace_binding(vault, brain="some-brain", slug="my-slug", allow_rebind=False)
        assert exc_info.value.code == "vault_root_not_workspace"

    def test_normal_workspace_is_not_affected(self, tmp_path, vault):
        """A regular workspace (not a vault root) must still be bindable."""
        ws = tmp_path / "myworkspace"
        ws.mkdir()
        # Should not raise — just return a convergence result.
        result = converge_workspace_binding(ws, brain="some-brain", allow_rebind=False)
        assert result.brain == "some-brain"
        assert (ws / ".brain" / "local" / "workspace.yaml").is_file()

    def test_agents_md_only_workspace_is_not_refused(self, tmp_path, vault):
        """A workspace with an AGENTS.md (e.g. the dev repo) but no
        .brain-core/VERSION is NOT a vault — the narrow refuse-guard predicate
        must leave it bindable."""
        ws = tmp_path / "devrepo"
        ws.mkdir()
        (ws / "AGENTS.md").write_text("# bootstrap\n")
        result = converge_workspace_binding(ws, brain="some-brain", allow_rebind=False)
        assert result.brain == "some-brain"
        assert (ws / ".brain" / "local" / "workspace.yaml").is_file()

    def test_error_message_mentions_vault_root(self, vault):
        """The error message should mention vault root and explain the contract."""
        with pytest.raises(WorkspaceBindingError) as exc_info:
            converge_workspace_binding(vault, brain="brain", allow_rebind=False)
        msg = str(exc_info.value).lower()
        assert "vault" in msg


# ---------------------------------------------------------------------------
# Vault-self transport mode tests
# ---------------------------------------------------------------------------

class TestVaultSelfTransportMode:
    """apply_mcp_transport_action with vault_self=True writes NO workspace.yaml."""

    def test_vault_self_produces_project_config_no_workspace_yaml_claude(self, vault):
        """Claude vault-self: project config has BRAIN_WORKSPACE_DIR=<vault>, no workspace.yaml."""
        result, mock_write_project, _ = _make_apply_result(
            vault, vault_self=True, client_arg="claude"
        )

        # workspace.yaml must NOT exist.
        assert not (vault / ".brain" / "local" / "workspace.yaml").exists(), (
            "workspace.yaml should not be written in vault-self mode"
        )
        # MCP config must have been written.
        mock_write_project.assert_called_once()
        # The server config written must include BRAIN_WORKSPACE_DIR=<vault>.
        written_config = mock_write_project.call_args.args[0]
        assert written_config["env"]["BRAIN_WORKSPACE_DIR"] == str(vault), (
            f"Expected BRAIN_WORKSPACE_DIR={vault} but got {written_config['env'].get('BRAIN_WORKSPACE_DIR')!r}"
        )

    def test_vault_self_produces_project_config_no_workspace_yaml_codex(self, vault):
        """Codex vault-self: project config has BRAIN_WORKSPACE_DIR=<vault>, no workspace.yaml."""
        result, _, mock_write_codex = _make_apply_result(
            vault, vault_self=True, client_arg="codex"
        )

        assert not (vault / ".brain" / "local" / "workspace.yaml").exists(), (
            "workspace.yaml should not be written in vault-self mode"
        )
        mock_write_codex.assert_called_once()
        # The server config written must include BRAIN_WORKSPACE_DIR=<vault>.
        written_config = mock_write_codex.call_args.args[0]
        assert written_config["env"]["BRAIN_WORKSPACE_DIR"] == str(vault), (
            f"Expected BRAIN_WORKSPACE_DIR={vault} but got {written_config['env'].get('BRAIN_WORKSPACE_DIR')!r}"
        )

    def test_vault_self_produces_project_config_no_workspace_yaml_all(self, vault):
        """vault_self=True with client=all: both configs have BRAIN_WORKSPACE_DIR=<vault>, no workspace.yaml."""
        result, mock_write_project, mock_write_codex = _make_apply_result(
            vault, vault_self=True, client_arg="all"
        )

        assert not (vault / ".brain" / "local" / "workspace.yaml").exists()
        mock_write_project.assert_called_once()
        mock_write_codex.assert_called_once()
        # Both configs must carry BRAIN_WORKSPACE_DIR=<vault>.
        claude_config = mock_write_project.call_args.args[0]
        assert claude_config["env"]["BRAIN_WORKSPACE_DIR"] == str(vault)
        codex_config = mock_write_codex.call_args.args[0]
        assert codex_config["env"]["BRAIN_WORKSPACE_DIR"] == str(vault)

    def test_vault_self_false_would_raise_on_vault_root(self, vault):
        """Without vault_self=True, applying to a vault root raises (refuse-guard)."""
        with pytest.raises(InitTransportError):
            with (
                patch(
                    "_bootstrap.mcp_transport._resolve_managed_python",
                    return_value=FAKE_PYTHON,
                ),
                patch(
                    "_bootstrap.mcp_transport._warn_if_user_scope_exists",
                ),
                patch("_bootstrap.mcp_transport._has_claude_cli", return_value=False),
            ):
                apply_mcp_transport_action(
                    vault,
                    client_arg="all",
                    scope="project",
                    target_dir=vault,
                    remove=False,
                    vault_self=False,  # explicit False — must raise
                )
