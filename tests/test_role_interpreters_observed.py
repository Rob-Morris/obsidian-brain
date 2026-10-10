"""Observed kernel names of real Brain processes on a temporary managed venv.

Linux reads `/proc/<pid>/comm`; macOS reads `ps -o ucomm` and skips on framework
builds, which cannot be named. Short-lived processes (the warm-up worker, a CLI
job) are observed from inside: a `.pth` hook in the temporary venv records each
interpreter's own kernel name at start-up, using the same `/proc/self/comm` and
`proc_name` reads the materialisation probe uses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import time

import pytest

from _common import _venv
from _machine.topology import find_live_brain_runtime_processes
from brain_test_support import process_diagnostics
from proxy_test_support import _read_until_id

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
FRAMEWORK_BUILD = sys.platform == "darwin" and bool(sysconfig.get_config_var("PYTHONFRAMEWORK"))

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(os.name != "posix", reason="role files are POSIX-only"),
    pytest.mark.skipif(FRAMEWORK_BUILD, reason="macOS framework builds re-exec into Python.app and cannot be named"),
]

RECORDER = '''
import json, os, sys

def _kernel_name():
    if sys.platform == "darwin":
        import ctypes
        buffer = ctypes.create_string_buffer(64)
        ctypes.CDLL("libproc.dylib").proc_name(os.getpid(), buffer, len(buffer))
        return buffer.value.decode()
    return open("/proc/self/comm").read().strip()

_log = os.environ.get("BRAIN_TEST_KERNEL_NAME_LOG")
if _log:
    with open(_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": os.getpid(), "name": _kernel_name(), "executable": sys.executable,
                                 "prefix": sys.prefix, "role": os.environ.get("BRAIN_RUNTIME_ROLE"),
                                 "argv": sys.orig_argv}) + "\\n")
'''


def _expected(role: str) -> str:
    return _venv.ROLE_INTERPRETER_NAMES[role][:_venv._KERNEL_NAME_LIMITS[sys.platform]]


def _kernel_name(pid: int) -> str:
    if sys.platform == "darwin":
        return subprocess.run(["ps", "-o", "ucomm=", "-p", str(pid)], capture_output=True, text=True,
                              check=True, timeout=10).stdout.strip()
    return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()


class NamedRuntime:
    def __init__(self, clone, python: Path, home: Path, log: Path):
        self.clone = clone
        self.vault = clone.vault_root
        self.python = python
        self.home = home
        self.log = log

    def environment(self, **extra: str) -> dict[str, str]:
        env = {**os.environ, **self.clone.environment, "HOME": str(self.home),
               "BRAIN_TEST_KERNEL_NAME_LOG": str(self.log), **extra}
        env.pop("BRAIN_RUNTIME_ROLE", None)
        return env

    def proxy_environment(self, **extra: str) -> dict[str, str]:
        """What a project-scope client config gives the proxy."""
        return self.environment(BRAIN_VAULT_ROOT=str(self.vault), PYTHONPATH=str(self.vault / ".brain-core"), **extra)

    def records(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line]

    def wait_for(self, predicate, *, timeout: float = 60) -> list[dict]:
        deadline = time.monotonic() + timeout
        while True:
            matched = [record for record in self.records() if predicate(record)]
            if matched or time.monotonic() > deadline:
                return matched
            time.sleep(0.05)


@pytest.fixture
def named_runtime(command_vault_clone, monkeypatch, tmp_path):
    vault = command_vault_clone.vault_root
    prepared = subprocess.run([sys.executable, str(FIXTURES / "managed_proxy_runtime.py"), str(vault)],
                              capture_output=True, text=True, timeout=120)
    assert prepared.returncode == 0, process_diagnostics(prepared)
    python = Path(prepared.stdout.strip())
    home = vault.parent / "proxy-home"
    packages = python.parent.parent / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    (packages / "_brain_test_kernel_names.py").write_text(RECORDER, encoding="utf-8")
    (packages / "zz-brain-test-kernel-names.pth").write_text("import _brain_test_kernel_names\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    _venv.ensure_role_interpreters(python.parent.parent)
    for role in ("mcp", "cli"):
        assert _venv.role_interpreter_usable(_venv.role_interpreter(python, role), python), role
    return NamedRuntime(command_vault_clone, python, home, tmp_path / "kernel-names.jsonl")


def _launch_proxy(runtime: NamedRuntime):
    python = str(runtime.python)
    return subprocess.Popen(
        [python, "-m", "brain_mcp.proxy", python, "brain_mcp.server"], cwd=runtime.vault,
        env=runtime.proxy_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _finish(process: subprocess.Popen) -> str:
    process.stdin.close()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    os.set_blocking(process.stderr.fileno(), False)
    return (process.stderr.read() or b"").decode(errors="replace")


class _Session:
    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.sequence = 0

    def request(self, method: str, params: dict, *, timeout: float = 60) -> dict:
        self.sequence += 1
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": self.sequence, "method": method,
                                              "params": params}) + "\n").encode())
        self.process.stdin.flush()
        messages = _read_until_id(self.process, self.sequence, timeout=timeout)
        reply = next((message for message in messages if message.get("id") == self.sequence), None)
        assert reply is not None, (method, messages, self.process.poll())
        return reply

    def call(self, name: str, arguments: dict | None = None) -> dict:
        reply = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        assert "result" in reply, reply
        return reply["result"]

    def initialise(self) -> None:
        reply = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "named-interpreters", "version": "1"}})
        assert "result" in reply, reply
        self.process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.process.stdin.flush()


def test_proxy_server_worker_and_cli_job_carry_their_role_names(named_runtime):
    runtime = named_runtime
    (runtime.vault / ".brain/local/runtime-status.json").unlink(missing_ok=True)
    process = _launch_proxy(runtime)
    session = _Session(process)
    try:
        session.initialise()
        assert _kernel_name(process.pid) == _expected("mcp"), "the project-scope proxy names itself in place"
        images = runtime.wait_for(lambda r: r["pid"] == process.pid)
        assert [(r["role"], r["name"] == _expected("mcp")) for r in images] == [(None, False), ("mcp", True)]
        assert images[-1]["executable"] == str(runtime.python) and images[-1]["prefix"] == str(runtime.python.parent.parent)

        server, = runtime.wait_for(lambda r: r["argv"][1:3] == ["-m", "brain_mcp.server"])
        assert server["name"] == _kernel_name(server["pid"]) == _expected("mcp")
        assert server["executable"] == str(runtime.python) and server["prefix"] == str(runtime.python.parent.parent)

        # Invariance: identity checks and argv-based detection see canonical `bin/python`.
        status = session.call("brain_proxy_status")["structuredContent"]["result"]["runtime"]
        assert status == {"loaded": str(runtime.python), "child": str(runtime.python), "required": str(runtime.python),
                          "state": "current", "restart_required": False}
        live = find_live_brain_runtime_processes([runtime.python])
        assert live["available"] is True
        assert {process.pid, server["pid"]} <= {entry["pid"] for entry in live["processes"][str(runtime.python)]}

        warmup = session.call("runtime_warmup")
        assert warmup["isError"] is False, warmup
        worker, = runtime.wait_for(lambda r: "--worker" in r["argv"])
        assert (worker["role"], worker["name"]) == ("mcp", _expected("mcp"))
        deadline = time.monotonic() + 120
        while session.call("runtime_status")["structuredContent"]["result"]["runtime_status"]["state"] != "ready":
            assert time.monotonic() < deadline, "warm-up did not finish"
            time.sleep(0.2)

        # A same-image restart re-execs the proxy through the owner and starts a named child.
        proxy_file = runtime.vault / ".brain-core/brain_mcp/proxy.py"
        proxy_file.write_text(proxy_file.read_text() + "\n# named interpreters handoff\n")
        restart = session.call("brain_proxy_restart")
        assert restart["isError"] is False, restart
        assert restart["structuredContent"]["result"]["handoff"]["state"] == "completed", restart
        assert session.request("ping", {}).get("result") == {}
        replacement = runtime.wait_for(lambda r: r["pid"] == process.pid and "--handoff-fd" in r["argv"])
        assert [(r["role"], r["name"]) for r in replacement] == [("mcp", _expected("mcp"))]
        assert _kernel_name(process.pid) == _expected("mcp")
        # The preflight candidate's server and the replacement's server are both named.
        servers = runtime.wait_for(lambda r: r["argv"][1:3] == ["-m", "brain_mcp.server"] and r["pid"] != server["pid"])
        assert servers and {(r["role"], r["name"]) for r in servers} == {("mcp", _expected("mcp"))}
    finally:
        stderr = _finish(process)
    assert process.returncode == 0, stderr[-4000:]

    job = subprocess.run(
        [sys.executable, "-m", "_local_cli.main", "session", "start", "--json", "--vault", str(runtime.vault)],
        cwd=runtime.vault, capture_output=True, text=True, timeout=180,
        env=runtime.environment(
            PYTHONPATH=os.pathsep.join((str(REPO_ROOT / "cli"), str(REPO_ROOT / "src/brain-core/scripts"))),
            BRAIN_CLI_BINARY=str(REPO_ROOT / "cli/brain"), BRAIN_CLI_DISTRIBUTION_ROOT=str(REPO_ROOT),
            BRAIN_VAULT_ROOT="", BRAIN_WORKSPACE_DIR=""),
    )
    assert job.returncode == 0, process_diagnostics(job)
    assert json.loads(job.stdout)["status"] == "ok"
    cli_job, = [r for r in runtime.records() if r["argv"][1:2] == [str(runtime.vault / ".brain-core/scripts/command.py")]]
    assert (cli_job["role"], cli_job["name"]) == ("cli", _expected("cli"))
    assert cli_job["executable"] == str(runtime.python)


def test_project_scope_proxy_execs_exactly_once(named_runtime):
    runtime = named_runtime

    def images_for(process):
        process.wait(timeout=60)
        return [(r["role"], r["name"] == _expected("mcp"), r["argv"][0]) for r in runtime.records() if r["pid"] == process.pid]

    proxy_argv = ["-m", "brain_mcp.proxy"]
    without_arguments = subprocess.Popen([str(runtime.python), *proxy_argv], env=runtime.proxy_environment(),
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert images_for(without_arguments) == [(None, False, str(runtime.python)), ("mcp", True, str(runtime.python))]
    assert without_arguments.returncode == 1, "usage exit after the exec, from the same PID"

    bare = subprocess.Popen(["python", *proxy_argv], executable=str(runtime.python),
                            env=runtime.proxy_environment(PATH=os.pathsep.join((str(runtime.python.parent), os.environ["PATH"]))),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert images_for(bare) == [(None, False, "python"), ("mcp", True, str(runtime.python))]

    handoff_entry = subprocess.Popen([str(runtime.python), *proxy_argv, "--check-handoff", "99"], env=runtime.proxy_environment(),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert images_for(handoff_entry) == [(None, False, str(runtime.python))]

    # `brain mcp serve` arrives named through the owner's exec, so the guard is a no-op.
    served = _venv.managed_command([str(runtime.python), *proxy_argv], role="mcp", env=runtime.proxy_environment()).popen(
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert images_for(served) == [("mcp", True, str(runtime.python))]

    for role in ("mcp", "cli"):
        _venv.role_interpreter(runtime.python, role).unlink()
    unnamed = subprocess.Popen([str(runtime.python), *proxy_argv], env=runtime.proxy_environment(),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert images_for(unnamed) == [(None, False, str(runtime.python))], "no usable role file: no exec"
