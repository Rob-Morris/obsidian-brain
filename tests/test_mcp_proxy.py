"""
Tests for Brain MCP Proxy — subprocess-level integration tests.

The proxy is launched as a real process (python proxy.py <python> <server>)
with mock child servers written as inline Python scripts to tmp_path.

Each test class owns a specific behaviour: forwarding, restart-on-drift,
crash backoff, version-reset after give-up, proxy drift injection, and
init timeout.
"""

import json
import os
import sys
import textwrap
import time

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROXY_SCRIPT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "mcp", "proxy.py")
)
PYTHON = sys.executable


def _make_jsonrpc(method: str, id: int | str | None = None, params: dict | None = None) -> str:
    """Build a JSON-RPC request as an NDJSON line (no trailing newline)."""
    obj: dict = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        obj["id"] = id
    if params is not None:
        obj["params"] = params
    return json.dumps(obj) + "\n"


def _make_response(id: int | str | None, result: dict) -> str:
    """Build a JSON-RPC success response as an NDJSON line (no trailing newline)."""
    return json.dumps({"jsonrpc": "2.0", "id": id, "result": result}) + "\n"


def _write_vault(tmp_path, version: str = "1.0.0") -> None:
    """Create the minimal vault structure the proxy needs."""
    bc = tmp_path / ".brain-core"
    bc.mkdir(exist_ok=True)
    (bc / "VERSION").write_text(version)
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
    if extra_env:
        env.update(extra_env)

    return subprocess.Popen(
        [PYTHON, PROXY_SCRIPT, PYTHON, server_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _read_responses(proc, *, timeout: float = 5.0, count: int = 1) -> list[dict]:
    """
    Read up to `count` JSON objects from proc.stdout within `timeout` seconds.
    Uses select on POSIX so we don't block forever.
    """
    import select

    results = []
    deadline = time.monotonic() + timeout
    buf = ""

    while len(results) < count and time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select([proc.stdout], [], [], remaining)
        if not ready:
            break
        chunk = proc.stdout.readline()
        if not chunk:
            break
        buf += chunk
        # Each line should be a complete JSON object
        line = chunk.strip()
        if line:
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # skip malformed lines

    return results


def _read_all_responses(proc, *, timeout: float = 5.0, max_count: int = 10) -> list[dict]:
    """
    Read as many responses as arrive within timeout, up to max_count.
    Useful when the proxy may send notifications before the real response.
    """
    import select

    results = []
    deadline = time.monotonic() + timeout

    while len(results) < max_count and time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select([proc.stdout], [], [], remaining)
        if not ready:
            break
        chunk = proc.stdout.readline()
        if not chunk:
            break
        line = chunk.strip()
        if line:
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    return results


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


# ---------------------------------------------------------------------------
# Server script factories
# ---------------------------------------------------------------------------

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
        f.write(script)
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
        f.write(script)
    return path


def _crash_server_script(tmp_path) -> str:
    """
    Server that responds to initialize then immediately exits (crash) on any
    other message.
    """
    path = str(tmp_path / "crash_server.py")
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
                                   "serverInfo": {"name": "crash", "version": "0.0.1"}}}
                print(json.dumps(resp), flush=True)
            else:
                # Crash with non-zero, non-10 exit code
                sys.exit(1)
    """)
    with open(path, "w") as f:
        f.write(script)
    return path


def _crash_then_echo_server_script(tmp_path, *, crash_runs: int = 1) -> str:
    """
    Server that crashes on real requests for the first `crash_runs` invocations,
    then echoes normally.  Uses a counter file to track runs.
    """
    counter_file = str(tmp_path / "crash_run_count.txt")
    path = str(tmp_path / "crash_then_echo_server.py")
    script = textwrap.dedent(f"""\
        import sys, json, os

        counter_path = {counter_file!r}
        crash_runs = {crash_runs}

        # Read and increment run counter
        try:
            with open(counter_path) as f:
                run_no = int(f.read().strip())
        except Exception:
            run_no = 0
        with open(counter_path, "w") as f:
            f.write(str(run_no + 1))

        should_crash = run_no < crash_runs

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
                                    "serverInfo": {{"name": "cv", "version": "0.0.1"}}}}}}
                print(json.dumps(resp), flush=True)
            else:
                if should_crash:
                    sys.exit(1)
                else:
                    resp = {{"jsonrpc": "2.0", "id": msg_id,
                             "result": {{"content": [{{"type": "text", "text": "recovered"}}]}}}}
                    print(json.dumps(resp), flush=True)
    """)
    with open(path, "w") as f:
        f.write(script)
    return path


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
        f.write(script)
    return path


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestMessageForwarding:
    """Proxy forwards JSON-RPC requests to echo server and returns responses."""

    def test_forward_request_and_response(self, tmp_path):
        """Proxy forwards a request to an echo server and relays the response."""
        _write_vault(tmp_path)
        server_script = _echo_server_script(tmp_path)
        proc = _launch_proxy(tmp_path, server_script)
        try:
            # 1. Send initialize (required first)
            proc.stdin.write(_make_jsonrpc("initialize", id=1,
                                           params={"protocolVersion": "2024-11-05",
                                                   "clientInfo": {"name": "test", "version": "0"}}))
            proc.stdin.flush()

            # 2. Read initialize response
            init_msgs = _read_responses(proc, timeout=5.0, count=1)
            assert len(init_msgs) == 1, f"Expected init response, got: {init_msgs}"
            assert init_msgs[0].get("id") == 1
            assert "result" in init_msgs[0]

            # 3. Send a real request
            proc.stdin.write(_make_jsonrpc("tools/call", id=2, params={"name": "ping"}))
            proc.stdin.flush()

            # 4. Read response
            msgs = _read_responses(proc, timeout=5.0, count=1)
            assert len(msgs) >= 1, "No response received for tools/call"
            resp = _find_by_id(msgs, 2)
            if resp is None:
                # May need to drain more
                more = _read_all_responses(proc, timeout=3.0)
                resp = _find_by_id(more, 2)
            assert resp is not None, f"No response with id=2 found. Got: {msgs}"
            assert "result" in resp, f"Expected result in response, got: {resp}"
            assert resp["result"]["content"][0]["text"] == "echo:tools/call"

        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestVersionDriftRestart:
    """Proxy restarts the child on exit code 10 (version drift)."""

    def test_restart_on_drift_exit(self, tmp_path):
        """Child exits with code 10 on first real request; proxy relaunches and second request succeeds."""
        _write_vault(tmp_path)
        server_script = _drift_then_echo_server_script(tmp_path)
        proc = _launch_proxy(tmp_path, server_script)
        try:
            # Initialize
            proc.stdin.write(_make_jsonrpc("initialize", id=1,
                                           params={"protocolVersion": "2024-11-05",
                                                   "clientInfo": {"name": "test", "version": "0"}}))
            proc.stdin.flush()
            init_msgs = _read_responses(proc, timeout=5.0, count=1)
            assert init_msgs and init_msgs[0].get("id") == 1

            # First real request — will cause child to exit(10)
            proc.stdin.write(_make_jsonrpc("tools/call", id=2, params={"name": "anything"}))
            proc.stdin.flush()

            # Give the proxy time to detect exit, restart, and send list_changed
            time.sleep(0.5)

            # Second request — should succeed through restarted child
            proc.stdin.write(_make_jsonrpc("tools/call", id=3, params={"name": "anything"}))
            proc.stdin.flush()

            # Read all messages; we expect a list_changed notification + response for id=3
            all_msgs = _read_all_responses(proc, timeout=10.0, max_count=10)

            # Check for tools/list_changed notification
            notif = _find_notification(all_msgs, "notifications/tools/list_changed")
            assert notif is not None, (
                f"Expected notifications/tools/list_changed after restart. Got: {all_msgs}"
            )

            # Check that id=3 gets a success response
            resp = _find_by_id(all_msgs, 3)
            assert resp is not None, f"No response with id=3. Got: {all_msgs}"
            assert "result" in resp, f"Expected result, got error: {resp}"
            assert resp["result"]["content"][0]["text"] == "ok after restart"

        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestCrashBackoff:
    """Proxy gives up after exhausting the backoff schedule."""

    def test_gives_up_after_max_retries(self, tmp_path):
        """Child always crashes on real requests; proxy returns '5 attempts' error."""
        _write_vault(tmp_path)
        server_script = _crash_server_script(tmp_path)
        # Use BRAIN_PROXY_BACKOFF=0,0,0,0,0 to skip all delays (5 slots = 5 backoff entries)
        proc = _launch_proxy(tmp_path, server_script,
                             extra_env={"BRAIN_PROXY_BACKOFF": "0,0,0,0,0"})
        try:
            # Initialize
            proc.stdin.write(_make_jsonrpc("initialize", id=1,
                                           params={"protocolVersion": "2024-11-05",
                                                   "clientInfo": {"name": "test", "version": "0"}}))
            proc.stdin.flush()
            init_msgs = _read_responses(proc, timeout=5.0, count=1)
            assert init_msgs and init_msgs[0].get("id") == 1

            # Backoff mechanics:
            # - Schedule [0,0,0,0,0] has 5 entries (indices 0-4).
            # - Each crash: if slot < 5 → restart (slot++) ; if slot >= 5 → give up.
            # - So crashes 1-5 each trigger a restart (slots 0-4 consumed).
            # - Crash 6: slot=5 >= 5 → gave_up=True, no restart.
            # - 7th request: child=None + gave_up → error response.
            #
            # Strategy: send 6 requests to trigger 6 crashes (exhausting all 5 backoff
            # slots and reaching give-up), then send 1 final request to get the error.
            all_msgs = []

            # Send 6 requests to exhaust backoff (triggers 6 crashes total)
            for req_id in range(2, 8):  # ids 2-7
                proc.stdin.write(_make_jsonrpc("tools/call", id=req_id,
                                               params={"name": "anything"}))
                proc.stdin.flush()
                # Give the crash+restart cycle time to complete
                batch = _read_all_responses(proc, timeout=5.0, max_count=5)
                all_msgs.extend(batch)

            # After 6 crashes, proxy has given up. Send the give-up probe request.
            proc.stdin.write(_make_jsonrpc("tools/call", id=99, params={"name": "anything"}))
            proc.stdin.flush()
            batch = _read_all_responses(proc, timeout=5.0, max_count=5)
            all_msgs.extend(batch)

            # Find the error response for id=99
            error_resp = _find_by_id(all_msgs, 99)
            assert error_resp is not None, (
                f"Expected error response with id=99 after backoff exhaustion. Got: {all_msgs}"
            )
            assert "error" in error_resp, f"Expected error, got: {error_resp}"

            error_msg = error_resp["error"]["message"]
            assert "5 attempts" in error_msg or "attempt" in error_msg.lower(), (
                f"Expected '5 attempts' in error message. Got: {error_msg!r}"
            )
            assert "Restart MCP via /mcp" in error_msg or "/mcp" in error_msg, (
                f"Expected restart instruction in error message. Got: {error_msg!r}"
            )

        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestVersionResetAfterGiveUp:
    """After give-up, changing VERSION on disk resets backoff on next tools/call."""

    def test_version_change_resets_backoff(self, tmp_path):
        """After give-up, update VERSION file; next tools/call triggers restart and succeeds."""
        _write_vault(tmp_path, version="1.0.0")
        # Need 6 crash-causing runs (run_no 0-5) to exhaust all 5 backoff slots:
        # - Crashes 1-5 each consume a slot (0-4) and trigger a restart.
        # - Crash 6: slot=5 >= 5 → gave_up=True, no restart.
        # Runs 6+ will echo (succeed), which is what we want post-version-reset.
        server_script = _crash_then_echo_server_script(tmp_path, crash_runs=6)
        proc = _launch_proxy(tmp_path, server_script,
                             extra_env={"BRAIN_PROXY_BACKOFF": "0,0,0,0,0"})
        try:
            # Initialize
            proc.stdin.write(_make_jsonrpc("initialize", id=1,
                                           params={"protocolVersion": "2024-11-05",
                                                   "clientInfo": {"name": "test", "version": "0"}}))
            proc.stdin.flush()
            init_msgs = _read_responses(proc, timeout=5.0, count=1)
            assert init_msgs and init_msgs[0].get("id") == 1

            # Send 6 requests to trigger 6 crashes (exhausting the backoff schedule)
            all_msgs = []
            for req_id in range(2, 8):  # ids 2-7
                proc.stdin.write(_make_jsonrpc("tools/call", id=req_id,
                                               params={"name": "anything"}))
                proc.stdin.flush()
                batch = _read_all_responses(proc, timeout=5.0, max_count=5)
                all_msgs.extend(batch)

            # Send a probe to confirm give-up
            proc.stdin.write(_make_jsonrpc("tools/call", id=50, params={"name": "anything"}))
            proc.stdin.flush()
            probe_msgs = _read_all_responses(proc, timeout=5.0, max_count=5)
            all_msgs.extend(probe_msgs)

            give_up_resp = _find_by_id(all_msgs, 50)
            assert give_up_resp is not None and "error" in give_up_resp, (
                f"Expected give-up error for id=50. Got: {all_msgs}"
            )

            # Now update VERSION on disk to simulate an upgrade
            version_path = tmp_path / ".brain-core" / "VERSION"
            version_path.write_text("2.0.0")

            # Give proxy a moment, then send a tools/call — this triggers _try_version_reset.
            # The proxy resets backoff, starts child run_no=6+ (which echoes), sends list_changed,
            # and then the request is forwarded and answered.
            time.sleep(0.2)
            proc.stdin.write(_make_jsonrpc("tools/call", id=100, params={"name": "anything"}))
            proc.stdin.flush()

            all_post = _read_all_responses(proc, timeout=15.0, max_count=20)

            resp = _find_by_id(all_post, 100)
            assert resp is not None, (
                f"No response with id=100 after version reset. Got: {all_post}"
            )
            assert "result" in resp, (
                f"Expected success after version reset restart, got error: {resp}"
            )
            assert resp["result"]["content"][0]["text"] == "recovered"

        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestProxyDrift:
    """Proxy injects upgrade note when its on-disk version differs from running version."""

    def test_drift_note_injected(self, tmp_path):
        """On-disk proxy_version file shows '99.0.0'; after restart, responses contain upgrade note."""
        import shutil

        _write_vault(tmp_path)

        # Create an "on-disk" version file that shows a newer PROXY_VERSION.
        # We'll write a Python file that looks like proxy.py for the regex.
        on_disk_path = str(tmp_path / "proxy_on_disk.py")
        with open(on_disk_path, "w") as f:
            f.write('PROXY_VERSION = "99.0.0"\n')

        # Create a wrapper that:
        # 1. Imports the real proxy module
        # 2. Creates a Proxy instance
        # 3. Overrides proxy_script to point at the on-disk file above
        # 4. Starts the child and runs
        wrapper_path = str(tmp_path / "proxy_wrapper.py")
        real_proxy_dir = os.path.dirname(PROXY_SCRIPT)
        wrapper_script = textwrap.dedent(f"""\
            import sys
            import os

            # Ensure proxy module is importable
            sys.path.insert(0, {real_proxy_dir!r})
            import proxy as proxy_mod

            def main():
                if len(sys.argv) != 3:
                    print("Usage: wrapper.py <python> <server>", file=sys.stderr)
                    sys.exit(1)

                python_path = sys.argv[1]
                server_script = sys.argv[2]
                vault_root = os.environ.get("BRAIN_VAULT_ROOT", os.getcwd())

                proxy_mod._logger = proxy_mod._setup_logging(vault_root)

                p = proxy_mod.Proxy(python_path, server_script, vault_root)
                # Override proxy_script to point at our on-disk file with 99.0.0
                p.proxy_script = {on_disk_path!r}

                success = p._start_child()
                if not success:
                    p._backoff_slot = 1

                p.run()

            if __name__ == "__main__":
                main()
        """)
        with open(wrapper_path, "w") as f:
            f.write(wrapper_script)

        # Use a server that exits with code 10 on first non-init real request,
        # then echoes — this triggers a restart which calls _check_proxy_drift.
        server_script = _drift_then_echo_server_script(tmp_path)

        env = os.environ.copy()
        env["BRAIN_VAULT_ROOT"] = str(tmp_path)

        import subprocess
        proc = subprocess.Popen(
            [PYTHON, wrapper_path, PYTHON, server_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            # Initialize
            proc.stdin.write(_make_jsonrpc("initialize", id=1,
                                           params={"protocolVersion": "2024-11-05",
                                                   "clientInfo": {"name": "test", "version": "0"}}))
            proc.stdin.flush()
            init_msgs = _read_responses(proc, timeout=5.0, count=1)
            assert init_msgs and init_msgs[0].get("id") == 1

            # First request — child exits(10), proxy restarts, _check_proxy_drift detects 99.0.0
            proc.stdin.write(_make_jsonrpc("tools/call", id=2, params={"name": "anything"}))
            proc.stdin.flush()

            # Wait for restart + list_changed to be sent
            time.sleep(0.5)

            # Second request — restarted child answers; proxy injects drift note
            proc.stdin.write(_make_jsonrpc("tools/call", id=3, params={"name": "anything"}))
            proc.stdin.flush()

            all_msgs = _read_all_responses(proc, timeout=10.0, max_count=15)

            # Find response for id=3
            resp = _find_by_id(all_msgs, 3)
            if resp is None:
                more = _read_all_responses(proc, timeout=5.0, max_count=10)
                all_msgs.extend(more)
                resp = _find_by_id(all_msgs, 3)

            assert resp is not None, f"No response with id=3. Got: {all_msgs}"
            assert "result" in resp, f"Expected result, got: {resp}"

            content_items = resp["result"].get("content", [])
            text_content = " ".join(
                item.get("text", "") for item in content_items if item.get("type") == "text"
            )
            assert "proxy has been upgraded" in text_content, (
                f"Expected 'proxy has been upgraded' drift note in response text. Got: {text_content!r}"
            )
            assert "99.0.0" in text_content, (
                f"Expected new version '99.0.0' in drift note. Got: {text_content!r}"
            )

        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestVersionDriftReplay:
    """The request that triggers version drift is transparently replayed to the new child."""

    def test_drift_triggering_request_gets_success(self, tmp_path):
        """
        The request that causes exit(10) should be replayed to the restarted child,
        so the client gets a success response — not an error.
        """
        _write_vault(tmp_path)
        server_script = _drift_then_echo_server_script(tmp_path)
        proc = _launch_proxy(tmp_path, server_script)
        try:
            # Initialize
            proc.stdin.write(_make_jsonrpc("initialize", id=1,
                                           params={"protocolVersion": "2024-11-05",
                                                   "clientInfo": {"name": "test", "version": "0"}}))
            proc.stdin.flush()
            init_msgs = _read_responses(proc, timeout=5.0, count=1)
            assert init_msgs and init_msgs[0].get("id") == 1

            # Send request that will trigger version drift (exit code 10)
            proc.stdin.write(_make_jsonrpc("tools/call", id=2, params={"name": "anything"}))
            proc.stdin.flush()

            # Read all messages — should include list_changed + success for id=2
            all_msgs = _read_all_responses(proc, timeout=10.0, max_count=10)

            # The triggering request (id=2) should get a SUCCESS response via replay
            resp = _find_by_id(all_msgs, 2)
            assert resp is not None, (
                f"No response with id=2 after drift replay. Got: {all_msgs}"
            )
            assert "result" in resp, (
                f"Expected success for replayed request id=2, got error: {resp}"
            )
            assert resp["result"]["content"][0]["text"] == "ok after restart"

            # Should also get the list_changed notification
            notif = _find_notification(all_msgs, "notifications/tools/list_changed")
            assert notif is not None, (
                f"Expected notifications/tools/list_changed. Got: {all_msgs}"
            )

        finally:
            proc.terminate()
            proc.wait(timeout=5)


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
        f.write(script)
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
            proc.stdin.write(_make_jsonrpc("tools/call", id=2, params={"name": "anything"}))
            proc.stdin.flush()

            # Wait for 3 consecutive timeouts (1s each) + kill + drain + restart attempts.
            # The drain sends the error for id=2, then _handle_child_exit tries to
            # restart. Use a generous timeout to account for restart attempts.
            all_msgs = _read_all_responses(proc, timeout=20.0, max_count=10)

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

        # Use a 2-second init timeout and zero backoff so all retries happen fast.
        proc = _launch_proxy(tmp_path, server_script,
                             extra_env={"BRAIN_PROXY_INIT_TIMEOUT": "2",
                                        "BRAIN_PROXY_BACKOFF": "0,0,0,0,0"})
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
            #    The init timeout (2s) fires; _start_child returns False.
            #    The proxy calls _advance_backoff and retries up to 5 times.
            #    Each retry = 2s timeout → ~10s total for 5 retries.
            proc.stdin.write(_make_jsonrpc("tools/call", id=2, params={"name": "ping"}))
            proc.stdin.flush()

            # 3. After the backoff is exhausted (5 × 2s ≈ 10s), send another request
            #    to get the give-up error response.
            # Wait a bit past the full backoff cycle before sending the next request.
            time.sleep(12)
            proc.stdin.write(_make_jsonrpc("tools/call", id=3, params={"name": "ping"}))
            proc.stdin.flush()

            all_msgs = _read_all_responses(proc, timeout=10.0, max_count=20)

            # Find an error response for id=3 (the post-give-up request)
            error_resp = _find_by_id(all_msgs, 3)
            assert error_resp is not None, (
                f"Expected an error response with id=3 after init timeout/give-up. Got: {all_msgs}"
            )
            assert "error" in error_resp, (
                f"Expected error in response for id=3, got: {error_resp}"
            )
            error_msg = error_resp["error"]["message"]
            assert (
                "attempt" in error_msg.lower()
                or "restart" in error_msg.lower()
                or "start" in error_msg.lower()
            ), f"Expected restart/attempt related error. Got: {error_msg!r}"

        finally:
            proc.terminate()
            proc.wait(timeout=5)
