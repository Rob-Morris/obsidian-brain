"""Tests for scripts/setup.py workspace setup flow."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts"))
import configure
import setup as brain_setup


@pytest.fixture
def vault(tmp_path):
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.43.6\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    (tmp_path / ".brain" / "local").mkdir(parents=True)
    return tmp_path


def test_setup_workspace_creates_binding_manifest(tmp_path, vault, monkeypatch, capsys):
    workspace = tmp_path / "demo-workspace"
    workspace.mkdir()

    monkeypatch.setattr(configure, "resolve_local_brain_vault", lambda brain_id: vault if brain_id == "brain" else None)
    monkeypatch.setattr(brain_setup, "resolve_local_brain_alias", lambda _vault_root: "brain")

    exit_code = brain_setup.main([
        "workspace",
        str(workspace),
        "--vault",
        str(vault),
        "--brain",
        "brain",
        "--json",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    manifest = workspace / ".brain" / "local" / "workspace.yaml"
    assert manifest.read_text(encoding="utf-8") == "brain: brain\nslug: demo-workspace\n"


def test_setup_workspace_errors_when_brain_id_is_unknown(tmp_path, vault, monkeypatch, capsys):
    workspace = tmp_path / "demo-workspace"
    workspace.mkdir()

    monkeypatch.setattr(configure, "resolve_local_brain_vault", lambda _brain_id: None)

    exit_code = brain_setup.main([
        "workspace",
        str(workspace),
        "--vault",
        str(vault),
        "--brain",
        "missing",
        "--json",
    ])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert "unknown local Brain ID 'missing'" in payload["steps"][0]["message"]



def test_guided_setup_orchestrates_optional_branches(tmp_path, vault, monkeypatch, capsys):
    workspace = tmp_path / "demo-workspace"
    workspace.mkdir()

    monkeypatch.setattr(configure, "resolve_local_brain_vault", lambda brain_id: vault if brain_id == "brain" else None)
    monkeypatch.setattr(brain_setup, "resolve_local_brain_alias", lambda _vault_root: "brain")
    monkeypatch.setattr(
        configure,
        "configure_mcp_action",
        lambda vault_root, **_kwargs: {
            "status": "ok",
            "steps": [{"name": "mcp_transport", "status": "changed", "message": "Configured Brain MCP transport for all (user)."}],
            "notes": ["configured project mcp"],
        },
    )

    prompts = iter([
        "brain",
        "demo-workspace",
        "",
        "",
        "",
        "y",
        "",
        "y",
        "workspace/demo",
        "workspace=demo-workspace",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt='': next(prompts))

    exit_code = brain_setup.main([
        "workspace",
        str(workspace),
        "--vault",
        str(vault),
        "--guided",
        "--json",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    step_names = [step["name"] for step in payload["steps"]]
    assert "workspace_binding" in step_names
    assert "mcp_transport" in step_names
    assert "workspace_bootstrap_agents" in step_names
    assert "workspace_bootstrap_claude" in step_names
    assert "workspace_metadata" in step_names
    manifest = (workspace / ".brain" / "local" / "workspace.yaml").read_text(encoding="utf-8")
    assert "brain: brain" in manifest
    assert "slug: demo-workspace" in manifest
    assert "workspace/demo" in manifest
    assert "workspace: demo-workspace" in manifest


def test_guided_setup_rebinds_when_user_confirms(tmp_path, vault, monkeypatch, capsys):
    workspace = tmp_path / "demo-workspace"
    workspace.mkdir()

    monkeypatch.setattr(configure, "resolve_local_brain_vault", lambda brain_id: vault if brain_id == "brain" else None)
    monkeypatch.setattr(brain_setup, "resolve_local_brain_alias", lambda _vault_root: "brain")

    calls = []

    def fake_binding_action(vault_root, *, workspace_dir, brain_id, slug, force):
        calls.append(force)
        if not force:
            return {
                "status": "error",
                "steps": [{
                    "name": "workspace_binding",
                    "status": "error",
                    "message": "already bound",
                    "reason": "already_bound",
                }],
                "notes": [],
            }
        return {
            "status": "ok",
            "steps": [{
                "name": "workspace_binding",
                "status": "changed",
                "message": "Rebound workspace binding.",
            }],
            "notes": [],
        }

    monkeypatch.setattr(configure, "configure_workspace_binding_action", fake_binding_action)

    prompts = iter([
        "brain",
        "demo-workspace",
        "y",
        "n",
        "n",
        "n",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt='': next(prompts))

    exit_code = brain_setup.main([
        "workspace",
        str(workspace),
        "--vault",
        str(vault),
        "--guided",
        "--json",
    ])

    assert exit_code == 0
    assert calls == [False, True]
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["steps"][0]["status"] == "changed"
