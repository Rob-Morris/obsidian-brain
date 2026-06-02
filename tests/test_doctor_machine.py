"""Tests for the legacy BRAIN_VAULT_ROOT informational finding (brain doctor)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import _bootstrap.diagnostics as diagnostics


def _make_minimal_vault(path: Path) -> Path:
    """Create a minimal Brain vault directory structure."""
    (path / ".brain-core").mkdir(parents=True)
    (path / ".brain-core" / "VERSION").write_text("0.99.0\n")
    return path


def _write_claude_mcp_json(vault: Path, server_config: dict) -> None:
    """Write a .mcp.json with the given Brain server config."""
    payload = {"mcpServers": {"brain": server_config}}
    (vault / ".mcp.json").write_text(json.dumps(payload, indent=2))


class TestCollectMcpLegacyVaultRootFindings:
    """collect_mcp_legacy_vault_root_findings emits an info finding for each
    Brain MCP registration that carries a legacy BRAIN_VAULT_ROOT env var.
    """

    def test_claude_registration_with_brain_vault_root_emits_info_finding(self, tmp_path):
        vault = _make_minimal_vault(tmp_path / "vault")
        legacy_target = tmp_path / "some-other-brain"
        _write_claude_mcp_json(
            vault,
            {
                "command": "/usr/bin/python3",
                "args": ["-m", "brain_mcp.proxy", "/usr/bin/python3", "brain_mcp.server"],
                "env": {
                    "PYTHONPATH": str(vault / ".brain-core"),
                    "BRAIN_VAULT_ROOT": str(legacy_target),
                },
            },
        )

        findings = diagnostics.collect_mcp_legacy_vault_root_findings(vault)

        assert len(findings) == 1
        finding = findings[0]
        assert finding["check"] == "mcp_legacy_vault_root"
        assert finding["severity"] == "info"
        assert finding["file"] == ".mcp.json"
        assert str(legacy_target.resolve()) in finding["message"]
        assert "BRAIN_VAULT_ROOT" in finding["message"]
        assert "no longer written by new registrations" in finding["message"]
        assert "repair" not in finding, "info finding must not carry a repair key"

    def test_claude_registration_without_brain_vault_root_emits_no_finding(self, tmp_path):
        vault = _make_minimal_vault(tmp_path / "vault")
        _write_claude_mcp_json(
            vault,
            {
                "command": "/usr/bin/python3",
                "args": ["-m", "brain_mcp.proxy", "/usr/bin/python3", "brain_mcp.server"],
                "env": {
                    "PYTHONPATH": str(vault / ".brain-core"),
                    "BRAIN_WORKSPACE_DIR": str(vault),
                },
            },
        )

        findings = diagnostics.collect_mcp_legacy_vault_root_findings(vault)

        assert findings == []

    def test_no_mcp_state_present_emits_no_finding(self, tmp_path):
        vault = _make_minimal_vault(tmp_path / "vault")
        # No .mcp.json, no .codex/config.toml, no init-state.json

        findings = diagnostics.collect_mcp_legacy_vault_root_findings(vault)

        assert findings == []

    def test_finding_is_included_in_collect_bootstrap_check_findings(self, tmp_path, monkeypatch):
        """The legacy-vault-root finding must reach the surface via collect_bootstrap_check_findings."""
        vault = _make_minimal_vault(tmp_path / "vault")
        legacy_target = tmp_path / "legacy-brain"
        _write_claude_mcp_json(
            vault,
            {
                "command": "/usr/bin/python3",
                "args": ["-m", "brain_mcp.proxy", "/usr/bin/python3", "brain_mcp.server"],
                "env": {
                    "PYTHONPATH": str(vault / ".brain-core"),
                    "BRAIN_VAULT_ROOT": str(legacy_target),
                },
            },
        )
        # Suppress other findings that need a healthy runtime or registry
        monkeypatch.setattr(diagnostics, "collect_registry_check_findings", lambda _vr: [])
        monkeypatch.setattr(diagnostics, "collect_runtime_check_findings", lambda _vr: [])
        monkeypatch.setattr(diagnostics, "collect_mcp_check_findings", lambda _vr: [])

        findings = diagnostics.collect_bootstrap_check_findings(vault)

        legacy_findings = [f for f in findings if f["check"] == "mcp_legacy_vault_root"]
        assert len(legacy_findings) == 1
        assert legacy_findings[0]["severity"] == "info"
