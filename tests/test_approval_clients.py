"""Native approval edits preserve non-owned policy and transport semantics."""

import json
from pathlib import Path
import tomllib

import pytest

from _bootstrap.approval_clients import Selection, desired_items, observed_items, render_items


def select(tmp_path, client="codex", surface="mcp"):
    return Selection(client, "user", surface, tmp_path, tmp_path / "bin/brain")


@pytest.mark.parametrize("header", ['[mcp_servers.brain]', '["mcp_servers"."brain"]'])
def test_codex_preserves_transport_tool_limits_siblings_and_comments(tmp_path, header):
    content = '# global\nmodel="example"\n' + header + '\n# keep\ncommand="brain"\nargs=["mcp","serve"]\ntools={read={approval_mode="prompt",output_token_limit=40}}\n[mcp_servers.other]\ncommand="other"\n'
    selection = select(tmp_path)
    values = {**observed_items(selection, content), "read": "approve", "document_structured-edit": "approve"}
    result = render_items(selection, content, values)
    before = tomllib.loads(content)
    before["mcp_servers"]["brain"]["tools"]["read"]["approval_mode"] = "approve"
    before["mcp_servers"]["brain"]["tools"]["document_structured-edit"] = {"approval_mode": "approve"}
    assert tomllib.loads(result) == before
    assert '# global' in result and '# keep' in result


def test_codex_refuses_unproven_inline_root_without_write(tmp_path):
    selection = select(tmp_path)
    content = 'mcp_servers={brain={command="brain",args=[]}}'
    with pytest.raises(ValueError, match="inline root"):
        render_items(selection, content, {"artefact_read": "approve"})


def test_claude_preserves_asks_denies_duplicates_and_other_fields(tmp_path):
    selection = select(tmp_path, "claude")
    content = json.dumps({"model": "opus", "permissions": {"ask": ["mcp__brain__*"], "deny": ["Read(secret)"],
                                                          "allow": ["Read", "Read"]}})
    values = observed_items(selection, content)
    values.update(desired_items(selection, {"artefact_read": "allow"}))
    result = json.loads(render_items(selection, content, values))
    assert result["model"] == "opus"
    assert result["permissions"] == {"ask": ["mcp__brain__*"], "deny": ["Read(secret)"],
                                     "allow": ["Read", "Read", "mcp__brain__artefact_read"]}


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_cli_projection_exact_command_first_only(tmp_path, client):
    selection = select(tmp_path, client, "cli")
    values = desired_items(selection, {json.dumps(["artefact", "read"]): "allow"})
    content = "# unrelated\n" if client == "codex" else "{}"
    assert observed_items(selection, render_items(selection, content, {**observed_items(selection, content), **values})).items() >= values.items()


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_forged_receipt_cannot_modify_non_brain_policy(tmp_path, client):
    selection = select(tmp_path, client, "cli")
    key = 'prefix_rule(pattern=["sh"], decision="allow")' if client == "codex" else "allow:Bash(*)"
    with pytest.raises(ValueError, match="outside"):
        render_items(selection, "" if client == "codex" else "{}", {key: 1})


def test_codex_tool_removal_preserves_output_policy(tmp_path):
    selection = select(tmp_path)
    content = '[mcp_servers.brain]\ncommand="brain"\n[mcp_servers.brain.tools.artefact_read]\napproval_mode="approve"\noutput_token_limit=30\n'
    result = tomllib.loads(render_items(selection, content, {}))
    assert result["mcp_servers"]["brain"]["tools"]["artefact_read"] == {"output_token_limit": 30}


def test_approval_edit_remains_compatible_with_transport_repair_and_removal(tmp_path):
    from _bootstrap.mcp_state import render_toml_config, read_toml_server_config, render_toml_without_server
    selection = select(tmp_path)
    server = {"command": "brain", "args": ["mcp", "serve"], "env": {"KEY": "value"}}
    content = render_toml_config("", server)
    managed = render_items(selection, content, {"artefact_read": "approve"})
    path = tmp_path / "config.toml"
    path.write_text(managed)
    assert read_toml_server_config(path) == server
    repaired = render_toml_config(managed, server)
    assert tomllib.loads(repaired) == tomllib.loads(managed)
    removed = render_items(selection, repaired, {})
    assert "brain" not in tomllib.loads(render_toml_without_server(removed, server)).get("mcp_servers", {})


def test_restrictive_native_policy_is_reported_without_removal(tmp_path):
    from _bootstrap.approval_clients import known_overrides
    selection = select(tmp_path, "claude")
    desired = desired_items(selection, {"artefact_read": "allow"})
    content = json.dumps({"permissions": {"ask": ["mcp__brain__*"]}})
    assert tuple(known_overrides(selection, content, desired)) == ("allow:mcp__brain__artefact_read",)
