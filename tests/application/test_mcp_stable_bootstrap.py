"""Persisted user commands run real MCP across isolated Brain runtime contracts."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CLI = Path(__file__).resolve().parents[2] / "cli"
if str(CLI) not in sys.path:
    sys.path.insert(0, str(CLI))

from _distribution import install_from_source
from _bootstrap import mcp_registration
from _bootstrap.file_transaction import apply_file_changes


def test_persisted_user_command_uses_isolated_installed_bootstrap(tmp_path, command_vault_clone):
    root = command_vault_clone.vault_root
    repo = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, str(repo / "tests/fixtures/managed_proxy_runtime.py"), str(root)],
                   check=True, capture_output=True, text=True)
    home = root.parent / "proxy-home"
    installed = install_from_source(repo, tmp_path / "installed space/bin" / ("brain.cmd" if os.name == "nt" else "brain"))
    server = mcp_registration.stable_server_config(installed.cli_binary)
    plan = mcp_registration._configure_plan(None, home, None, mcp_registration.McpScope.USER,
                                            (mcp_registration.McpClient.CLAUDE,), server)
    apply_file_changes(plan.changes())
    persisted = json.loads((home / ".claude.json").read_text())["mcpServers"]["brain"]
    env = {**os.environ, **command_vault_clone.environment, "HOME": str(home), "USERPROFILE": str(home), "PATH": "",
           "PYTHONPATH": "/foreign/imports", "PYTHONHOME": "/foreign/home",
           "BRAIN_CLI_BUNDLE": "/foreign/distribution", "BRAIN_VAULT_ROOT": "/stale/brain"}
    env.pop("BRAIN_WORKSPACE_DIR", None)
    env.pop("BRAIN_OWNER_CHANNEL", None)
    async def round_trip():
        params = StdioServerParameters(command=persisted["command"], args=persisted["args"], env=env, cwd=root)
        async with asyncio.timeout(45):
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert any(tool.name == "runtime_read-environment" for tool in tools.tools)
                    result = await session.call_tool("runtime_read-environment", {})
                    assert not result.is_error, result.content
                    assert result.structured_content["status"] == "ok"
                    facts = {fact["name"]: fact["value"] for fact in result.structured_content["result"]["facts"]}
                    assert facts["vault_root"] == str(root)

    asyncio.run(round_trip())


def test_missing_bootstrap_python_is_actionable_and_protocol_silent(tmp_path):
    installed = install_from_source(Path(__file__).resolve().parents[2], tmp_path / "prefix/bin" / ("brain.cmd" if os.name == "nt" else "brain"))
    (installed.distribution_root / ".bootstrap-python").write_text("/missing/base-python\n")
    result = subprocess.run([str(installed.cli_binary), "mcp", "serve"], capture_output=True, text=True, env={**os.environ, "PATH": ""})
    assert result.returncode == 4
    assert result.stdout == ""
    assert "--bootstrap-python" in result.stderr


def test_installed_user_lifecycle_needs_no_selected_or_default_brain(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("BRAIN_VAULT_ROOT", str(tmp_path / "deleted Brain"))
    monkeypatch.setenv("PATH", str(Path(sys.executable).resolve().parent) + os.pathsep + os.environ.get("PATH", ""))
    installed = install_from_source(Path(__file__).resolve().parents[2], tmp_path / "prefix/bin" / ("brain.cmd" if os.name == "nt" else "brain"))

    def invoke(verb, **request):
        result = subprocess.run([str(installed.cli_binary), "mcp", verb, "--request-json",
                                 json.dumps({"client": "all", "scope": "user", **request}), "--json"],
                                cwd=home, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr + result.stdout
        payload = json.loads(result.stdout)
        assert payload["status"] == "ok", payload
        return payload

    invoke("configure")
    codex = home / ".codex/config.toml"
    codex.write_text(codex.read_text() + '\n[mcp_servers.brain.tools.write]\napproval_mode = "approve"\n')
    claude_settings = home / ".claude/settings.json"
    claude_settings.parent.mkdir(exist_ok=True)
    claude_settings.write_text('{"permissions":{"ask":["mcp__brain__write"]}}\n')
    grok = home / ".grok/config.toml"
    grok.write_text(grok.read_text() + '\n[permission]\nrules = [{ action = "ask", tool = "MCPTool" }]\n')
    (home / ".claude.json").unlink()
    invoke("repair")
    assert (home / ".claude.json").exists()
    assert tomllib.loads(codex.read_text())["mcp_servers"]["brain"]["tools"]["write"]["approval_mode"] == "approve"
    assert json.loads(claude_settings.read_text())["permissions"] == {"ask": ["mcp__brain__write"]}
    assert tomllib.loads(grok.read_text())["permission"]["rules"] == [{"action": "ask", "tool": "MCPTool"}]
    invoke("configure", action="remove")
    assert not mcp_registration.user_ledger_path(home).exists()
    assert not (home / ".config/brain/default").exists()


def test_same_persisted_user_command_routes_two_bound_brains_with_distinct_contracts(tmp_path, command_vault_clone, monkeypatch):
    import vault_registry
    from _bootstrap.mcp_readiness import verify_command

    first = command_vault_clone.vault_root
    second = first.parent / "second Brain"
    shutil.copytree(first, second)
    requirements = second / ".brain-core/brain_mcp/requirements.txt"
    requirements.write_text(requirements.read_text() + "\n# A distinct installed runtime contract\n")
    home = first.parent / "proxy-home"
    for key, value in command_vault_clone.environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    repo = Path(__file__).resolve().parents[2]
    pythons = []
    for root in (first, second):
        pythons.append(subprocess.run([sys.executable, str(repo / "tests/fixtures/managed_proxy_runtime.py"), str(root)],
                                     check=True, capture_output=True, text=True).stdout.strip())
    assert pythons[0] != pythons[1]
    installed = install_from_source(repo, tmp_path / "installed space/bin" / ("brain.cmd" if os.name == "nt" else "brain"))
    plan = mcp_registration._configure_plan(None, home, None, mcp_registration.McpScope.USER,
                                            (mcp_registration.McpClient.CLAUDE,), mcp_registration.stable_server_config(installed.cli_binary))
    apply_file_changes(plan.changes())
    persisted = json.loads((home / ".claude.json").read_text())["mcpServers"]["brain"]
    workspaces = []
    for index, root in enumerate((first, second)):
        identity = f"brain-{index}"
        vault_registry.register(root, identity)
        workspace = tmp_path / f"project {index}"
        binding = workspace / ".brain/local/workspace.yaml"
        binding.parent.mkdir(parents=True)
        binding.write_text(f"brain: {identity}\nslug: project-{index}\n")
        workspaces.append(workspace)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("PYTHONHOME", "/foreign/python")
    monkeypatch.setenv("PYTHONPATH", "/foreign/imports")
    monkeypatch.setenv("BRAIN_CLI_BUNDLE", "/foreign/distribution")
    for workspace, root in zip(workspaces, (first, second)):
        verify_command(persisted, workspace, root)
