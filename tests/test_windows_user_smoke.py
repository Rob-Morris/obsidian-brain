"""Native Windows user-path smoke tests.

This file intentionally covers only the supported win32 user path. The full
test suite remains macOS/Linux/WSL contributor coverage.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows smoke")

REPO_ROOT = Path(__file__).resolve().parents[1]
BRAIN_CORE = REPO_ROOT / "src" / "brain-core"
SCRIPTS = BRAIN_CORE / "scripts"


def _windows_smoke_env(tmp_path: Path) -> dict[str, str]:
    """Return an isolated Windows user environment for machine-level state."""
    home = tmp_path / "home"
    appdata = home / "AppData" / "Roaming"
    appdata.mkdir(parents=True)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["APPDATA"] = str(appdata)
    env["PYTHONPATH"] = os.pathsep.join([str(BRAIN_CORE), str(SCRIPTS)])
    return env


# Bound on how long we wait for background warmup to compile the router before
# brain_read can serve the environment resource. The server runs warmup off the
# request thread and returns a "starting" progress response with retry_after_ms
# until the router is ready, so the smoke test honours that contract rather than
# racing the first call against a cold start (slow on the Windows runner).
_WARMUP_READY_TIMEOUT_S = 120.0


async def _call_installed_brain_read(vault_root: Path, env: dict[str, str]) -> dict:
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
            assert any(tool.name == "brain_read" for tool in tools.tools)

            deadline = asyncio.get_running_loop().time() + _WARMUP_READY_TIMEOUT_S
            while True:
                result = await session.call_tool("brain_read", {"resource": "environment"})
                payload = json.loads(result.content[0].text)
                if not result.isError:
                    return payload

                # Cold-start progress contract: retry while warmup is still
                # running; surface anything else as the failure it is.
                status = payload.get("status")
                assert status == "starting", payload
                assert asyncio.get_running_loop().time() < deadline, (
                    f"brain_read environment never became ready: {payload}"
                )
                retry_after_s = payload.get("retry_after_ms", 1000) / 1000
                await asyncio.sleep(retry_after_s)


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


def test_native_windows_install_and_mcp_brain_read_round_trip(tmp_path):
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

    environment = asyncio.run(_call_installed_brain_read(vault, env))
    assert Path(environment["vault_root"]) == vault
    assert environment["platform"] == "win32"
