"""Tests for the shared Python installer core."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import stat
import sys

import pytest

from brain_test_support import copy_install_source
import vault_registry


install_core = importlib.import_module("install")


def _copy_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    copy_install_source(source)
    return source


def _runtime_result(vault_root: Path) -> dict:
    runtime = vault_root / "runtime"
    python = runtime / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("python\n", encoding="utf-8")
    return {"created": True, "venv_dir": str(runtime), "python": str(python)}


def test_install_core_scaffolds_fresh_vault_and_scrubs_machine_local_state(tmp_path):
    source = _copy_source(tmp_path)
    template = source / "template-vault"
    (template / ".venv").mkdir()
    (template / ".venv" / "source-only-marker").write_text("do not copy\n", encoding="utf-8")
    (template / ".mcp.json").write_text("stale\n", encoding="utf-8")
    (template / ".codex").mkdir(exist_ok=True)
    (template / ".codex" / "config.toml").write_text("stale\n", encoding="utf-8")
    (template / ".brain" / "local").mkdir(parents=True, exist_ok=True)
    (template / ".brain" / "local" / "session.md").write_text("stale\n", encoding="utf-8")

    vault = tmp_path / "vault"
    result = install_core.install_vault_action(
        vault,
        source_root=source,
        mcp_scope="skip",
    )

    assert result["status"] == "ok"
    assert (vault / ".brain-core" / "VERSION").is_file()
    runtime_step = next(step for step in result["steps"] if step["name"] == "machine_resolution_runtime")
    assert runtime_step["status"] == "changed"
    assert Path(runtime_step["entry"]).is_file()
    assert (vault / "AGENTS.md").is_file()
    assert not (vault / ".venv" / "source-only-marker").exists()
    assert not (vault / ".mcp.json").exists()
    assert not (vault / ".codex" / "config.toml").exists()
    assert not (vault / ".brain" / "local" / "session.md").exists()
    assert (vault / ".brain" / "local" / ".gitkeep").is_file()


def test_install_core_existing_vault_preserves_user_content_and_adds_system_scaffold(tmp_path):
    source = _copy_source(tmp_path)
    vault = tmp_path / "existing"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (vault / "Ideas").mkdir()
    (vault / "Ideas" / "note.md").write_text("keep\n", encoding="utf-8")
    (vault / "AGENTS.md").write_text("custom agents\n", encoding="utf-8")

    result = install_core.install_vault_action(
        vault,
        source_root=source,
        mcp_scope="skip",
    )

    assert result["status"] == "ok"
    assert (vault / ".brain-core" / "VERSION").is_file()
    assert (vault / "_Config").is_dir()
    assert (vault / "Ideas" / "note.md").read_text(encoding="utf-8") == "keep\n"
    assert (vault / "AGENTS.md").read_text(encoding="utf-8") == "custom agents\n"
    assert (vault / ".obsidian" / "snippets" / "brain-folder-colours.css").is_file()


def test_install_core_refuses_existing_brain_vault_and_preserves_upgrade_boundary(tmp_path):
    source = _copy_source(tmp_path)
    vault = tmp_path / "vault"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("0.1.0\n", encoding="utf-8")

    result = install_core.install_vault_action(vault, source_root=source, mcp_scope="skip")

    assert result["status"] == "error"
    assert "use upgrade.py" in result["steps"][0]["message"]
    assert (vault / ".brain-core" / "VERSION").read_text(encoding="utf-8") == "0.1.0\n"


def test_install_core_refuses_source_checkout_as_destination(tmp_path):
    source = _copy_source(tmp_path)

    result = install_core.install_vault_action(source, source_root=source, mcp_scope="skip")

    assert result["status"] == "error"
    assert "source checkout" in result["steps"][0]["message"]


def test_install_core_refuses_missing_parent_directory(tmp_path):
    source = _copy_source(tmp_path)
    destination = tmp_path / "missing" / "vault"

    result = install_core.install_vault_action(destination, source_root=source, mcp_scope="skip")

    assert result["status"] == "error"
    assert "parent directory does not exist" in result["steps"][0]["message"]


def test_install_core_validates_direct_client_argument(tmp_path):
    source = _copy_source(tmp_path)
    vault = tmp_path / "vault"

    result = install_core.install_vault_action(
        vault,
        source_root=source,
        mcp_scope="project",
        client="unknown",
    )

    assert result["status"] == "error"
    assert "invalid client" in result["steps"][0]["message"]
    assert not vault.exists()


def test_install_core_project_mcp_uses_vault_self_transport(tmp_path, monkeypatch):
    source = _copy_source(tmp_path)
    vault = tmp_path / "vault"
    calls: list[dict] = []

    monkeypatch.setattr(
        install_core,
        "ensure_central_venv",
        lambda _requirements, *, launcher: _runtime_result(vault),
    )

    def fake_apply(vault_root, **kwargs):
        calls.append({"vault_root": vault_root, **kwargs})
        return {"status": "changed", "verification_notes": ["verify"], "warnings": []}

    monkeypatch.setattr(install_core.mcp_transport, "apply_mcp_transport_action", fake_apply)

    result = install_core.install_vault_action(
        vault,
        source_root=source,
        launcher=sys.executable,
        mcp_scope="project",
        client="claude",
    )

    assert result["status"] == "ok"
    assert calls == [
        {
            "vault_root": vault,
            "client_arg": "claude",
            "scope": "project",
            "target_dir": vault,
            "remove": False,
            "vault_self": True,
        }
    ]
    assert "verify" in result["notes"]


def test_install_core_user_scope_sets_machine_default(tmp_path, monkeypatch):
    source = _copy_source(tmp_path)
    vault = tmp_path / "vault"

    monkeypatch.setattr(
        install_core,
        "ensure_central_venv",
        lambda _requirements, *, launcher: _runtime_result(vault),
    )
    monkeypatch.setattr(
        install_core.mcp_transport,
        "apply_mcp_transport_action",
        lambda vault_root, **kwargs: {"status": "changed", "verification_notes": [], "warnings": []},
    )

    result = install_core.install_vault_action(
        vault,
        source_root=source,
        launcher=sys.executable,
        mcp_scope="user",
        brain_id="my-brain",
    )

    assert result["status"] == "ok"
    assert vault_registry.get_default() == "my-brain"
    step_names = [step["name"] for step in result["steps"]]
    assert step_names.index("mcp_transport") < step_names.index("machine_default")
    assert any(step["name"] == "machine_default" and "my-brain" in step["message"] for step in result["steps"])


def test_install_core_keeps_scaffold_when_runtime_install_fails(tmp_path, monkeypatch):
    source = _copy_source(tmp_path)
    vault = tmp_path / "vault"

    def fail_runtime(_requirements, *, launcher):
        raise RuntimeError("simulated pip failure")

    monkeypatch.setattr(install_core, "ensure_central_venv", fail_runtime)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("MCP registration should not run after runtime failure")

    monkeypatch.setattr(install_core.mcp_transport, "apply_mcp_transport_action", fail_if_called)

    result = install_core.install_vault_action(
        vault,
        source_root=source,
        launcher=sys.executable,
        mcp_scope="project",
    )

    assert result["status"] == "partial"
    assert (vault / ".brain-core" / "VERSION").is_file()
    assert any(step["name"] == "managed_runtime" and step["status"] == "error" for step in result["steps"])
    assert any("Vault scaffold is present" in note for note in result["notes"])


def test_install_core_does_not_set_user_default_when_runtime_install_fails(tmp_path, monkeypatch):
    source = _copy_source(tmp_path)
    vault = tmp_path / "vault"

    def fail_runtime(_requirements, *, launcher):
        raise RuntimeError("simulated pip failure")

    monkeypatch.setattr(install_core, "ensure_central_venv", fail_runtime)

    result = install_core.install_vault_action(
        vault,
        source_root=source,
        launcher=sys.executable,
        mcp_scope="user",
        brain_id="my-brain",
    )

    assert result["status"] == "partial"
    assert vault_registry.get_default() is None
    assert "machine_default" not in [step["name"] for step in result["steps"]]


def test_install_core_registry_failure_reports_recovery_note(tmp_path, monkeypatch):
    source = _copy_source(tmp_path)
    vault = tmp_path / "vault"

    def fail_register(*_args, **_kwargs):
        raise RuntimeError("lock failed")

    monkeypatch.setattr(install_core.vault_registry, "register", fail_register)

    result = install_core.install_vault_action(vault, source_root=source, mcp_scope="skip")

    assert result["status"] == "partial"
    assert (vault / ".brain-core" / "VERSION").is_file()
    assert any(step["name"] == "vault_registry" and step["status"] == "error" for step in result["steps"])
    assert any("NOT registered" in note and "vault_registry.py --register" in note for note in result["notes"])


def test_install_core_resolves_bare_launcher_on_path(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    launcher = fake_bin / "python3.12"
    launcher.write_text("python\n", encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(fake_bin))

    assert install_core._resolve_launcher_arg("python3.12") == launcher.resolve()


def test_install_core_main_maps_unhandled_exception_to_hard_error(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"

    def crash(*_args, **_kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(install_core, "install_vault_action", crash)

    exit_code = install_core.main([str(vault), "--json"])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["steps"][0]["name"] == "install"
    assert "simulated crash" in payload["steps"][0]["message"]
