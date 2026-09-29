"""Version drift restarts, backoff reset and running-proxy upgrade guidance."""

import os
import textwrap
import time
import pytest

from proxy_test_support import (
    PROXY_SCRIPT,
    PYTHON,
    _call_until_result,
    _drift_then_echo_server_script,
    _exhaust_backoff,
    _find_by_id,
    _find_notification,
    _launch_proxy,
    _make_jsonrpc,
    _read_all_responses,
    _read_responses,
    _read_until_id,
    _with_interface_discovery,
    _write_vault,
)

pytestmark = pytest.mark.slow


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
        f.write(_with_interface_discovery(script))
    return path


class TestVersionDriftRestart:
    """Proxy restarts the child on exit code 10 (version drift)."""

    def test_restart_on_drift_exit(self, tmp_path):
        """Child exits with code 10 on first real request; proxy relaunches and second request succeeds."""
        _write_vault(tmp_path)
        server_script = _drift_then_echo_server_script(tmp_path)
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

            # First real request — will cause child to exit(10)
            proc.stdin.write(_make_jsonrpc("ping", id=2, params={"name": "anything"}))
            proc.stdin.flush()

            # Retry the follow-up request until the restarted child serves a
            # result, instead of a fixed sleep that races the restart. A request
            # landing mid-restart gets a transient "server restarting" error
            # even after list_changed is emitted.
            all_msgs, resp = _call_until_result(proc, {"name": "anything"}, start_id=3)

            # Restart must announce the tool list changed.
            notif = _find_notification(all_msgs, "notifications/tools/list_changed")
            assert notif is not None, (
                f"Expected notifications/tools/list_changed after restart. Got: {all_msgs}"
            )

            # The restarted child answers successfully.
            assert resp is not None, f"No successful response after restart. Got: {all_msgs}"
            assert resp["result"]["content"][0]["text"] == "ok after restart"

        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestVersionResetAfterGiveUp:
    """After give-up, changing VERSION on disk resets backoff on next tools/call."""

    def test_version_change_resets_backoff(self, tmp_path):
        """After give-up, update VERSION file; next tools/call soft-fails, then recovery succeeds."""
        _write_vault(tmp_path, version="1.0.0")
        # Need 6 crash-causing runs (run_no 0-5) to exhaust all 5 backoff slots:
        # - Crashes 1-5 each consume a slot (0-4) and trigger a restart.
        # - Crash 6: slot=5 >= 5 → gave_up=True, no restart.
        # Runs 6+ will echo (succeed), which is what we want post-version-reset.
        server_script = _crash_then_echo_server_script(tmp_path, crash_runs=6)
        proc = _launch_proxy(tmp_path, server_script,
                             extra_env={"BRAIN_PROXY_BACKOFF": "0,0,0,0,0",
                                        "BRAIN_PROXY_VERSION_CHECK_INTERVAL": "0"})
        try:
            # Initialize
            proc.stdin.write(_make_jsonrpc("initialize", id=1,
                                           params={"protocolVersion": "2024-11-05",
                                                   "clientInfo": {"name": "test", "version": "0"}}))
            proc.stdin.flush()
            init_msgs = _read_responses(proc, timeout=5.0, count=1)
            assert init_msgs and init_msgs[0].get("id") == 1

            all_msgs = _exhaust_backoff(proc, probe_id=50)

            give_up_resp = _find_by_id(all_msgs, 50)
            assert give_up_resp is not None and "error" in give_up_resp, (
                f"Expected give-up error for id=50. Got: {all_msgs}"
            )

            # Now update VERSION on disk to simulate an upgrade
            version_path = tmp_path / ".brain-core" / "VERSION"
            version_path.write_text("2.0.0")

            # Send a tools/call which triggers the async version-reset signal
            # (rate limit disabled via VERSION_CHECK_INTERVAL=0). The triggering
            # request still gets the give-up error immediately.
            time.sleep(0.2)
            proc.stdin.write(_make_jsonrpc("tools/call", id=100, params={"name": "artefact_read"}))
            proc.stdin.flush()

            first_post = _read_until_id(proc, 100, timeout=5.0)
            first_resp = _find_by_id(first_post, 100)
            assert first_resp is not None and "error" in first_resp, (
                f"Expected immediate give-up error for id=100. Got: {first_post}"
            )
            assert "MCP unrecoverable" in first_resp["error"]["message"]

            post_reset_msgs = _read_all_responses(proc, timeout=5.0, idle=0.5, max_count=10)
            notif = _find_notification(post_reset_msgs, "notifications/tools/list_changed")
            assert notif is not None, (
                f"Expected list_changed after async version reset. Got: {post_reset_msgs}"
            )

            proc.stdin.write(_make_jsonrpc("ping", id=101, params={"name": "anything"}))
            proc.stdin.flush()

            # Keep reading until the post-reset success arrives.
            all_post = _read_until_id(proc, 101, timeout=15.0)

            resp = _find_by_id(all_post, 101)
            assert resp is not None, (
                f"No response with id=101 after version reset. Got: {all_post}"
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
        real_proxy_root = PROXY_SCRIPT
        wrapper_script = textwrap.dedent(f"""\
            import sys
            import os

            # Ensure proxy module is importable
            sys.path.insert(0, {real_proxy_root!r})
            from brain_mcp import proxy as proxy_mod

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

                p._start_writer_loop()
                p._start_recovery_loop()
                p._start_reader_loop()

                success = p._start_child()
                if not success:
                    p._signal_recovery(exit_code=1)

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
        env["BRAIN_PROXY_BACKOFF"] = "0,0,0,0,0"

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
            proc.stdin.write(_make_jsonrpc("ping", id=2, params={"name": "anything"}))
            proc.stdin.flush()

            # Retry the follow-up until the restarted child serves a result; a
            # request mid-restart gets a transient "server restarting" error
            # (with the drift note appended), not the success we assert on.
            all_msgs, resp = _call_until_result(proc, {"name": "anything"}, start_id=3)

            assert resp is not None, f"No successful response after restart. Got: {all_msgs}"

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
