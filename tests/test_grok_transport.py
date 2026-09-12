"""Native Grok setup preserves client boundaries and owned-file lifecycles."""

from pathlib import Path
import json
import tomllib

import pytest

from _bootstrap import agent_skills, diagnostics, grok_mcp, mcp_transport
from _bootstrap.file_transaction import FilePlan, apply_file_changes
from _bootstrap.mcp_state import (
    GROK_CONFIG_REL,
    GROK_RULE_REL,
    build_mcp_config,
    record_init_target,
)


def _server(vault, project=None):
    return build_mcp_config("/managed/python", vault, workspace_dir=project)


@pytest.mark.parametrize("scope", ["project", "user"])
def test_native_registration_and_owned_removal(
    bootstrap_vault, project, fake_home, scope
):
    root = project if scope == "project" else fake_home
    target = project if scope == "project" else None
    config = root / GROK_CONFIG_REL
    config.parent.mkdir(parents=True)
    config.write_text(
        '[ui]\ntheme = "dark"\n\n[mcp_servers.other]\ncommand = "other"\n'
    )
    server = _server(bootstrap_vault, target)
    record = mcp_transport.register_grok(server, scope, target)
    data = tomllib.loads(config.read_text())
    assert data["mcp_servers"]["brain"] == server
    assert data["mcp_servers"]["other"]["command"] == "other"
    assert data["ui"]["theme"] == "dark"
    assert (root / GROK_RULE_REL).read_text() == grok_mcp.RULE_CONTENT
    assert not (root / ".claude").exists()
    assert not (root / ".codex").exists()
    before = config.read_bytes()
    assert mcp_transport.register_grok(server, scope, target) == record
    assert config.read_bytes() == before
    assert mcp_transport._remove_record(bootstrap_vault, record)
    assert not (root / GROK_RULE_REL).exists()
    assert "brain" not in tomllib.loads(config.read_text())["mcp_servers"]
    assert mcp_transport._remove_record(bootstrap_vault, record)


@pytest.mark.parametrize("collision", ["custom", "symlink", "malformed"])
def test_native_preflight_prevents_partial_registration(
    bootstrap_vault, project, collision
):
    rule = project / GROK_RULE_REL
    rule.parent.mkdir(parents=True)
    config = project / GROK_CONFIG_REL
    if collision == "symlink":
        original = project / "custom.md"
        original.write_text("custom")
        rule.symlink_to(original)
    elif collision == "custom":
        rule.write_text("custom")
    else:
        config.write_text("this is not TOML")
    before = config.read_bytes() if config.exists() else None
    with pytest.raises((ValueError, OSError)):
        mcp_transport.register_grok(_server(bootstrap_vault), "project", project)
    assert (config.read_bytes() if config.exists() else None) == before
    if collision == "malformed":
        assert not rule.exists()


def test_repair_preserves_user_options_and_removal_preserves_modified_entry(
    bootstrap_vault, project
):
    old = _server(bootstrap_vault)
    mcp_transport.register_grok(old, "project", project)
    config = project / GROK_CONFIG_REL
    text = config.read_text().replace(
        "[mcp_servers.brain]\n",
        "[mcp_servers.brain]\nenabled = false\nstartup_timeout_sec = 90\n",
    )
    config.write_text(text)
    new = {**old, "command": "/new/python"}
    record = mcp_transport.register_grok(new, "project", project)
    server = tomllib.loads(config.read_text())["mcp_servers"]["brain"]
    assert server["enabled"] is False
    assert server["startup_timeout_sec"] == 90
    assert server["command"] == "/new/python"
    assert not mcp_transport._remove_record(bootstrap_vault, record)
    assert config.exists()
    assert (project / GROK_RULE_REL).exists()


def test_missing_config_removal_cleans_rule_but_keeps_edited_rule(
    bootstrap_vault, project
):
    record = mcp_transport.register_grok(_server(bootstrap_vault), "project", project)
    (project / GROK_CONFIG_REL).unlink()
    rule = project / GROK_RULE_REL
    rule.write_text("my edited rule")
    assert not mcp_transport._remove_record(bootstrap_vault, record)
    assert rule.read_text() == "my edited rule"
    rule.write_text(grok_mcp.RULE_CONTENT)
    assert mcp_transport._remove_record(bootstrap_vault, record)
    assert not rule.exists()


def test_grok_has_no_local_scope():
    with pytest.raises(mcp_transport.InitTransportError, match="Grok"):
        mcp_transport._resolve_clients_or_error("grok", "local")


def test_standalone_bootstrap_and_skill_ownership(bootstrap_vault, project, fake_home):
    import configure

    result = configure.configure_workspace_bootstrap_action(
        bootstrap_vault, workspace_dir=project, surface="grok"
    )
    assert result["steps"][0]["status"] == "changed"
    assert not (project / "CLAUDE.md").exists()
    assert not (project / "AGENTS.md").exists()
    result = configure.configure_workspace_bootstrap_action(
        bootstrap_vault, workspace_dir=project, surface="grok", remove=True
    )
    assert result["steps"][0]["status"] == "changed"
    assert not (project / GROK_RULE_REL).exists()
    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=fake_home, client="grok"
    )
    assert steps[0]["status"] == "changed"
    skill = fake_home / ".grok/skills/shaping/SKILL.md"
    assert skill.is_file()
    skill.write_text("custom")
    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=fake_home, client="grok", remove=True
    )
    assert steps[0]["status"] == "error"
    assert skill.read_text() == "custom"


