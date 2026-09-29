"""Shared proxy test transport, in-process doubles and minimal server harnesses.

Scenario-specific servers and assertions belong beside their tests. Callers own
process/thread lifetimes; mutable state belongs to each test, never this module."""

import json
import logging
import os
import sys
import textwrap
import threading
import time
from types import SimpleNamespace
from brain_mcp import proxy as proxy_mod
from brain_mcp._command_adapter import application_interface_header
from _application.registry import current_application_catalogue
from brain_mcp._proxy_handoff import RawLineReader


PROXY_SCRIPT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src", "brain-core")
)


PROXY_MODULE = "brain_mcp.proxy"


PYTHON = sys.executable


_GIVE_UP_MSG_FRAGMENT = "recovery attempts"


def lifecycle_request(name="brain_proxy_refresh", request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": {}}}


def drive_lifecycle(relay, name="brain_proxy_refresh"):
    """Drive the wired admission/preparation/completion path deterministically."""
    if relay._client_protocol is None:
        relay._client_protocol = "modern"
    reader = RawLineReader(0)
    incoming, outgoing = os.pipe()
    relay._wake_write = outgoing
    try:
        code = relay._admit_lifecycle(lifecycle_request(name), reader)
        if code:
            return code
        relay._prepare_lifecycle()
        relay._finish_prepared_handoff(reader)
        response = relay._outbound.get_nowait()["result"]["structuredContent"]
        return response.get("error", {}).get("code")
    finally:
        relay._wake_write = None
        os.close(incoming)
        os.close(outgoing)


def _make_jsonrpc(method: str, id: int | str | None = None, params: dict | None = None) -> str:
    """Build a JSON-RPC request as a newline-terminated NDJSON line."""
    obj: dict = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        obj["id"] = id
    if params is not None:
        obj["params"] = params
    return json.dumps(obj) + "\n"


def _make_response(id: int | str | None, result: dict) -> str:
    """Build a JSON-RPC success response as a newline-terminated NDJSON line."""
    return json.dumps({"jsonrpc": "2.0", "id": id, "result": result}) + "\n"


def _write_vault(tmp_path, version: str = "1.0.0") -> None:
    """Create the minimal vault structure the proxy needs."""
    bc = tmp_path / ".brain-core"
    bc.mkdir(exist_ok=True)
    (bc / "VERSION").write_text(version)
    (bc / "session-core.md").write_text("# Session Core\n")
    # Proxy writes a log file; give it somewhere to put it
    log_dir = tmp_path / ".brain" / "local"
    log_dir.mkdir(parents=True, exist_ok=True)


def _launch_proxy(
    tmp_path,
    server_script: str,
    *,
    extra_env: dict | None = None,
) -> "subprocess.Popen":
    """Launch the proxy against a server script, returning the Popen handle."""
    import subprocess

    env = os.environ.copy()
    env["BRAIN_VAULT_ROOT"] = str(tmp_path)
    env["PYTHONPATH"] = PROXY_SCRIPT
    if extra_env:
        env.update(extra_env)

    return subprocess.Popen(
        [PYTHON, "-m", PROXY_MODULE, PYTHON, server_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        # cwd isolation: prevents the rung-2 walk from wandering out of
        # tmp_path and finding markers in the real working tree.
        cwd=str(tmp_path),
    )


def _read_responses(proc, *, timeout: float = 5.0, count: int = 1) -> list[dict]:
    """
    Read up to `count` JSON objects from proc.stdout within `timeout` seconds.
    Uses select on POSIX so we don't block forever.
    """
    return _read_json_messages(proc, timeout=timeout, max_count=count)


def _read_json_messages(
    proc,
    *,
    timeout: float,
    max_count: int | None = None,
    idle: float | None = None,
    stop_id: int | str | None = None,
) -> list[dict]:
    """Read NDJSON messages from a subprocess pipe without text-buffer races.

    These tests mix notifications and responses on the same stdout pipe. Using
    ``select()`` with ``TextIOWrapper.readline()`` is flaky because one read can
    buffer multiple lines in user space, leaving the fd unreadable while a
    second message is already waiting in Python's text buffer. Read bytes from
    the fd directly instead so readiness and consumption stay aligned.
    """
    import select

    fd = proc.stdout.fileno()
    results: list[dict] = []
    buf = getattr(proc, "_ndjson_buffer", b"")
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        while b"\n" in buf:
            raw_line, buf = buf.split(b"\n", 1)
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            results.append(msg)
            if stop_id is not None and msg.get("id") == stop_id:
                proc._ndjson_buffer = buf
                return results
            if max_count is not None and len(results) >= max_count:
                proc._ndjson_buffer = buf
                return results

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wait = min(idle, remaining) if idle is not None else remaining
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready:
            break
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        buf += chunk

    proc._ndjson_buffer = buf
    return results


def _read_all_responses(proc, *, timeout: float = 5.0, idle: float = 0.5, max_count: int = 10) -> list[dict]:
    """
    Read as many responses as arrive within timeout, up to max_count.
    Useful when the proxy may send notifications before the real response.

    ``idle`` caps how long each individual select waits — once no data
    arrives for ``idle`` seconds the function returns, even if the overall
    ``timeout`` hasn't elapsed.  This prevents tests from blocking for
    the full timeout when only a few quick messages are expected.
    """
    return _read_json_messages(
        proc, timeout=timeout, idle=idle, max_count=max_count
    )


def _read_until_id(proc, target_id: int | str, *, timeout: float = 15.0) -> list[dict]:
    """Read messages until one with ``id == target_id`` arrives, or ``timeout`` elapses.

    Unlike ``_read_all_responses``, this does not return early on idle gaps —
    it keeps reading until the target response is seen. Useful when a
    slow-to-start child server may introduce a gap between an early
    notification and the real response.
    """
    return _read_json_messages(proc, timeout=timeout, stop_id=target_id)


def _call_until_result(proc, params, *, start_id: int, timeout: float = 10.0):
    """Send ``tools/call`` with an incrementing id until a result arrives.

    A request that lands during a child restart gets a transient
    "server restarting, please retry" error; retrying until a result (or the
    deadline) removes the race on how long the subprocess restart takes under
    load. Returns ``(collected_messages, result_response_or_None)``.
    """
    collected: list[dict] = []
    rid = start_id
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc.stdin.write(_make_jsonrpc("ping", id=rid, params=params))
        proc.stdin.flush()
        remaining = max(0.0, deadline - time.monotonic())
        msgs = _read_until_id(proc, rid, timeout=max(0.05, min(5.0, remaining)))
        collected += msgs
        resp = _find_by_id(msgs, rid)
        if resp is not None and "result" in resp:
            return collected, resp
        if resp is not None and "error" in resp:
            message = resp["error"].get("message", "")
            if "server restarting, please retry" not in message:
                raise AssertionError(
                    f"Unexpected error while waiting for restart result: {resp}; "
                    f"collected: {collected}"
                )
        rid += 1
    return collected, None


def _call_until_error(proc, params, predicate, *, start_id: int, timeout: float = 10.0):
    """Send ``tools/call`` with incrementing ids until an expected error appears."""
    collected: list[dict] = []
    rid = start_id
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc.stdin.write(_make_jsonrpc("ping", id=rid, params=params))
        proc.stdin.flush()
        remaining = max(0.0, deadline - time.monotonic())
        msgs = _read_until_id(proc, rid, timeout=max(0.05, min(5.0, remaining)))
        collected += msgs
        resp = _find_by_id(msgs, rid)
        if resp is not None and "error" in resp and predicate(resp):
            return collected, resp
        rid += 1
    return collected, None


def _find_by_id(messages: list[dict], id: int | str) -> dict | None:
    """Return the first message whose 'id' matches."""
    for m in messages:
        if m.get("id") == id:
            return m
    return None


def _find_notification(messages: list[dict], method: str) -> dict | None:
    """Return the first notification matching method."""
    for m in messages:
        if m.get("method") == method and m.get("id") is None:
            return m
    return None


def _wait_for(predicate, *, timeout: float = 2.0, interval: float = 0.01) -> bool:
    """Poll until predicate() is truthy or timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _exhaust_backoff(proc, *, crash_count: int = 6, probe_id: int = 99) -> list[dict]:
    """Drive crashes until explicit exhaustion, then confirm with a separate request.

    Only the documented transient restart errors permit another probe. An idle
    pipe is not evidence that the replacement child is ready.
    """
    all_msgs: list[dict] = []
    deadline = time.monotonic() + 30
    req_id = 1000
    observed_crashes = 0
    while time.monotonic() < deadline:
        proc.stdin.write(_make_jsonrpc("ping", id=req_id,
                                       params={"name": "anything"}))
        proc.stdin.flush()
        messages = _read_until_id(proc, req_id, timeout=min(5, max(0, deadline - time.monotonic())))
        all_msgs.extend(messages)
        response = _find_by_id(messages, req_id)
        assert response is not None and "error" in response, all_msgs
        error = response["error"]["message"]
        if _GIVE_UP_MSG_FRAGMENT in error.lower():
            assert observed_crashes == crash_count, all_msgs
            break
        assert error in {"server exited mid-request, restarting", "server restarting, please retry"}, response
        if error == "server exited mid-request, restarting":
            observed_crashes += 1
            assert observed_crashes <= crash_count, all_msgs
        else:
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        req_id += 1
    else:
        raise AssertionError(f"Backoff never exhausted: {all_msgs}")

    proc.stdin.write(_make_jsonrpc("ping", id=probe_id,
                                   params={"name": "anything"}))
    proc.stdin.flush()
    all_msgs.extend(_read_until_id(proc, probe_id, timeout=5.0))
    return all_msgs


class _FakeBuffer:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeStdin:
    def __init__(self, lines: list[bytes]):
        self.buffer = _FakeBuffer(lines)


class _BlockingBuffer:
    def __init__(self, lines: list[bytes], eof_event: threading.Event):
        self._lines = list(lines)
        self._eof_event = eof_event

    def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        self._eof_event.wait()
        return b""


class _BlockingFakeStdin:
    def __init__(self, lines: list[bytes], eof_event: threading.Event):
        self.buffer = _BlockingBuffer(lines, eof_event)


class _NoOpThread:
    def __init__(self, target=None, daemon=None, name=None):
        self._target = target
        self._started = False

    def start(self, **_options) -> None:
        self._started = True

    def is_alive(self) -> bool:
        return self._started

    def join(self, timeout=None) -> None:
        return None


class _FakeChild:
    def __init__(
        self,
        *,
        poll_values: list[int | None] | None = None,
        send_exception: type[BaseException] | None = None,
    ):
        self._poll_values = list(poll_values or [None])
        self._poll_index = 0
        self._send_exception = send_exception
        self.sent: list[dict] = []
        self.started = False
        self.killed = False
        self.pid = 12345
        self.stdout_fd = None
        self.subscription_id = None

    def start(self, **_options) -> None:
        self.started = True

    def send(self, obj: dict) -> None:
        if self._send_exception is not None:
            raise self._send_exception()
        self.sent.append(obj)

    def send_control(self, obj: dict, **kwargs) -> None:
        self.send(obj)

    def poll(self) -> int | None:
        if self._poll_index < len(self._poll_values):
            value = self._poll_values[self._poll_index]
            self._poll_index += 1
            return value
        return self._poll_values[-1]

    def wait(self) -> int | None:
        return self.poll()

    def kill(self) -> None:
        self.killed = True


class _ReadableFakeChild(_FakeChild):
    def __init__(self, lines: list[bytes | None]):
        super().__init__(poll_values=[None, 0])
        self._lines = list(lines)
        self.stdout_fd = 123

    def readline(self) -> bytes | None:
        if self._lines:
            return self._lines.pop(0)
        return None


def _isolate_proxy_logging(monkeypatch):
    # Do not initialise the registered production logger: its handler retains
    # pytest's captured stderr after the originating test finishes. Propagate
    # an unregistered, test-owned logger to the root so caplog still observes it.
    logger = logging.Logger("brain-proxy", logging.DEBUG)
    logger.parent = logging.getLogger()
    monkeypatch.setattr(proxy_mod, "_logger", logger)


def _fake_proxy_threads(monkeypatch):
    # Replace only the proxy's dependency, not the shared stdlib module used by
    # pytest and other components. Synchronisation primitives remain real.
    monkeypatch.setattr(proxy_mod, "threading", SimpleNamespace(
        Thread=_NoOpThread, Event=threading.Event, Lock=threading.Lock,
    ))


def _make_inprocess_proxy(tmp_path, monkeypatch, stdin_lines: list[bytes]) -> tuple[proxy_mod.Proxy, list[dict]]:
    _write_vault(tmp_path)
    _isolate_proxy_logging(monkeypatch)
    proxy = proxy_mod.Proxy(PYTHON, "fake-server", str(tmp_path))
    proxy._client_protocol = "legacy"
    proxy._initial_protocol_selected.set()
    sent_to_client: list[dict] = []

    _fake_proxy_threads(monkeypatch)
    monkeypatch.setattr(proxy_mod.sys, "stdin", _FakeStdin(stdin_lines))
    monkeypatch.setattr(proxy, "_send_to_client", lambda obj: sent_to_client.append(obj))

    return proxy, sent_to_client


def _make_inprocess_proxy_with_real_threads(
    tmp_path, monkeypatch, stdin=None,
) -> tuple[proxy_mod.Proxy, list[dict]]:
    """In-process Proxy with real background threads. For tests that need
    the recovery thread to actually run (e.g. concurrent-signal coverage)."""
    _write_vault(tmp_path)
    _isolate_proxy_logging(monkeypatch)
    proxy = proxy_mod.Proxy(PYTHON, "fake-server", str(tmp_path))
    proxy._client_protocol = "legacy"
    proxy._initial_protocol_selected.set()
    sent_to_client: list[dict] = []

    if stdin is not None:
        monkeypatch.setattr(proxy_mod.sys, "stdin", stdin)
    monkeypatch.setattr(proxy, "_send_to_client", lambda obj: sent_to_client.append(obj))

    return proxy, sent_to_client


def _run_proxy_wrapper(tmp_path, server_script, patch_body: str, *, backoff: str = "0,0,0,0,0"):
    """Spawn the proxy as a subprocess via a wrapper script that monkeypatches
    proxy_mod before calling main(). patch_body is appended verbatim between
    the import boilerplate and the main() call. Caller owns terminate/wait."""
    import subprocess

    header = textwrap.dedent(f"""\
        import sys
        import os

        sys.path.insert(0, {PROXY_SCRIPT!r})
        from brain_mcp import proxy as proxy_mod
        """)
    wrapper_path = str(tmp_path / "proxy_wrapper.py")
    with open(wrapper_path, "w") as f:
        f.write(header + patch_body + "\nproxy_mod.main()\n")

    env = os.environ.copy()
    env["BRAIN_VAULT_ROOT"] = str(tmp_path)
    env["BRAIN_PROXY_BACKOFF"] = backoff

    return subprocess.Popen(
        [PYTHON, wrapper_path, PYTHON, server_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        # cwd isolation: prevents the rung-2 walk from wandering out of
        # tmp_path and finding markers in the real working tree.
        cwd=str(tmp_path),
    )


def _with_interface_discovery(script: str) -> str:
    """Give lifecycle mock servers the required Brain child handshake.

    Their requests remain protocol pings; granular invocation and outcome
    behavior is verified separately against canonical tool headers.
    """
    from brain_mcp._interface_protocol import command_interface_wire

    wire = command_interface_wire(application_interface_header(current_application_catalogue()))
    result = {"capabilities": {"experimental": {"brainCommandInterface": wire}}}
    branch = (
        '    if method == "server/discover":\n'
        f'        print(json.dumps({{"jsonrpc": "2.0", "id": msg_id, "result": {result!r}}}), flush=True)\n'
        '        continue\n'
    )
    script = script.replace('    if method == "initialize":', branch + '    if method == "initialize":')
    script = script.replace('    msg_id = obj.get("id")', '    msg_id = obj.get("id")\n    if msg_id is None:\n        continue')
    return script.replace('"capabilities": {}', f'"capabilities": {result["capabilities"]!r}')


def _echo_server_script(tmp_path) -> str:
    """
    An echo server: responds to initialize with a fixed result,
    and echoes every subsequent request back as a success response
    with result.content[{type:text, text: 'echo:<method>'}].
    """
    path = str(tmp_path / "echo_server.py")
    script = textwrap.dedent("""\
        import sys, json

        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            method = obj.get("method", "")
            msg_id = obj.get("id")
            if method == "initialize":
                resp = {"jsonrpc": "2.0", "id": msg_id,
                        "result": {"protocolVersion": "2024-11-05",
                                   "capabilities": {},
                                   "serverInfo": {"name": "echo", "version": "0.0.1"}}}
            else:
                resp = {"jsonrpc": "2.0", "id": msg_id,
                        "result": {"content": [{"type": "text", "text": f"echo:{method}"}]}}
            print(json.dumps(resp), flush=True)
    """)
    with open(path, "w") as f:
        f.write(_with_interface_discovery(script))
    return path


def _drift_then_echo_server_script(tmp_path) -> str:
    """
    Server that exits with code 10 on the first non-initialize request,
    then on the second invocation (detected via a marker file) echoes normally.
    """
    marker = str(tmp_path / "server_restarted.marker")
    path = str(tmp_path / "drift_server.py")
    script = textwrap.dedent(f"""\
        import sys, json, os

        marker_path = {marker!r}
        first_run = not os.path.exists(marker_path)
        if first_run:
            open(marker_path, "w").close()

        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            method = obj.get("method", "")
            msg_id = obj.get("id")
            if method == "initialize":
                resp = {{"jsonrpc": "2.0", "id": msg_id,
                         "result": {{"protocolVersion": "2024-11-05",
                                    "capabilities": {{}},
                                    "serverInfo": {{"name": "drift", "version": "0.0.1"}}}}}}
                print(json.dumps(resp), flush=True)
            else:
                if first_run:
                    # Simulate version drift — exit with code 10
                    sys.exit(10)
                else:
                    resp = {{"jsonrpc": "2.0", "id": msg_id,
                             "result": {{"content": [{{"type": "text", "text": "ok after restart"}}]}}}}
                    print(json.dumps(resp), flush=True)
    """)
    with open(path, "w") as f:
        f.write(_with_interface_discovery(script))
    return path
