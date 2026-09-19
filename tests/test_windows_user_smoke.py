"""Native Windows user-path smoke and portable MCP protocol tests.

Installation covers the supported win32 user path; the shared protocol contract
also runs on macOS/Linux/WSL contributor platforms.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


native_windows = pytest.mark.skipif(
    sys.platform != "win32",
    reason="native Windows smoke",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BRAIN_CORE = REPO_ROOT / "src" / "brain-core"
SCRIPTS = BRAIN_CORE / "scripts"
CLI_VERSION = re.search(
    r'^BRAIN_CLI_VERSION="(\d+\.\d+\.\d+)"$',
    (REPO_ROOT / "cli" / "brain").read_text(encoding="utf-8"),
    re.MULTILINE,
).group(1)


def _windows_smoke_env(tmp_path: Path) -> dict[str, str]:
    """Return an isolated Windows user environment for machine-level state."""
    home = tmp_path / "home"
    appdata = home / "AppData" / "Roaming"
    appdata.mkdir(parents=True)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["APPDATA"] = str(appdata)
    env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
    env["PYTHONPATH"] = os.pathsep.join([str(BRAIN_CORE), str(SCRIPTS)])
    return env


def _parse_environment(envelope: dict) -> dict[str, object]:
    assert envelope["schema"] == "brain.command-result/1"
    assert envelope["command"] == "runtime.read-environment"
    assert envelope["status"] == "ok"
    env = {
        fact["name"]: fact["value"]
        for fact in envelope["result"]["facts"]
    }
    missing = {"vault_root", "platform"} - set(env)
    assert not missing, f"environment payload missing keys {sorted(missing)}: {envelope!r}"
    return env


def test_environment_parser_requires_complete_structural_facts():
    envelope = {
        "schema": "brain.command-result/1",
        "command": "runtime.read-environment",
        "status": "ok",
        "result": {
            "facts": [
                {"name": "vault_root", "value": "C:/Brain"},
                {"name": "platform", "value": "win32"},
            ]
        },
    }

    assert _parse_environment(envelope) == {
        "vault_root": "C:/Brain",
        "platform": "win32",
    }
    envelope["result"]["facts"].pop()
    with pytest.raises(AssertionError, match="environment payload missing keys"):
        _parse_environment(envelope)


async def _call_installed_environment_read(
    vault_root: Path, env: dict[str, str], *, timeout_seconds: float = 90,
) -> dict:
    try:
        async with asyncio.timeout(timeout_seconds):
            return await _installed_environment_round_trip(vault_root, env)
    except TimeoutError as exc:
        proxy_log = vault_root / ".brain" / "local" / "diagnostics" / "proxy.log"
        diagnostics = proxy_log.read_text(encoding="utf-8") if proxy_log.is_file() else "<absent>"
        raise AssertionError(
            f"MCP smoke timed out after {timeout_seconds}s for {vault_root}"
            f"\nproxy log={diagnostics}"
        ) from exc


async def _installed_environment_round_trip(vault_root: Path, env: dict[str, str]) -> dict:
    config = json.loads((vault_root / ".mcp.json").read_text(encoding="utf-8"))
    server_config = config["mcpServers"]["brain"]
    server_env = dict(env)
    server_env.update(server_config.get("env", {}))

    params = StdioServerParameters(
        command=server_config["command"],
        args=server_config["args"],
        env=server_env,
        cwd=vault_root,
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert any(tool.name == "runtime_read-environment" for tool in tools.tools)
            assert any(tool.name == "attachment_upload" for tool in tools.tools)

            result = await session.call_tool("runtime_read-environment", {})
            if result.is_error:
                direct = subprocess.run(
                    [
                        server_config["command"],
                        str(vault_root / ".brain-core" / "scripts" / "command.py"),
                        "runtime",
                        "read-environment",
                        "--vault",
                        str(vault_root),
                        "--request-json",
                        "{}",
                        "--json",
                    ],
                    cwd=vault_root,
                    env=server_env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                proxy_log = vault_root / ".brain" / "local" / "diagnostics" / "proxy.log"
                diagnostics = (
                    f"\ndirect exit={direct.returncode}"
                    f"\ndirect stdout={direct.stdout}"
                    f"\ndirect stderr={direct.stderr}"
                    f"\nproxy log={proxy_log.read_text(encoding='utf-8') if proxy_log.is_file() else '<absent>'}"
                )
                raise AssertionError(result.content[0].text + diagnostics)
            environment = _parse_environment(result.structured_content)
            access = await session.call_tool(
                "access_status", {"target_command_id": "attachment.upload"},
            )
            assert not access.is_error, access.content
            assert access.structured_content["result"]["command"]["state"] == "authorised"
            upload = await session.call_tool(
                "attachment_upload",
                {
                    "destination_key": "windows-smoke",
                    "name": "windows-smoke.txt",
                    "content_base64": base64.b64encode(
                        b"native windows attachment"
                    ).decode("ascii"),
                },
            )
            assert not upload.is_error, upload.content[0].text
            upload_envelope = upload.structured_content
            assert upload_envelope["status"] == "ok"
            uploaded_path = upload_envelope["result"]["path"]
            assert (vault_root / uploaded_path).read_bytes() == b"native windows attachment"
            return environment


def test_installed_smoke_timeout_closes_transport_and_reports_diagnostics(tmp_path, monkeypatch):
    (tmp_path / ".mcp.json").write_text(json.dumps({
        "mcpServers": {"brain": {"command": "unused", "args": []}},
    }))
    proxy_log = tmp_path / ".brain" / "local" / "diagnostics" / "proxy.log"
    proxy_log.parent.mkdir(parents=True)
    proxy_log.write_text("proxy stalled during startup", encoding="utf-8")
    closed = []

    @asynccontextmanager
    async def stalled_transport(_params):
        try:
            await asyncio.Event().wait()
            yield  # pragma: no cover -- deliberately never starts a session
        finally:
            closed.append(True)

    monkeypatch.setattr(sys.modules[__name__], "stdio_client", stalled_transport)
    with pytest.raises(AssertionError, match="MCP smoke timed out.*") as error:
        asyncio.run(_call_installed_environment_read(tmp_path, {}, timeout_seconds=0.01))
    assert "proxy stalled during startup" in str(error.value)
    assert closed == [True]


def test_installed_smoke_protocol_contract_on_every_platform(command_vault_clone):
    from _bootstrap.mcp_state import build_mcp_config

    vault = command_vault_clone.vault_root
    runtime = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tests/fixtures/managed_proxy_runtime.py"), str(vault)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    config = build_mcp_config(runtime, vault, workspace_dir=vault)
    (vault / ".mcp.json").write_text(json.dumps({"mcpServers": {"brain": config}}))
    home = vault.parent / "proxy-home"
    env = {
        **os.environ, **command_vault_clone.environment,
        "HOME": str(home), "USERPROFILE": str(home),
    }
    for key in ("BRAIN_VAULT_ROOT", "BRAIN_WORKSPACE_DIR", "BRAIN_OWNER_CHANNEL"):
        env.pop(key, None)
    environment = asyncio.run(_call_installed_environment_read(vault, env))
    assert Path(environment["vault_root"]) == vault
    assert environment["platform"] == sys.platform


def _run_install_ps1(vault: Path, env: dict[str, str], *, launcher: str | None) -> subprocess.CompletedProcess[str]:
    args = [
        "pwsh",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(REPO_ROOT / "install.ps1"),
        "-VaultPath",
        str(vault),
    ]
    if launcher is not None:
        args.extend(["-Launcher", launcher])
    args.extend([
        "-McpScope",
        "project",
        "-Client",
        "all",
        "-NonInteractive",
    ])
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=480,
    )


@native_windows
def test_native_windows_install_and_granular_mcp_round_trip(tmp_path):
    env = _windows_smoke_env(tmp_path)
    vault = tmp_path / "Brain Vault"
    discovery_vault = tmp_path / "Discovered Python Brain Vault"

    import_check = subprocess.run(
        [
            sys.executable,
            "-c",
            "import vault_registry; import brain_mcp.server; print('ok')",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert import_check.returncode == 0, import_check.stderr
    assert import_check.stdout.strip() == "ok"

    discovery_install = _run_install_ps1(discovery_vault, env, launcher=None)
    assert discovery_install.returncode == 0, discovery_install.stderr
    assert (discovery_vault / ".mcp.json").is_file()

    install = _run_install_ps1(vault, env, launcher=sys.executable)
    assert install.returncode == 0, install.stderr
    assert (vault / ".mcp.json").is_file()
    cli = Path(env["LOCALAPPDATA"]) / "Programs" / "Brain" / "bin" / "brain.cmd"
    assert cli.is_file()
    cli_version = subprocess.run(
        [str(cli), "--version"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cli_version.returncode == 0, cli_version.stderr
    assert cli_version.stdout.strip() == f"brain {CLI_VERSION}"

    environment = asyncio.run(_call_installed_environment_read(vault, env))
    assert Path(environment["vault_root"]) == vault
    assert environment["platform"] == "win32"
