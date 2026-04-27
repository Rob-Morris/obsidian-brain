#!/usr/bin/env python3
"""
Brain MCP Proxy — thin stdio proxy between MCP client and brain MCP server.

Owns the stdio channel so it survives server restarts. Spawns the configured
server target as a child subprocess, forwards messages bidirectionally, and
handles restart logic with exponential backoff.

Usage:
    python -m brain_mcp.proxy <python_path> <server_target>

Env:
    BRAIN_VAULT_ROOT        — vault path (passed through to child)
    BRAIN_WORKSPACE_DIR     — optional active workspace path (passed through to child)
    BRAIN_LOG_LEVEL         — file handler log level (default INFO)
    BRAIN_PROXY_BACKOFF     — comma-separated int seconds (default: 0,4,8,16,32)
    BRAIN_PROXY_INIT_TIMEOUT — seconds to wait for child initialize response (default 60)
    BRAIN_PROXY_VERSION_CHECK_INTERVAL — rate-limit for version-reset checks (default 5)
"""

import hashlib
import json
import logging
import logging.handlers
import os
import queue
import re
import select
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROXY_VERSION = "0.4.0"

_LOG_REL = os.path.join(".brain", "local", "mcp-proxy.log")
_LOG_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
_LOG_BACKUP_COUNT = 1

_DEFAULT_BACKOFF = [0, 4, 8, 16, 32]
_CHILD_ALIVE_RESET_SECS = 60  # reset backoff if child lives this long
_EXIT_CODE_VERSION_DRIFT = 10
_EXIT_CODE_CLEAN = 0
_READER_SELECT_TIMEOUT = 30  # seconds; override with BRAIN_PROXY_READ_TIMEOUT
_HANG_CONSECUTIVE_LIMIT = 3  # kill child after this many timeouts with in-flight requests
_MAX_REPLAY_DEPTH = 1  # cap replay to prevent infinite drift loops
_VERSION_CHECK_INTERVAL = 5  # seconds; override with BRAIN_PROXY_VERSION_CHECK_INTERVAL

def _unrecoverable_msg(reason: str) -> str:
    """Build a user-facing error message for an unrecoverable proxy state.

    All such messages share the `MCP unrecoverable — ...` prefix so clients
    can recognise terminal states regardless of cause.
    """
    return f"MCP unrecoverable — {reason}. Restart MCP via /mcp."


_RECOVERY_THREAD_CRASHED_MSG = _unrecoverable_msg("proxy recovery thread crashed")

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

_logger: logging.Logger | None = None


def _setup_logging(vault_root: str) -> logging.Logger:
    """Configure file + stderr logging for the proxy."""
    log_path = os.path.join(vault_root, _LOG_REL)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    logger = logging.getLogger("brain-proxy")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    env_level = os.environ.get("BRAIN_LOG_LEVEL", "INFO").upper()
    file_level = getattr(logging, env_level, logging.INFO)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT,
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(file_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter("[brain-proxy] %(levelname)s: %(message)s"))
    logger.addHandler(stderr_handler)

    return logger


