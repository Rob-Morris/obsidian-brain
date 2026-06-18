"""Regression tests for permanent MCP transport removal behaviour."""

import tomllib

import pytest

from _bootstrap import mcp_transport
from _bootstrap.mcp_state import (
    _load_init_state,
    build_mcp_config,
    read_codex_server_config,
    record_init_target,
    write_codex_config,
)


def test_remove_claude_project_registration_cleans_bootstrap_and_hook(bootstrap_vault, project, monkeypatch):
    monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault)

    record = mcp_transport.register_claude(bootstrap_vault, config, "project", project)

    assert (project / ".mcp.json").is_file()
    assert (project / "CLAUDE.md").is_file()
    assert (project / ".claude" / "settings.local.json").is_file()

    removed = mcp_transport._remove_record(bootstrap_vault, record)

    assert removed is True
    assert not (project / ".mcp.json").exists()
    assert not (project / "CLAUDE.md").exists()
    assert not (project / ".claude").exists()


def test_remove_claude_project_continues_when_bootstrap_cleanup_fails(bootstrap_vault, project, monkeypatch):
    monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault)
    record = mcp_transport.register_claude(bootstrap_vault, config, "project", project)
    record_init_target(bootstrap_vault, record)

    def fail_cleanup(*_args, **_kwargs):
        raise mcp_transport.InitTransportError("cannot read CLAUDE.md")

    monkeypatch.setattr(mcp_transport, "cleanup_claude_bootstrap", fail_cleanup)

    result = mcp_transport.apply_mcp_transport_action(
        bootstrap_vault,
        client_arg="claude",
        scope="project",
        target_dir=project,
        remove=True,
    )

    assert result["status"] == "changed"
    assert result["removed_count"] == 1
    assert _load_init_state(bootstrap_vault)["records"] == []
    assert not (project / ".mcp.json").exists()
    assert not (project / ".claude").exists()
    assert (project / "CLAUDE.md").is_file()


def test_remove_claude_project_registration_preserves_user_claude_md_content(
    bootstrap_vault, project, monkeypatch
):
    monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)
    (project / "CLAUDE.md").write_text("# My Project\n\nExisting content.\n", encoding="utf-8")
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault)

    record = mcp_transport.register_claude(bootstrap_vault, config, "project", project)

    removed = mcp_transport._remove_record(bootstrap_vault, record)

    assert removed is True
    assert not (project / ".mcp.json").exists()
    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == "# My Project\n\nExisting content.\n"
    assert not (project / ".claude").exists()


def test_remove_claude_local_registration_cleans_local_bootstrap_and_hook(
    bootstrap_vault, project, monkeypatch
):
    monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault)

    record = mcp_transport.register_claude(bootstrap_vault, config, "local", project)

    removed = mcp_transport._remove_record(bootstrap_vault, record)

    assert removed is True
    assert not (project / ".claude").exists()


def test_remove_claude_project_uses_recorded_bootstrap_line(bootstrap_vault, project, monkeypatch):
    monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault)
    record = mcp_transport.register_claude(bootstrap_vault, config, "project", project)

    recorded_line = record["bootstrap_line"]
    assert recorded_line in (project / "CLAUDE.md").read_text(encoding="utf-8")

    monkeypatch.setattr(
        mcp_transport,
        "bootstrap_line_for_target",
        lambda _target: "@.brain-core/index.md (newer-format-bootstrap)",
    )

    removed = mcp_transport._remove_record(bootstrap_vault, record)

    assert removed is True
    claude_md = project / "CLAUDE.md"
    if claude_md.exists():
        assert recorded_line not in claude_md.read_text(encoding="utf-8")


def test_remove_codex_skips_mismatched_entry(bootstrap_vault, project):
    expected = build_mcp_config("/usr/bin/python3", bootstrap_vault)
    other = build_mcp_config("/usr/local/bin/python3", bootstrap_vault)
    config_path = project / ".codex" / "config.toml"
    write_codex_config(other, config_path)

    record = {
        "client": "codex",
        "scope": "project",
        "target_path": str(project),
        "config_path": str(config_path),
        "server_config": expected,
    }

    removed = mcp_transport._remove_record(bootstrap_vault, record)

    assert removed is False
    assert config_path.is_file()
    assert read_codex_server_config(config_path) == other
    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert payload["mcp_servers"]["brain"]["command"] == other["command"]
