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
FULL_SUPPORT_CORE_VERSION = re.search(
    r'^CLI_FULL_SUPPORT_CORE_VERSION = "([^"]+)"$',
    (REPO_ROOT / "cli" / "_version_contract.py").read_text(encoding="utf-8"),
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


def test_managed_runtime_fixture_processes_reused_package_pth(command_vault_clone):
    vault = command_vault_clone.vault_root
    runner_packages = vault.parent / "runner packages"
    nested = runner_packages / "nested"
    nested.mkdir(parents=True)
    (nested / "brain_test_nested.py").write_text("READY = True\n", encoding="utf-8")
    (runner_packages / "dependency.pth").write_text(
        "nested\nimport builtins; builtins.BRAIN_TEST_PTH_READY = True\n", encoding="utf-8",
    )
    fixture = REPO_ROOT / "tests/fixtures/managed_proxy_runtime.py"
    runtime = subprocess.run(
        [sys.executable, "-c",
         "import runpy, site, sys; from pathlib import Path; "
         "site.getsitepackages = lambda: [sys.argv[2]]; "
         "fixture = runpy.run_path(sys.argv[1]); "
         "print(fixture['prepare_runtime'](Path(sys.argv[3])))",
         str(fixture), str(runner_packages), str(vault)],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    result = subprocess.run(
        [runtime, "-I", "-c", "import brain_test_nested, builtins; "
         "assert brain_test_nested.READY; assert builtins.BRAIN_TEST_PTH_READY"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


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
        [str(cli), "version", "--json"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cli_version.returncode == 0, cli_version.stderr
    version_payload = json.loads(cli_version.stdout)["result"]
    assert version_payload["cli_version"] == CLI_VERSION
    assert version_payload["distributed_with_core_version"] == (BRAIN_CORE / "VERSION").read_text().strip()
    assert version_payload["full_support_core_version"] == (
        None if FULL_SUPPORT_CORE_VERSION == "next-release" else FULL_SUPPORT_CORE_VERSION)

    environment = asyncio.run(_call_installed_environment_read(vault, env))
    assert Path(environment["vault_root"]) == vault
    assert environment["platform"] == "win32"

    env.update(PYTHONUTF8="0", PYTHONIOENCODING="cp1252")

    original = "Windows retained output 雪 😀.\n" * 180
    source = vault / "README.md"
    source.write_text(original, encoding="utf-8", newline="")

    def call(command, request, session_file=None):
        argv = [str(cli), "--vault", str(vault), *command, "--json",
                "--request-json", json.dumps(request)]
        if session_file is not None:
            argv.extend(["--result-session-file", str(session_file)])
        completed = subprocess.run(argv, cwd=tmp_path, env=env,
                                   capture_output=True, text=True, encoding="cp1252", timeout=90)
        assert completed.returncode == 0, completed.stderr or completed.stdout
        assert completed.stdout.isascii()
        return json.loads(completed.stdout)

    def content(payload):
        if "fragments" in payload:
            return "".join(item["text"] + item["line_ending"] for item in payload["fragments"])
        return payload["content"]

    first = call(["vault", "read-file"], {"path": "README.md"})
    assert first["status"] == "ok" and first["delivery"]["total_pages"] > 1
    session_file = Path(first["result_session_file"])
    credential = session_file.read_bytes()
    result_id = first["delivery"]["result_id"]
    source.write_text("Changed after opening.\n", encoding="utf-8", newline="")
    output = content(first["result"])
    page = first
    for _ in range(first["delivery"]["total_pages"] - 1):
        cursor = page["delivery"]["continuation"]
        assert cursor is not None
        page = call(["result", "read"], {"result_id": result_id, "continuation": cursor}, session_file)
        assert page["status"] == "ok"
        output += content(page["result"]["envelope"]["result"])
    assert output == original
    assert page["delivery"]["continuation"] is None
    assert session_file.read_bytes() == credential
    assert source.read_text(encoding="utf-8") == "Changed after opening.\n"

    cleared = call(["result", "clear"], {}, session_file)
    assert cleared["result"]["cleared"]
    reused = call(["vault", "read-file"], {"path": "README.md"}, session_file)
    assert content(reused["result"]) == "Changed after opening.\n"
    assert session_file.read_bytes() == credential

    from _common import document_revision_at
    memory = vault / "_Config/Memories/brain-core-reference.md"
    marker = "\nNative CP1252 once-only effect: 雪 😀.\n"
    mutation = call(["document", "write-body"], {
        "document": {"resource": "memory", "reference": "brain-core-reference"},
        "expected_revision": document_revision_at(memory), "operation": "append",
        "content": {"source": "inline", "content": marker},
    }, session_file)
    assert mutation["committed_effects"]
    after = memory.read_bytes()
    assert after.endswith(marker.encode("utf-8")) and after.count(marker.encode("utf-8")) == 1
    replay = call(["result", "read"], {"result_id": mutation["delivery"]["result_id"], "page": 0}, session_file)
    assert replay["result"]["envelope"]["committed_effects"] == mutation["committed_effects"]
    assert memory.read_bytes() == after


@native_windows
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_native_powershell_pinned_retry_round_trips_cmd_stdin_and_selectors(tmp_path, monkeypatch, shell):
    monkeypatch.syspath_prepend(str(REPO_ROOT / "cli"))
    from _local_cli.main import _pinned_retry_command
    import shutil
    # Exercise each available native shell: Windows PowerShell and PowerShell 7
    # have different native-argument passing rules.
    if not shutil.which(shell):
        pytest.skip(f"{shell} is unavailable")
    directory = tmp_path / "retry's working & directory 雪 😀"
    directory.mkdir()
    capture = directory / "capture.py"
    capture.write_text(
        "import json,os,sys; raw=sys.stdin.read(); print('stdin='+ascii(raw),file=sys.stderr); "
        "json.dump(dict(argv=sys.argv[1:], request=json.loads(raw), "
        "cwd=os.getcwd(), vault=os.environ.get('BRAIN_VAULT_ROOT'), "
        "workspace=os.environ.get('BRAIN_WORKSPACE_DIR')),sys.stdout)", encoding="utf-8")
    binary = directory / "brain.cmd"
    binary.write_text('@"' + sys.executable + '" "%~dp0capture.py" %*\n', encoding="utf-8")
    payload = {"source_root": str(directory / "pinned source"), "version": None,
               "vault_root": str(directory / "vault 雪"), "brain_id": "selected"}
    argv = [str(binary), "install", "--request-json", json.dumps(payload), "--json",
            "--vault", str(directory / "vault"), "--brain", "selected",
            "--workspace", str(directory / "workspace"), "--dry-run"]
    selected = {"BRAIN_VAULT_ROOT": str(directory / "env vault"),
                "BRAIN_WORKSPACE_DIR": str(directory / "env workspace")}
    command = _pinned_retry_command(argv, directory, selected)
    assert command.isascii()
    completed = subprocess.run([shell, "-NoProfile", "-Command", command],
                               capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout)
    expected_argv = argv[1:]
    expected_argv[expected_argv.index("--request-json") + 1] = "-"
    assert actual["argv"] == expected_argv
    assert actual["request"] == payload
    assert Path(actual["cwd"]) == directory
    assert actual["vault"] == selected["BRAIN_VAULT_ROOT"]
    assert actual["workspace"] == selected["BRAIN_WORKSPACE_DIR"]