def _log() -> logging.Logger:
    global _logger
    if _logger is None:
        # Fallback before vault root is known — stderr only
        _logger = logging.getLogger("brain-proxy")
        if not _logger.handlers:
            _logger.setLevel(logging.DEBUG)
            h = logging.StreamHandler(sys.stderr)
            h.setLevel(logging.WARNING)
            _logger.addHandler(h)
    return _logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_proxy_version_from_disk(proxy_script: str) -> str | None:
    """Read PROXY_VERSION from proxy.py on disk via regex (not import)."""
    try:
        with open(proxy_script, "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r'^PROXY_VERSION\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
        if m:
            return m.group(1)
    except OSError:
        pass
    return None


def _read_brain_version_from_disk(vault_root: str) -> str | None:
    """Read .brain-core/VERSION from vault root."""
    version_path = os.path.join(vault_root, ".brain-core", "VERSION")
    try:
        with open(version_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _make_error_response(msg_id: int | str | None, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def _write_line(stream, obj: dict) -> None:
    """Serialize obj as NDJSON and write to stream."""
    line = json.dumps(obj) + "\n"
    stream.write(line.encode("utf-8"))
    stream.flush()


def _decorate_with_drift_note(response: dict, old_ver: str, new_ver: str) -> dict:
    """
    Inject a proxy drift note into any outbound response — success or error.
    Notifications (no result/error) are returned unchanged.
    For success responses: appends to first text content item.
    For error responses: appends to error.message.
    Returns a (possibly modified) shallow copy.
    """
    note = (
        f"\n\nNote: MCP proxy has been upgraded ({old_ver} → {new_ver}). "
        "Restart MCP server via /mcp to load new proxy."
    )
    # Success response with text content
    try:
        content = response.get("result", {}).get("content", [])
        if isinstance(content, list):
            for i, item in enumerate(content):
                if isinstance(item, dict) and item.get("type") == "text":
                    new_item = dict(item)
                    new_item["text"] = new_item.get("text", "") + note
                    new_content = list(content)
                    new_content[i] = new_item
                    modified = dict(response)
                    modified["result"] = dict(modified["result"])
                    modified["result"]["content"] = new_content
                    return modified
    except Exception:
        pass
    # Error response
    try:
        err = response.get("error")
        if isinstance(err, dict) and "message" in err:
            modified = dict(response)
            modified["error"] = dict(err)
            modified["error"]["message"] = err["message"] + note
            return modified
    except Exception:
        pass
    return response


def _get_backoff_schedule() -> list[int]:
    """Return backoff schedule from env or default."""
    env = os.environ.get("BRAIN_PROXY_BACKOFF", "")
    if env.strip():
        try:
            return [int(x.strip()) for x in env.split(",")]
        except ValueError:
            pass
    return list(_DEFAULT_BACKOFF)


def _get_init_timeout() -> int:
    """Return child initialize timeout in seconds."""
    try:
        return int(os.environ.get("BRAIN_PROXY_INIT_TIMEOUT", "60"))
    except ValueError:
        return 60


def _get_read_timeout() -> int:
    """Return reader thread select timeout in seconds."""
    try:
        return int(os.environ.get("BRAIN_PROXY_READ_TIMEOUT", str(_READER_SELECT_TIMEOUT)))
    except ValueError:
        return _READER_SELECT_TIMEOUT


def _get_version_check_interval() -> float:
    """Return version-reset rate-limit interval in seconds."""
    try:
        return float(os.environ.get("BRAIN_PROXY_VERSION_CHECK_INTERVAL", str(_VERSION_CHECK_INTERVAL)))
    except ValueError:
        return float(_VERSION_CHECK_INTERVAL)


# ---------------------------------------------------------------------------
# ChildProcess
# ---------------------------------------------------------------------------

class ChildProcess:
    """Manages a single child server subprocess."""

    def __init__(self, python_path: str, server_target: str):
        self.python_path = python_path
        self.server_target = server_target
        self._proc: subprocess.Popen | None = None
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> None:
        """Spawn the child process."""
        env = os.environ.copy()
        if os.path.isfile(self.server_target):
            cmd = [self.python_path, self.server_target]
        else:
            cmd = [self.python_path, "-m", self.server_target]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        # Forward child stderr to proxy stderr in a background thread
        self._stderr_thread = threading.Thread(
            target=self._relay_stderr, daemon=True, name="child-stderr"
        )
        self._stderr_thread.start()
        _log().info(
            "child started: pid=%d cmd=%s",
            self._proc.pid, cmd,
        )

    def _relay_stderr(self) -> None:
        """Read child stderr and write to proxy stderr."""
        assert self._proc is not None
        try:
            for line in self._proc.stderr:
                sys.stderr.buffer.write(line)
                sys.stderr.buffer.flush()
        except Exception:
            pass

    def send(self, obj: dict) -> None:
        """Send a JSON object to the child's stdin."""
        assert self._proc is not None and self._proc.stdin is not None
        _write_line(self._proc.stdin, obj)

    def readline(self) -> bytes | None:
        """Read one line from child stdout. Returns None on EOF."""
        assert self._proc is not None and self._proc.stdout is not None
        line = self._proc.stdout.readline()
        return line if line else None

    def wait(self) -> int | None:
        """Wait for child to exit. Returns exit code."""
        if self._proc is None:
            return None
        return self._proc.wait()

    def kill(self) -> None:
        """Kill the child process."""
        if self._proc is not None:
            try:
                self._proc.kill()
            except OSError:
                pass

    def poll(self) -> int | None:
        """Return exit code if exited, else None."""
        if self._proc is None:
            return None
        return self._proc.poll()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def stdout_fd(self) -> int | None:
        """Return the file descriptor of child stdout, or None."""
        if self._proc and self._proc.stdout:
            return self._proc.stdout.fileno()
        return None


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------

class Proxy:
    """
    Main proxy logic. Owns the stdio channel (sys.stdin.buffer / sys.stdout.buffer).
    Spawns ChildProcess, forwards messages, handles restarts with backoff.
    """

    def __init__(self, python_path: str, server_target: str, vault_root: str):
        self.python_path = python_path
        self.server_target = server_target
        self.vault_root = vault_root
        self.proxy_script = os.path.abspath(__file__)

        self._child: ChildProcess | None = None
        self._child_lock = threading.Lock()

        # initialize capture
        self._init_request: dict | None = None
        self._init_response: dict | None = None

        # Backoff state
        self._backoff_schedule = _get_backoff_schedule()
        self._backoff_slot = 0               # index into schedule
        self._child_start_time: float = 0.0  # monotonic time child was started
        self._gave_up = False
        self._last_launched_version: str | None = None

        # Proxy drift detection
        self._proxy_drift = False
        self._proxy_version_on_disk: str | None = None  # version read from disk
        self._proxy_file_hash = self._compute_proxy_hash()  # hash at startup

        # In-flight request tracking — maps request ID to full request object
        # for requests forwarded to the child but not yet answered.
        # Protected by _inflight_lock.
        self._inflight_requests: dict[int | str, dict] = {}
        self._inflight_lock = threading.Lock()
        # _pending_replay holds drained-but-not-yet-replayed requests across
        # the wake/sleep boundary on a drift restart. Protected by
        # _restart_lock (NOT _inflight_lock — drain copies under _inflight_lock,
        # then hands the snapshot to recovery state under _restart_lock).
        self._pending_replay: list[dict] = []
        self._replay_depth = 0  # prevent infinite drift→replay loops

        # Synchronization
        self._child_ready = threading.Event()  # set when child is assigned
        self._recovery_trigger = threading.Event()
        self._restart_lock = threading.Lock()
        self._restart_in_progress = False
        self._recovery_exit_code: int | None = None
        self._version_reset_requested = False
        self._last_version_check: float = 0.0  # monotonic timestamp for cooldown

        # Outbound message queue — all writes to sys.stdout.buffer go through here.
        # A single writer thread drains this queue, ensuring thread-safe writes
        # and centralised drift-note decoration. None is the shutdown sentinel.
        self._outbound: queue.Queue[dict | None] = queue.Queue()
        self._writer_thread_handle: threading.Thread | None = None
        self._reader_thread_handle: threading.Thread | None = None
        self._recovery_thread_handle: threading.Thread | None = None
        # Monotonic write-once: only ever flips False → True. Concurrent
        # setters are safe because every writer is setting the same value.
        self._recovery_thread_failed = False

        # Shutdown flag
        self._shutdown = False

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _start_writer_loop(self) -> None:
        """Start the client-writer thread once."""
        if self._writer_thread_handle is not None:
            return
        writer = threading.Thread(
            target=self._writer_thread, daemon=True, name="client-writer"
        )
        writer.start()
        self._writer_thread_handle = writer

    def _start_recovery_loop(self) -> None:
        """Start the child-recovery thread once."""
        if self._recovery_thread_handle is not None:
            return
        recovery = threading.Thread(
            target=self._run_recovery_thread, daemon=True, name="child-recovery"
        )
        recovery.start()
        self._recovery_thread_handle = recovery

    def _start_reader_loop(self) -> None:
        """Start the child-reader thread once."""
        if self._reader_thread_handle is not None:
            return
        reader = threading.Thread(
            target=self._reader_thread, daemon=True, name="child-reader"
        )
        reader.start()
        self._reader_thread_handle = reader

    def _ensure_background_threads(self) -> None:
        """Start background threads if they have not been started yet."""
        self._start_writer_loop()
        self._start_recovery_loop()
        self._start_reader_loop()

    def _recovery_thread_is_dead(self) -> bool:
        """True if the recovery thread was started but is no longer running."""
        handle = self._recovery_thread_handle
        return handle is not None and not handle.is_alive()

    def _start_child(self) -> bool:
        """
        Spawn child and perform initialize handshake.
        Returns True on success, False on failure (timeout or crash).
        """
        child = ChildProcess(self.python_path, self.server_target)
        try:
            child.start()
        except Exception as e:
            _log().error("failed to start child process: %s", e)
            return False
        self._child_start_time = time.monotonic()
        self._last_launched_version = _read_brain_version_from_disk(self.vault_root)

        # Check for proxy drift
        self._check_proxy_drift()

        # Replay initialize only after the first session has completed it
        # (init_response captured) — otherwise the bootstrap request would be
        # sent twice: once here and again by the main loop.
        if self._init_request is not None and self._init_response is not None:
            try:
                child.send(self._init_request)
            except Exception as e:
                _log().error("failed to send initialize to new child: %s", e)
                child.kill()
                return False

            # Read response with timeout
            response = self._read_with_timeout(child, _get_init_timeout())
            if response is None:
                _log().error("child init timeout or crash during restart")
                child.kill()
                return False

            _log().info("child restarted successfully, discarding init response")

            # Notify client that tools may have changed
            try:
                self._send_to_client({
                    "jsonrpc": "2.0",
                    "method": "notifications/tools/list_changed",
                })
                _log().info("sent notifications/tools/list_changed to client")
            except Exception as e:
                _log().error("failed to send list_changed notification: %s", e)

        with self._child_lock:
            self._child = child
        self._child_ready.set()
        return True

    def _send_to_client(self, obj: dict) -> None:
        """Enqueue a message for the client. Thread-safe. Never raises."""
        self._outbound.put(obj)

    def _initiate_shutdown(self) -> None:
        """Begin proxy shutdown and wake any sleeping background threads."""
        self._shutdown = True
        self._child_ready.set()
        self._recovery_trigger.set()

    def _writer_thread(self) -> None:
        """
        Single thread that owns sys.stdout.buffer writes.
        Drains self._outbound, applies drift decoration, writes to stdout.
        Stops on None sentinel or BrokenPipeError.
        """
        while True:
            obj = self._outbound.get()
            if obj is None:
                break
            if self._proxy_drift and self._proxy_version_on_disk:
                obj = _decorate_with_drift_note(obj, PROXY_VERSION, self._proxy_version_on_disk)
            try:
                _write_line(sys.stdout.buffer, obj)
            except BrokenPipeError:
                _log().warning("client disconnected (broken pipe on stdout)")
                self._initiate_shutdown()
                return
            except Exception as e:
                _log().error("error writing to client stdout: %s", e)
                self._initiate_shutdown()
                return

    def _read_with_timeout(self, child: ChildProcess, timeout: int) -> dict | None:
        """
        Read one JSON line from child stdout within timeout seconds.
        Uses select() instead of a daemon thread to avoid leaked threads.
        Returns parsed dict or None on timeout/error.
        """
        fd = child.stdout_fd
        if fd is None:
            return None

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _log().warning("child initialize timed out after %ds", timeout)
                return None

            try:
                ready, _, _ = select.select([fd], [], [], remaining)
            except (ValueError, OSError):
                return None

            if ready:
                try:
                    line = child.readline()
                    if line:
                        return json.loads(line.decode("utf-8").strip())
                    # EOF — child died
                    return None
                except Exception as e:
                    _log().error("error reading child initialize response: %s", e)
                    return None

    def _compute_proxy_hash(self, content: bytes | None = None) -> str | None:
        """Compute SHA-256 hash prefix of proxy.py. Uses provided content or reads from disk."""
        try:
            if content is None:
                with open(self.proxy_script, "rb") as f:
                    content = f.read()
            return hashlib.sha256(content).hexdigest()[:12]
        except OSError:
            return None

    def _check_proxy_drift(self) -> None:
        """Read proxy file from disk once, check version string and hash."""
        try:
            with open(self.proxy_script, "rb") as f:
                content = f.read()
        except OSError:
            return
        # Extract version string
        m = re.search(
            rb'^PROXY_VERSION\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE,
        )
        if not m:
            return
        on_disk = m.group(1).decode("utf-8")
        if on_disk != PROXY_VERSION:
            if not self._proxy_drift:
                _log().warning(
                    "proxy drift detected: running=%s disk=%s", PROXY_VERSION, on_disk
                )
            self._proxy_drift = True
            self._proxy_version_on_disk = on_disk
            return
        # Version strings match — fall back to file hash comparison
        disk_hash = self._compute_proxy_hash(content)
        if (
            disk_hash is not None
            and self._proxy_file_hash is not None
            and disk_hash != self._proxy_file_hash
        ):
            if not self._proxy_drift:
                _log().warning(
                    "proxy drift detected via hash: running=%s disk=%s",
                    self._proxy_file_hash, disk_hash,
                )
            self._proxy_drift = True
            self._proxy_version_on_disk = f"{PROXY_VERSION}+modified"
        else:
            self._proxy_drift = False
            self._proxy_version_on_disk = None

    # ------------------------------------------------------------------
    # Backoff / restart logic
    # ------------------------------------------------------------------

    def _should_reset_backoff(self) -> bool:
        """True if child has been alive long enough to reset backoff."""
        return (
            self._child_start_time > 0
            and time.monotonic() - self._child_start_time >= _CHILD_ALIVE_RESET_SECS
        )

    def _finish_recovery_cycle(self) -> None:
        """Clear the active recovery marker after a recovery attempt ends."""
        with self._restart_lock:
            self._restart_in_progress = False
            self._recovery_exit_code = None
            self._version_reset_requested = False

    def _fail_pending_replay(self, message: str) -> None:
        """Fail any saved replay requests when recovery cannot complete."""
        with self._restart_lock:
            pending = list(self._pending_replay)
            self._pending_replay.clear()
        if pending:
            self._replay_depth = 0
            self._send_client_errors(pending, message)

    def _replay_pending_requests(self) -> None:
        """Replay saved requests to the current child before ending recovery."""
        with self._restart_lock:
            pending = list(self._pending_replay)
            self._pending_replay.clear()
        if pending:
            self._replay_requests(pending)

    def _signal_recovery(
        self,
        exit_code: int | None,
        *,
        child: ChildProcess | None = None,
    ) -> bool:
        """
        Record child loss and wake the recovery thread.

        Returns True if this call claimed the recovery work.
        """
        if exit_code is None:
            exit_code = 1

        if not self._proxy_drift:
            self._check_proxy_drift()

        with self._restart_lock:
            if self._shutdown or self._restart_in_progress:
                return False

            with self._child_lock:
                if child is not None:
                    if self._child is not child:
                        return False
                    self._child = None
                elif self._child is not None:
                    return False

            self._child_ready.clear()
            self._restart_in_progress = True
            self._recovery_exit_code = exit_code

            is_drift = exit_code == _EXIT_CODE_VERSION_DRIFT
            can_replay = is_drift and self._replay_depth < _MAX_REPLAY_DEPTH
            drained_requests = self._drain_inflight()

            if can_replay:
                for req in drained_requests:
                    _log().info("saving in-flight request id=%s for replay", req.get("id"))
                self._pending_replay = drained_requests
                self._replay_depth += 1
            else:
                if drained_requests:
                    self._replay_depth = 0
                self._pending_replay = []

        recovery_dead = self._recovery_thread_is_dead() or self._recovery_thread_failed
        if not can_replay and drained_requests:
            # When recovery cannot proceed, the orphan response must reflect
            # that — the soft "restarting" message would mislead the client
            # into retrying against a proxy that will never recover.
            message = (
                _RECOVERY_THREAD_CRASHED_MSG if recovery_dead
                else "server exited mid-request, restarting"
            )
            self._send_client_errors(drained_requests, message)

        self._recovery_trigger.set()

        # If the recovery thread is no longer running, set() above wakes
        # nobody — anything we just queued for replay would sit forever in
        # _pending_replay. Fail it now so the client gets a response, and
        # mark the proxy as unrecoverable so subsequent requests do too.
        # Gated on (pending_replay populated OR first detection) so steady-
        # state dead-recovery hits don't keep re-acquiring _restart_lock.
        if recovery_dead and (
            self._pending_replay or not self._recovery_thread_failed
        ):
            self._recovery_thread_failed = True
            self._fail_pending_replay(_RECOVERY_THREAD_CRASHED_MSG)

        return True

    def _signal_version_reset(self) -> None:
        """Ask the recovery thread to re-check VERSION after give-up."""
        with self._restart_lock:
            if self._shutdown or not self._gave_up:
                return
            self._version_reset_requested = True
        self._recovery_trigger.set()

    def _maybe_begin_version_reset(self) -> bool:
        """
        After give-up, check whether VERSION changed and restart recovery if so.
        """
        with self._restart_lock:
            requested = self._version_reset_requested
            gave_up = self._gave_up
        if not requested or not gave_up:
            return False

        now = time.monotonic()
        if now - self._last_version_check < _get_version_check_interval():
            with self._restart_lock:
                self._version_reset_requested = False
            return False
        self._last_version_check = now

        current_version = _read_brain_version_from_disk(self.vault_root)
        if current_version == self._last_launched_version:
            with self._restart_lock:
                self._version_reset_requested = False
            return False

        _log().info(
            "brain-core VERSION changed (%s → %s) — resetting backoff",
            self._last_launched_version, current_version,
        )
        with self._restart_lock:
            self._gave_up = False
            self._backoff_slot = 0
            self._restart_in_progress = True
            self._recovery_exit_code = 1
            self._version_reset_requested = False
        return True

    def _recover_from_exit(self, exit_code: int) -> None:
        """
        Recovery-thread owner for restart/backoff.

        Detection paths only populate recovery state and wake this loop.
        """
        _log().info("child exited with code %d", exit_code)

        if exit_code == _EXIT_CODE_CLEAN:
            _log().info("clean child exit — proxy shutting down")
            self._fail_pending_replay("server shutting down")
            self._finish_recovery_cycle()
            self._initiate_shutdown()
            return

        if self._should_reset_backoff():
            _log().info("child was healthy for >%ds — resetting backoff", _CHILD_ALIVE_RESET_SECS)
            with self._restart_lock:
                self._backoff_slot = 0

        if exit_code == _EXIT_CODE_VERSION_DRIFT:
            _log().info("exit code %d: version drift — restarting immediately", exit_code)
            if self._start_child():
                self._replay_pending_requests()
                self._finish_recovery_cycle()
                return
            _log().warning("version-drift restart failed — falling through to backoff")
            with self._restart_lock:
                self._backoff_slot = 0

        while not self._shutdown:
            with self._restart_lock:
                if self._backoff_slot >= len(self._backoff_schedule):
                    attempts = len(self._backoff_schedule)
                    self._gave_up = True
                    self._restart_in_progress = False
                    self._recovery_exit_code = None
                    exhausted = True
                else:
                    slot = self._backoff_slot
                    delay = self._backoff_schedule[slot]
                    exhausted = False

            if exhausted:
                _log().error("backoff exhausted after %d attempts — giving up", attempts)
                self._fail_pending_replay("server restart failed")
                return

            _log().warning(
                "child restart — backoff slot %d/%d, waiting %ds",
                slot, len(self._backoff_schedule) - 1, delay,
            )
            if delay > 0 and self._recovery_trigger.wait(timeout=delay):
                self._recovery_trigger.clear()
                if self._shutdown:
                    self._fail_pending_replay("server shutting down")
                    self._finish_recovery_cycle()
                    return

            with self._restart_lock:
                self._backoff_slot += 1
                attempt = self._backoff_slot
            if self._start_child():
                self._replay_pending_requests()
                self._finish_recovery_cycle()
                return
            _log().warning("child restart attempt %d failed", attempt)

        self._fail_pending_replay("server shutting down")
        self._finish_recovery_cycle()

    def _handle_recovery_thread_crash(self) -> None:
        """Mark the recovery thread as crashed and fail any saved replay work."""
        _log().exception("proxy recovery thread crashed")
        self._fail_pending_replay(_RECOVERY_THREAD_CRASHED_MSG)
        with self._restart_lock:
            self._restart_in_progress = False
            self._recovery_exit_code = None
            self._version_reset_requested = False
        self._recovery_thread_failed = True

    def _run_recovery_thread(self) -> None:
        """Thread entrypoint wrapper so recovery crashes stay observable."""
        try:
            self._recovery_thread()
        except Exception:
            self._handle_recovery_thread_crash()

    def _recovery_thread(self) -> None:
        """Dedicated recovery loop. Owns every backoff sleep and restart attempt."""
        while True:
            if self._shutdown:
                return

            self._recovery_trigger.wait()
            self._recovery_trigger.clear()

            if self._shutdown:
                return

            self._maybe_begin_version_reset()

            with self._restart_lock:
                if not self._restart_in_progress:
                    continue
                exit_code = self._recovery_exit_code if self._recovery_exit_code is not None else 1

            self._recover_from_exit(exit_code)

    # ------------------------------------------------------------------
    # Reader thread (child stdout → proxy stdout)
    # ------------------------------------------------------------------

    def _drain_inflight(self) -> list[dict]:
        """
        Atomically clear and return the in-flight request map.

        Caller decides what to do with the orphans (error to client, save for
        replay, etc.) — this method only owns the lock-protected snapshot.
        """
        with self._inflight_lock:
            orphans = list(self._inflight_requests.values())
            self._inflight_requests.clear()
        return orphans

    def _send_client_errors(self, requests: list[dict], message: str) -> None:
        """Send JSON-RPC error responses to the client for a list of requests."""
        for req in requests:
            req_id = req.get("id")
            _log().warning("orphaned in-flight request id=%s — sending error to client", req_id)
            self._send_to_client(_make_error_response(req_id, -32603, message))

    def _replay_requests(self, requests: list[dict]) -> None:
        """
        Replay saved requests to the current child after a version-drift restart.
        Called from the recovery thread after a successful restart.
        """
        child = self._get_child()
        if child is None:
            self._send_client_errors(requests, "server restart failed")
            self._replay_depth = 0
            return

        replayed_any = False
        for req in requests:
            req_id = req.get("id")
            _log().info("replaying request id=%s to new child", req_id)
            try:
                child.send(req)
                with self._inflight_lock:
                    self._inflight_requests[req_id] = req
                replayed_any = True
            except Exception as e:
                _log().error("replay failed for request id=%s: %s", req_id, e)
                self._send_client_errors([req], "replay failed after restart")
        if not replayed_any:
            self._replay_depth = 0

    def _reader_thread(self) -> None:
        """
        Continuously reads from child stdout and writes to proxy stdout.
        Uses select() with timeout for health checking. Handles child exit,
        version-drift replay, and restart logic.
        """
        timeout = _get_read_timeout()
        hang_counter = 0

        try:
            while not self._shutdown:
                child = self._get_child()
                if child is None:
                    self._child_ready.wait(timeout=1.0)
                    self._child_ready.clear()
                    hang_counter = 0
                    continue

                fd = child.stdout_fd
                if fd is None:
                    line = None
                else:
                    try:
                        ready, _, _ = select.select([fd], [], [], timeout)
                    except (ValueError, OSError):
                        line = None
                        ready = None
                    if ready is not None:
                        if ready:
                            line = child.readline()
                            hang_counter = 0
                        else:
                            # Timeout — check child health
                            poll = child.poll()
                            if poll is not None:
                                # Child already dead — treat as EOF
                                line = None
                            else:
                                # Child alive — check for hang
                                with self._inflight_lock:
                                    has_inflight = bool(self._inflight_requests)
                                if has_inflight:
                                    hang_counter += 1
                                    _log().warning(
                                        "child unresponsive with in-flight requests "
                                        "(timeout %d/%d)",
                                        hang_counter, _HANG_CONSECUTIVE_LIMIT,
                                    )
                                    if hang_counter >= _HANG_CONSECUTIVE_LIMIT:
                                        _log().error(
                                            "killing hung child after %d consecutive timeouts",
                                            hang_counter,
                                        )
                                        child.kill()
                                        line = None  # trigger EOF path
                                    else:
                                        continue
                                else:
                                    # No in-flight requests — idle is fine
                                    continue

                if line is None:
                    # Child stdout closed — wait for exit code
                    exit_code = child.wait()
                    _log().info("child stdout EOF, exit code=%s", exit_code)
                    hang_counter = 0
                    self._signal_recovery(exit_code, child=child)
                    continue

                # Parse and forward to client
                try:
                    obj = json.loads(line.decode("utf-8").strip())
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    _log().debug("failed to parse child output: %s", e)
                    continue

                msg_id = obj.get("id")
                method = obj.get("method")
                _log().debug("child→client: id=%s method=%s", msg_id, method)

                # Remove from in-flight tracking (response received)
                if msg_id is not None:
                    with self._inflight_lock:
                        self._inflight_requests.pop(msg_id, None)
                    self._replay_depth = 0

                # Capture initialize response (first time only)
                if (
                    self._init_response is None
                    and self._init_request is not None
                    and msg_id == self._init_request.get("id")
                    and "result" in obj
                ):
                    self._init_response = obj
                    _log().info("captured initialize response from child")

                self._send_to_client(obj)
        except Exception:
            _log().exception("proxy reader thread crashed")
            self._initiate_shutdown()

    def _get_child(self) -> ChildProcess | None:
        with self._child_lock:
            return self._child

    # ------------------------------------------------------------------
    # Main loop (proxy stdin → child stdin)
    # ------------------------------------------------------------------

    def _error_response_for_dead_child(self, msg_id: int | str | None) -> dict:
        if self._recovery_thread_failed:
            message = _RECOVERY_THREAD_CRASHED_MSG
        elif self._gave_up:
            message = _unrecoverable_msg(
                f"server restart failed after {len(self._backoff_schedule)} recovery attempts"
            )
        else:
            message = "server restarting, please retry"
        return _make_error_response(msg_id, -32603, message)

    def run(self) -> None:
        """Main proxy loop. Reads from stdin, forwards to child."""
        self._ensure_background_threads()

        stdin = sys.stdin.buffer

        while not self._shutdown:
            try:
                line = stdin.readline()
            except Exception as e:
                _log().error("error reading from stdin: %s", e)
                self._initiate_shutdown()
                break

            if not line:
                _log().info("stdin EOF — proxy shutting down")
                self._initiate_shutdown()
                break

            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                _log().warning("failed to parse client message: %s", e)
                continue

            msg_id = obj.get("id")
            method = obj.get("method", "")
            is_notification = msg_id is None and method
            is_request = msg_id is not None

            _log().debug("client→child: id=%s method=%s", msg_id, method)

            # Keep the latest initialize request until we capture a matching
            # initialize response from a live child.
            if method == "initialize" and self._init_response is None:
                self._init_request = obj
                _log().info("captured initialize request from client")

            child = self._get_child()
            if child is not None:
                exit_code = child.poll()
                if exit_code is not None:
                    self._signal_recovery(exit_code, child=child)
                    child = None

            recovery_thread_dead = child is None and self._recovery_thread_is_dead()
            if recovery_thread_dead and (
                self._pending_replay or not self._recovery_thread_failed
            ):
                self._recovery_thread_failed = True
                self._fail_pending_replay(_RECOVERY_THREAD_CRASHED_MSG)

            if self._restart_in_progress or child is None:
                if self._gave_up and method == "tools/call" and not recovery_thread_dead:
                    self._signal_version_reset()
                if is_request:
                    self._send_to_client(self._error_response_for_dead_child(msg_id))
                elif is_notification:
                    _log().debug("dropping notification (child dead): method=%s", method)
                continue

            # Forward to child
            if is_request:
                with self._inflight_lock:
                    self._inflight_requests[msg_id] = obj
            try:
                child.send(obj)
            except BrokenPipeError:
                _log().warning("child stdin broken pipe — child likely died")
                # poll() can return None if reaping hasn't completed; wait()
                # gives the real exit code so a drift exit (10) isn't
                # misclassified as a crash and silently loses replay.
                exit_code = child.poll()
                if exit_code is None:
                    try:
                        exit_code = child.wait()
                    except Exception:
                        exit_code = 1
                owned_recovery = self._signal_recovery(exit_code, child=child)
                if not owned_recovery and is_request:
                    # Reader thread already drove recovery; if its drain ran
                    # before our inflight insert, our id is still tracked and
                    # the client is owed a response.
                    with self._inflight_lock:
                        was_tracked = self._inflight_requests.pop(msg_id, None) is not None
                    if was_tracked:
                        self._send_to_client(self._error_response_for_dead_child(msg_id))
                continue
            except Exception as e:
                _log().error("error sending to child: %s", e)

        # Shutdown — kill child if still running
        self._initiate_shutdown()
        child = self._get_child()
        if child:
            _log().info("proxy shutting down — killing child pid=%s", child.pid)
            child.kill()

        reader = self._reader_thread_handle
        if reader is not None and reader.is_alive():
            reader.join(timeout=5.0)

        recovery = self._recovery_thread_handle
        if recovery is not None and recovery.is_alive():
            recovery.join(timeout=5.0)

        # Signal writer thread to drain remaining messages and stop
        self._outbound.put(None)
        writer = self._writer_thread_handle
        if writer is not None and writer.is_alive():
            writer.join(timeout=5.0)

        _log().info("proxy exited")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) != 3:
        print(
            f"Usage: {sys.argv[0]} <python_path> <server_target>",
            file=sys.stderr,
        )
        sys.exit(1)

    python_path = sys.argv[1]
    server_target = sys.argv[2]

    vault_root = os.environ.get("BRAIN_VAULT_ROOT", "")
    if not vault_root:
        # Fall back to cwd if vault root not set
        vault_root = os.getcwd()

    proxy = Proxy(python_path, server_target, vault_root)
    global _logger
    _logger = _setup_logging(vault_root)
    _log().info(
        "proxy starting: version=%s python=%s target=%s vault=%s",
        PROXY_VERSION, python_path, server_target, vault_root,
    )

    proxy._start_writer_loop()
    proxy._start_recovery_loop()
    proxy._start_reader_loop()

    # Start the child for the first time
    _log().info("spawning initial child process")
    if not proxy._start_child():
        _log().error("initial child start failed — entering restart backoff")
        proxy._signal_recovery(exit_code=1)

    proxy.run()


if __name__ == "__main__":
    main()