def test_grok_only_health_and_repair(bootstrap_vault, monkeypatch):
    import _repair_runtime

    server = _server(bootstrap_vault, bootstrap_vault)
    monkeypatch.setattr(
        diagnostics, "_expected_project_server_config", lambda _vault: server
    )
    monkeypatch.setattr(
        diagnostics, "inspect_runtime", lambda _vault: {"healthy": True}
    )
    record = mcp_transport.register_grok(server, "project", bootstrap_vault)
    record_init_target(bootstrap_vault, record)
    assert diagnostics.inspect_mcp(bootstrap_vault)["grok"]["healthy"]
    (bootstrap_vault / GROK_RULE_REL).unlink()
    assert not diagnostics.inspect_mcp(bootstrap_vault)["grok"]["healthy"]
    assert any(
        "Grok" in f["message"]
        for f in diagnostics.collect_mcp_check_findings(bootstrap_vault)
    )
    result = _repair_runtime.repair_mcp(bootstrap_vault, dry_run=True)
    assert (
        next(s for s in result["steps"] if s["name"] == "grok_project")["status"]
        == "planned"
    )
    assert not (bootstrap_vault / GROK_RULE_REL).exists()
    result = _repair_runtime.repair_mcp(bootstrap_vault, dry_run=False)
    assert (
        next(s for s in result["steps"] if s["name"] == "grok_project")["status"]
        == "changed"
    )
    assert diagnostics.inspect_mcp(bootstrap_vault)["grok"]["healthy"]


def test_unrelated_array_tables_survive_configure_and_remove(bootstrap_vault, project):
    record = mcp_transport.register_grok(_server(bootstrap_vault), "project", project)
    config = project / GROK_CONFIG_REL
    config.write_text(
        config.read_text()
        + '\n[[marketplace.sources]]\nname = "personal"\nurl = "https://example.org/plugins"\n'
    )
    mcp_transport.register_grok(_server(bootstrap_vault), "project", project)
    assert (
        tomllib.loads(config.read_text())["marketplace"]["sources"][0]["name"]
        == "personal"
    )
    assert mcp_transport._remove_record(bootstrap_vault, record)
    assert (
        tomllib.loads(config.read_text())["marketplace"]["sources"][0]["name"]
        == "personal"
    )


def test_malformed_parent_table_and_rule_conflict_are_structured(
    bootstrap_vault, project, monkeypatch
):
    import configure

    config = project / GROK_CONFIG_REL
    config.parent.mkdir(parents=True)
    config.write_text("mcp_servers = []\n")
    with pytest.raises(ValueError, match="must be a table"):
        mcp_transport.register_grok(_server(bootstrap_vault), "project", project)
    rule = project / GROK_RULE_REL
    rule.parent.mkdir(parents=True)
    rule.write_text("custom")
    result = configure.configure_workspace_bootstrap_action(
        bootstrap_vault, workspace_dir=project, surface="all"
    )
    assert result["steps"][0]["status"] == "error"
    assert not (project / "AGENTS.md").exists()
    assert not (project / "CLAUDE.md").exists()
    monkeypatch.setattr(
        mcp_transport, "_resolve_managed_python", lambda _vault: "/managed/python"
    )
    with pytest.raises(mcp_transport.InitTransportError):
        mcp_transport.apply_mcp_transport_action(
            bootstrap_vault,
            client_arg="grok",
            scope="project",
            target_dir=project,
            remove=False,
            vault_self=True,
        )


def test_non_utf8_rule_is_drift(bootstrap_vault, monkeypatch):
    server = _server(bootstrap_vault, bootstrap_vault)
    monkeypatch.setattr(
        diagnostics, "_expected_project_server_config", lambda _vault: server
    )
    record = mcp_transport.register_grok(server, "project", bootstrap_vault)
    record_init_target(bootstrap_vault, record)
    (bootstrap_vault / GROK_RULE_REL).write_bytes(b"\xff")
    assert not diagnostics.inspect_mcp(bootstrap_vault)["grok"]["healthy"]
    assert any(
        "Grok" in f["message"]
        for f in diagnostics.collect_mcp_check_findings(bootstrap_vault)
    )


def test_standalone_bootstrap_reports_surviving_transaction_effects(
    bootstrap_vault, project, monkeypatch
):
    import configure
    from _bootstrap.file_transaction import FileTransactionError

    rule = project / GROK_RULE_REL

    def fail_apply(changes):
        rule.parent.mkdir(parents=True, exist_ok=True)
        rule.write_text(grok_mcp.RULE_CONTENT)
        raise FileTransactionError("rollback failed", (rule,))

    monkeypatch.setattr(grok_mcp, "apply_file_changes", fail_apply)
    result = configure.configure_workspace_bootstrap_action(
        bootstrap_vault, workspace_dir=project, surface="grok"
    )
    assert [step["status"] for step in result["steps"]] == ["changed", "error"]
    assert result["steps"][0]["name"] == "workspace_bootstrap_grok"
    assert str(rule) in result["steps"][0]["message"]
