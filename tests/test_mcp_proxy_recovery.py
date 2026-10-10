"""Proxy recovery coordination, initial-start failures and crash backoff."""

import json
import textwrap
import threading
import time
import pytest

from proxy_test_support import (
    _BlockingFakeStdin,
    _FakeChild,
    _FakeStdin,
    _GIVE_UP_MSG_FRAGMENT,
    _call_until_error,
    _echo_server_script,
    _exhaust_backoff,
    _find_by_id,
    _launch_proxy,
    _make_inprocess_proxy,
    _make_inprocess_proxy_with_real_threads,
    _make_jsonrpc,
    _read_responses,
    _read_until_id,
    _run_proxy_wrapper,
    _wait_for,
    _with_interface_discovery,
    _write_vault,
)

pytestmark = pytest.mark.slow


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
        f.write(_with_interface_discovery(script))
    return path


class TestMainLoopRecoveryPaths:
    """Main-loop dead-child paths fail fast instead of blocking on recovery."""

    def test_initialize_during_recovery_returns_soft_error(self, tmp_path, monkeypatch):
        proxy, sent_to_client = _make_inprocess_proxy(
            tmp_path,
            monkeypatch,
            [
                _make_jsonrpc(
                    "initialize",
                    id=1,
                    params={
                        "protocolVersion": "2024-11-05",
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                ).encode("utf-8")
            ],
        )
        dead_child = _FakeChild(poll_values=[1])

        with proxy._child_lock:
            proxy._child = dead_child

        proxy.run()

        assert len(sent_to_client) == 1
        assert sent_to_client[0]["id"] == 1
        assert "error" in sent_to_client[0]
        assert sent_to_client[0]["error"]["message"] == "server restarting, please retry"
        assert proxy._restart_in_progress is True
        assert proxy._get_child() is None

    def test_dead_child_before_send_signals_recovery_and_errors_request(self, tmp_path, monkeypatch):
        proxy, sent_to_client = _make_inprocess_proxy(
            tmp_path,
            monkeypatch,
            [_make_jsonrpc("ping", id=2, params={"name": "ping"}).encode("utf-8")],
        )
        dead_child = _FakeChild(poll_values=[1])

        with proxy._child_lock:
            proxy._child = dead_child

        proxy.run()

        assert len(sent_to_client) == 1
        assert sent_to_client[0]["id"] == 2
        assert "error" in sent_to_client[0]
        assert sent_to_client[0]["error"]["message"] == "server restarting, please retry"
        assert proxy._restart_in_progress is True
        assert proxy._get_child() is None

    def test_broken_pipe_during_send_signals_recovery_and_errors_orphaned_request(
        self, tmp_path, monkeypatch
    ):
        proxy, sent_to_client = _make_inprocess_proxy(
            tmp_path,
            monkeypatch,
            [_make_jsonrpc("ping", id=2, params={"name": "ping"}).encode("utf-8")],
        )
        broken_child = _FakeChild(poll_values=[None, None, 1], send_exception=BrokenPipeError)

        with proxy._child_lock:
            proxy._child = broken_child

        proxy.run()

        assert len(sent_to_client) == 1
        assert sent_to_client[0]["id"] == 2
        assert "error" in sent_to_client[0]
        assert "mid-request" in sent_to_client[0]["error"]["message"]
        assert proxy._restart_in_progress is True
        assert proxy._get_child() is None

    def test_generic_send_error_signals_recovery_and_errors_orphaned_request(
        self, tmp_path, monkeypatch
    ):
        proxy, sent_to_client = _make_inprocess_proxy(
            tmp_path,
            monkeypatch,
            [_make_jsonrpc("ping", id=2, params={"name": "ping"}).encode("utf-8")],
        )
        broken_child = _FakeChild(poll_values=[None], send_exception=RuntimeError)

        with proxy._child_lock:
            proxy._child = broken_child

        proxy.run()

        assert len(sent_to_client) == 1
        assert sent_to_client[0]["id"] == 2
        assert "error" in sent_to_client[0]
        assert "mid-request" in sent_to_client[0]["error"]["message"]
        assert proxy._restart_in_progress is True
        assert proxy._get_child() is None


class TestInitialStartRecovery:
    """Initial child-start failures enter the same restart coordinator."""

    def test_initial_child_start_failure_retries_until_success(self, tmp_path, monkeypatch):
        proxy, _ = _make_inprocess_proxy_with_real_threads(tmp_path, monkeypatch)
        proxy._backoff_schedule = [0, 0, 0]
        replacement = _FakeChild()
        attempts: list[int] = []

        def fake_start_child() -> bool:
            attempts.append(1)
            if len(attempts) < 3:
                return False
            with proxy._child_lock:
                proxy._child = replacement
            proxy._child_ready.set()
            return True

        monkeypatch.setattr(proxy, "_start_child", fake_start_child)
        proxy._start_recovery_loop()

        try:
            assert proxy._signal_recovery(1)
            assert _wait_for(lambda: proxy._get_child() is replacement, timeout=2.0)
            assert len(attempts) == 3
            assert proxy._gave_up is False
        finally:
            proxy._initiate_shutdown()
            recovery = proxy._recovery_thread_handle
            if recovery is not None and recovery.is_alive():
                recovery.join(timeout=2.0)

    def test_subprocess_initial_start_transient_failure_recovers(self, tmp_path):
        """End-to-end: initial ChildProcess.start() fails twice, then succeeds.
        Requests sent during the failure window should get the soft restart
        error immediately, then a later initialize should succeed once recovery
        finishes."""
        _write_vault(tmp_path)
        server_script = _echo_server_script(tmp_path)
        fail_counter = str(tmp_path / "init_fail_counter.txt")
        with open(fail_counter, "w") as f:
            f.write("2")

        patch_body = textwrap.dedent(f"""\
            import time

            _real_start = proxy_mod.ChildProcess.start
            def _flaky_start(self, **options):
                try:
                    with open({fail_counter!r}, "r") as f:
                        remaining = int(f.read().strip())
                except (FileNotFoundError, ValueError):
                    remaining = 0
                if remaining > 0:
                    with open({fail_counter!r}, "w") as f:
                        f.write(str(remaining - 1))
                    time.sleep(0.2)
                    raise OSError("simulated initial start failure")
                return _real_start(self, **options)
            proxy_mod.ChildProcess.start = _flaky_start
            """)

        proc = _run_proxy_wrapper(tmp_path, server_script, patch_body)
        try:
            start = time.monotonic()
            proc.stdin.write(_make_jsonrpc("initialize", id=1,
                                           params={"protocolVersion": "2024-11-05",
                                                   "clientInfo": {"name": "test", "version": "0"}}))
            proc.stdin.flush()

            early_msgs = _read_until_id(proc, 1, timeout=2.0)
            early_resp = _find_by_id(early_msgs, 1)
            assert early_resp is not None, f"Expected immediate soft error, got: {early_msgs}"
            assert "error" in early_resp, f"Expected soft restart error, got: {early_resp}"
            assert early_resp["error"]["message"] == "server restarting, please retry"
            # Includes fresh Python process/import startup plus one deliberate
            # 0.2s child-start failure; keep the bound well below the retry
            # timeout without assuming sub-150ms host startup.
            assert time.monotonic() - start < 0.55, (
                "initialize should fail fast during initial-start recovery"
            )

            # No list_changed is emitted during bootstrap recovery, so there is
            # no restart-complete signal to wait on. Poll the initialize
            # handshake until it succeeds (retrying the soft "server restarting"
            # error with a fresh id each time) instead of a fixed sleep that
            # races the child's cold start under load.
            later_msgs = []
            init_resp = None
            retry_id = 2
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                proc.stdin.write(_make_jsonrpc("initialize", id=retry_id,
                                               params={"protocolVersion": "2024-11-05",
                                                       "clientInfo": {"name": "test", "version": "0"}}))
                proc.stdin.flush()
                msgs = _read_until_id(proc, retry_id, timeout=5.0)
                later_msgs += msgs
                resp = _find_by_id(msgs, retry_id)
                if resp is not None and "result" in resp:
                    init_resp = resp
                    break
                retry_id += 1
            assert init_resp is not None, (
                f"initialize never succeeded after recovery, got: {later_msgs}"
            )
            assert "result" in init_resp, f"Expected success result, got: {init_resp}"

            # No list_changed should be sent during bootstrap recovery — the
            # live initialize handshake is the first one the child has seen.
            list_changed = [m for m in early_msgs + later_msgs
                            if m.get("method") == "notifications/tools/list_changed"]
            assert not list_changed, (
                f"Expected no list_changed during bootstrap, got: {list_changed}"
            )

            id1_responses = [m for m in early_msgs if m.get("id") == 1]
            assert len(id1_responses) == 1, (
                f"Expected exactly one soft error for id=1, got: {id1_responses}"
            )
            init_successes = [
                m for m in later_msgs if "result" in m and m.get("id") is not None
            ]
            assert len(init_successes) == 1, (
                f"Expected exactly one successful initialize, got: {init_successes}"
            )

            with open(fail_counter, "r") as f:
                assert f.read().strip() == "0"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_subprocess_initial_start_permanent_failure_returns_give_up(self, tmp_path):
        """End-to-end: ChildProcess.start() always fails and returns give-up guidance."""
        _write_vault(tmp_path)
        server_script = _echo_server_script(tmp_path)

        patch_body = textwrap.dedent("""\
            def _always_fail(self, **options):
                raise OSError("simulated permanent start failure")
            proxy_mod.ChildProcess.start = _always_fail
            """)

        proc = _run_proxy_wrapper(tmp_path, server_script, patch_body, backoff="0,0,0")
        try:
            all_msgs, resp = _call_until_error(
                proc,
                {"name": "ping"},
                lambda r: "MCP unrecoverable" in r["error"].get("message", ""),
                start_id=1,
                timeout=10.0,
            )
            assert resp is not None, f"Expected give-up error response, got: {all_msgs}"
            error_msg = resp["error"]["message"]
            assert "MCP unrecoverable" in error_msg, (
                f"Expected unrecoverable prefix. Got: {error_msg!r}"
            )
            assert _GIVE_UP_MSG_FRAGMENT in error_msg.lower(), (
                f"Expected explicit give-up message. Got: {error_msg!r}"
            )
            assert "Restart MCP" in error_msg, (
                f"Expected restart guidance. Got: {error_msg!r}"
            )
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestAsyncRecoveryThread:
    """Dedicated recovery-thread behaviours: non-blocking, single-owner, and wake-up semantics."""

    def test_requests_during_recovery_return_immediately_while_restart_runs(
        self, tmp_path, monkeypatch
    ):
        eof_event = threading.Event()
        stdin_lines = [
            _make_jsonrpc("ping", id=req_id, params={"name": "ping"}).encode("utf-8")
            for req_id in range(1, 6)
        ]
        proxy, sent_to_client = _make_inprocess_proxy_with_real_threads(
            tmp_path, monkeypatch, stdin=_BlockingFakeStdin(stdin_lines, eof_event),
        )
        proxy._backoff_schedule = [0]

        start_entered = threading.Event()
        release_start = threading.Event()

        def slow_start_child() -> bool:
            start_entered.set()
            release_start.wait(timeout=5.0)
            return False

        monkeypatch.setattr(proxy, "_start_child", slow_start_child)
        assert proxy._signal_recovery(1)

        run_thread = threading.Thread(target=proxy.run, daemon=True)
        run_thread.start()
        try:
            assert start_entered.wait(timeout=1.0), "recovery thread never entered _start_child"
            assert _wait_for(lambda: len(sent_to_client) == 5, timeout=0.5), (
                f"Expected 5 immediate errors during recovery, got: {sent_to_client}"
            )
            assert all(
                msg["error"]["message"] == "server restarting, please retry"
                for msg in sent_to_client
            )
        finally:
            eof_event.set()
            release_start.set()
            run_thread.join(timeout=2.0)
            assert not run_thread.is_alive(), "proxy.run() did not exit cleanly"

    def test_simultaneous_recovery_signals_only_restart_once(self, tmp_path, monkeypatch):
        proxy, sent_to_client = _make_inprocess_proxy_with_real_threads(tmp_path, monkeypatch)
        proxy._backoff_schedule = [0]

        child = _FakeChild()
        replacement = _FakeChild()
        with proxy._child_lock:
            proxy._child = child
        with proxy._inflight_lock:
            proxy._inflight_requests[2] = (
                json.loads(_make_jsonrpc("ping", id=2, params={"name": "ping"})),
                time.monotonic(),
            )

        start_calls: list[int] = []

        def fake_start_child() -> bool:
            start_calls.append(1)
            with proxy._child_lock:
                proxy._child = replacement
            proxy._child_ready.set()
            return True

        monkeypatch.setattr(proxy, "_start_child", fake_start_child)
        proxy._start_recovery_loop()

        results: list[bool] = []
        try:
            def _signal() -> None:
                results.append(proxy._signal_recovery(1, child=child))

            t1 = threading.Thread(target=_signal, daemon=True)
            t2 = threading.Thread(target=_signal, daemon=True)
            t1.start()
            t2.start()
            t1.join(timeout=1.0)
            t2.join(timeout=1.0)

            assert _wait_for(lambda: proxy._get_child() is replacement, timeout=1.0)
            assert sum(1 for owned in results if owned) == 1
            assert start_calls == [1]
            assert len(sent_to_client) == 1
            assert sent_to_client[0]["id"] == 2
            assert "mid-request" in sent_to_client[0]["error"]["message"]
        finally:
            proxy._initiate_shutdown()
            recovery = proxy._recovery_thread_handle
            if recovery is not None and recovery.is_alive():
                recovery.join(timeout=2.0)

    def test_stdin_eof_during_backoff_stops_recovery_promptly(self, tmp_path, monkeypatch):
        proxy, _ = _make_inprocess_proxy_with_real_threads(
            tmp_path, monkeypatch, stdin=_FakeStdin([]),
        )
        proxy._backoff_schedule = [30]

        proxy._start_recovery_loop()
        assert proxy._signal_recovery(1)
        time.sleep(0.1)

        started = time.monotonic()
        proxy.run()
        elapsed = time.monotonic() - started

        assert elapsed < 5.0, f"stdin EOF should wake recovery wait promptly, took {elapsed:.2f}s"
        recovery = proxy._recovery_thread_handle
        assert recovery is not None
        assert not recovery.is_alive()

    def test_dead_recovery_thread_returns_unrecoverable_error(self, tmp_path, monkeypatch):
        proxy, sent_to_client = _make_inprocess_proxy_with_real_threads(
            tmp_path,
            monkeypatch,
            stdin=_FakeStdin([_make_jsonrpc("ping", id=7, params={"name": "ping"}).encode("utf-8")]),
        )

        def crash_recovery() -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(proxy, "_recovery_thread", crash_recovery)
        proxy._start_recovery_loop()

        assert _wait_for(lambda: proxy._recovery_thread_failed, timeout=1.0), (
            "recovery thread did not crash as expected"
        )

        proxy.run()

        assert len(sent_to_client) == 1
        error_msg = sent_to_client[0]["error"]["message"]
        assert "MCP unrecoverable" in error_msg
        assert "Restart MCP" in error_msg

    def test_crash_signal_after_recovery_crash_returns_unrecoverable(
        self, tmp_path, monkeypatch
    ):
        """Recovery thread crashes while child is alive; later non-drift child
        loss must surface the unrecoverable error to orphaned in-flight
        requests, not the transient 'server exited mid-request, restarting'."""
        proxy, sent_to_client = _make_inprocess_proxy_with_real_threads(
            tmp_path, monkeypatch,
        )

        def crash_recovery() -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(proxy, "_recovery_thread", crash_recovery)
        proxy._start_recovery_loop()
        assert _wait_for(lambda: proxy._recovery_thread_failed, timeout=1.0)

        child = _FakeChild()
        with proxy._child_lock:
            proxy._child = child
        with proxy._inflight_lock:
            proxy._inflight_requests[42] = (
                json.loads(_make_jsonrpc("ping", id=42, params={"name": "ping"})),
                time.monotonic(),
            )

        # Non-drift exit (crash with code 1) — replay path does not apply.
        assert proxy._signal_recovery(1, child=child)

        assert len(sent_to_client) == 1
        assert sent_to_client[0]["id"] == 42
        error_msg = sent_to_client[0]["error"]["message"]
        assert "MCP unrecoverable" in error_msg, (
            f"Expected unrecoverable error since recovery is dead, got: {error_msg!r}"
        )
        assert "Restart MCP" in error_msg

    def test_drift_signal_after_recovery_crash_does_not_strand_inflight(
        self, tmp_path, monkeypatch
    ):
        """Recovery thread crashes while child is alive; later drift exit must not
        leave the in-flight request stranded in _pending_replay forever."""
        proxy, sent_to_client = _make_inprocess_proxy_with_real_threads(
            tmp_path, monkeypatch,
        )

        def crash_recovery() -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(proxy, "_recovery_thread", crash_recovery)
        proxy._start_recovery_loop()

        assert _wait_for(lambda: proxy._recovery_thread_failed, timeout=1.0), (
            "recovery thread did not crash as expected"
        )

        # Stage a still-alive child with an in-flight request.
        child = _FakeChild()
        with proxy._child_lock:
            proxy._child = child
        with proxy._inflight_lock:
            proxy._inflight_requests[42] = (
                json.loads(_make_jsonrpc("ping", id=42, params={"name": "ping"})),
                time.monotonic(),
            )

        # Simulate a drift exit (code 10) signaled by a detection path.
        assert proxy._signal_recovery(10, child=child)

        # The in-flight request must receive an error — not stay stranded in
        # _pending_replay waiting for a recovery thread that will never run.
        assert len(sent_to_client) == 1, (
            f"Expected one error response for the stranded request, got: {sent_to_client}"
        )
        assert sent_to_client[0]["id"] == 42
        error_msg = sent_to_client[0]["error"]["message"]
        assert "MCP unrecoverable" in error_msg
        assert "Restart MCP" in error_msg
        assert proxy._pending_replay == []


class TestCrashBackoff:
    """Proxy gives up after exhausting the backoff schedule."""

    def test_gives_up_after_max_retries(self, tmp_path):
        """Child always crashes on real requests; proxy returns a hard recovery-failed error."""
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
            all_msgs = _exhaust_backoff(proc)

            # Find the error response for id=99 (the give-up probe)
            error_resp = _find_by_id(all_msgs, 99)
            assert error_resp is not None, (
                f"Expected error response with id=99 after backoff exhaustion. Got: {all_msgs}"
            )
            assert "error" in error_resp, f"Expected error, got: {error_resp}"

            error_msg = error_resp["error"]["message"]
            assert "MCP unrecoverable" in error_msg, (
                f"Expected unrecoverable prefix in error message. Got: {error_msg!r}"
            )
            assert _GIVE_UP_MSG_FRAGMENT in error_msg.lower(), (
                f"Expected explicit recovery exhaustion in error message. Got: {error_msg!r}"
            )
            assert "Restart MCP" in error_msg, (
                f"Expected restart instruction in error message. Got: {error_msg!r}"
            )

        finally:
            proc.terminate()
            proc.wait(timeout=5)
