"""Real-process coverage of hung requests and restart initialisation timeouts."""

import textwrap
import time
import pytest

from proxy_test_support import (
    _GIVE_UP_MSG_FRAGMENT,
    _find_by_id,
    _launch_proxy,
    _make_jsonrpc,
    _read_all_responses,
    _read_responses,
    _read_until_id,
    _with_interface_discovery,
    _write_vault,
)

pytestmark = pytest.mark.slow


def _hang_on_init_server_script(tmp_path) -> str:
    """
    Server that:
    - On first invocation: responds to initialize normally, then crashes on any
      subsequent message (triggering a restart).
    - On second+ invocations: hangs forever without responding to initialize
      (so the proxy's init-replay timeout fires).
    """
    marker = str(tmp_path / "hang_first_run.marker")
    path = str(tmp_path / "hang_server.py")
    script = textwrap.dedent(f"""\
        import sys, json, time, os

        marker_path = {marker!r}
        first_run = not os.path.exists(marker_path)
        if first_run:
            open(marker_path, "w").close()

        if not first_run:
            # Second+ invocation: hang on stdin without ever responding to initialize
            for raw in sys.stdin:
                time.sleep(3600)
            sys.exit(0)

        # First invocation: respond to initialize, crash on anything else
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
                                    "serverInfo": {{"name": "hang", "version": "0.0.1"}}}}}}
                print(json.dumps(resp), flush=True)
            else:
                # Crash with non-zero code to trigger restart
                sys.exit(1)
    """)
    with open(path, "w") as f:
        f.write(_with_interface_discovery(script))
    return path


def _hang_after_request_server_script(tmp_path) -> str:
    """
    Server that responds to initialize normally, then hangs forever (without
    responding or exiting) when it receives any other request.
    """
    path = str(tmp_path / "hang_after_request_server.py")
    script = textwrap.dedent("""\
        import sys, json, time

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
                                   "serverInfo": {"name": "hang", "version": "0.0.1"}}}
                print(json.dumps(resp), flush=True)
            else:
                # Hang forever — don't respond, don't exit
                while True:
                    time.sleep(3600)
    """)
    with open(path, "w") as f:
        f.write(_with_interface_discovery(script))
    return path


class TestHangDetection:
    """Proxy detects and kills a child that hangs with in-flight requests."""

    def test_hanging_child_killed_after_timeout(self, tmp_path):
        """
        Child hangs after receiving a request. Proxy's select timeout fires,
        detects in-flight requests, and kills the child after consecutive limit.
        Client gets an error response.
        """
        _write_vault(tmp_path)
        server_script = _hang_after_request_server_script(tmp_path)
        # Use very short select timeout (1s) and short init timeout to speed up
        proc = _launch_proxy(tmp_path, server_script,
                             extra_env={
                                 "BRAIN_PROXY_READ_TIMEOUT": "1",
                                 "BRAIN_PROXY_INIT_TIMEOUT": "2",
                                 "BRAIN_PROXY_BACKOFF": "0,0,0,0,0",
                             })
        try:
            # Initialize
            proc.stdin.write(_make_jsonrpc("initialize", id=1,
                                           params={"protocolVersion": "2024-11-05",
                                                   "clientInfo": {"name": "test", "version": "0"}}))
            proc.stdin.flush()
            init_msgs = _read_responses(proc, timeout=5.0, count=1)
            assert init_msgs and init_msgs[0].get("id") == 1

            # Send request — child will hang
            proc.stdin.write(_make_jsonrpc("ping", id=2, params={"name": "anything"}))
            proc.stdin.flush()

            # Wait for 3 consecutive timeouts (1s each) + kill + drain + async
            # restart attempts. Use idle > READ_TIMEOUT so we don't bail before
            # the proxy finishes the hang-detection cycle.
            all_msgs = _read_all_responses(proc, timeout=20.0, idle=10.0, max_count=10)

            # Should get an error response for id=2 (orphaned after kill)
            resp = _find_by_id(all_msgs, 2)
            assert resp is not None, (
                f"No response with id=2 after hang detection. Got: {all_msgs}"
            )
            assert "error" in resp, (
                f"Expected error for hung request id=2, got: {resp}"
            )

        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestStartupTimeout:
    """Proxy returns a timeout error when child hangs on initialize during restart."""

    def test_init_timeout(self, tmp_path):
        """
        Child responds to init normally on first run, crashes on real request (triggering
        restart), then hangs on initialize in subsequent runs.  Proxy exhausts backoff
        and returns an error.
        """
        _write_vault(tmp_path)
        server_script = _hang_on_init_server_script(tmp_path)

        # Use an immediate init timeout and a short multi-entry backoff schedule
        # so this still exercises schedule exhaustion without wall-clock hangs.
        proc = _launch_proxy(tmp_path, server_script,
                             extra_env={"BRAIN_PROXY_INIT_TIMEOUT": "0",
                                        "BRAIN_PROXY_BACKOFF": "0,0,0"})
        try:
            # 1. Initialize (first child run: responds normally)
            proc.stdin.write(_make_jsonrpc("initialize", id=1,
                                           params={"protocolVersion": "2024-11-05",
                                                   "clientInfo": {"name": "test", "version": "0"}}))
            proc.stdin.flush()
            init_msgs = _read_responses(proc, timeout=5.0, count=1)
            assert init_msgs and init_msgs[0].get("id") == 1

            # 2. Send a real request — first child crashes, triggering restart.
            #    On restart the proxy replays init to the new child (which now hangs).
            #    The immediate init timeout fires; the proxy retries until the
            #    short backoff schedule is exhausted, then moves into explicit give-up.
            proc.stdin.write(_make_jsonrpc("ping", id=2, params={"name": "ping"}))
            proc.stdin.flush()

            # 3. Poll with real requests until the proxy reports give-up. While
            #    recovery is still active, requests receive the soft restart
            #    error; after the schedule is exhausted they receive the hard
            #    unrecoverable guidance.
            all_msgs = []
            error_resp = None
            deadline = time.monotonic() + 5.0
            req_id = 3
            while time.monotonic() < deadline:
                proc.stdin.write(_make_jsonrpc("ping", id=req_id, params={"name": "ping"}))
                proc.stdin.flush()
                messages = _read_until_id(proc, req_id, timeout=5.0)
                all_msgs.extend(messages)
                resp = _find_by_id(messages, req_id)
                if resp and "MCP unrecoverable" in resp.get("error", {}).get("message", ""):
                    error_resp = resp
                    break
                req_id += 1
                time.sleep(0.01)

            assert error_resp is not None, (
                f"Expected an unrecoverable error after init timeout/give-up. Got: {all_msgs}"
            )
            assert "error" in error_resp, (
                f"Expected error in give-up probe response, got: {error_resp}"
            )
            error_msg = error_resp["error"]["message"]
            assert "MCP unrecoverable" in error_msg, (
                f"Expected unrecoverable prefix. Got: {error_msg!r}"
            )
            assert _GIVE_UP_MSG_FRAGMENT in error_msg.lower(), (
                f"Expected explicit give-up message. Got: {error_msg!r}"
            )
            assert "Restart MCP" in error_msg, (
                f"Expected explicit restart guidance. Got: {error_msg!r}"
            )

        finally:
            proc.terminate()
            proc.wait(timeout=5)
