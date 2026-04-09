#!/usr/bin/env python3
"""
Brain MCP Proxy — thin stdio proxy between MCP client and brain MCP server.

Owns the stdio channel so it survives server restarts. Spawns server.py as a
child subprocess, forwards messages bidirectionally, and handles restart logic
with exponential backoff.

Usage:
    python proxy.py <python_path> <server_script>

Env:
    BRAIN_VAULT_ROOT        — vault path (passed through to child)
    BRAIN_LOG_LEVEL         — file handler log level (default INFO)
    BRAIN_PROXY_BACKOFF     — comma-separated int seconds (default: 0,4,8,16,32)
    BRAIN_PROXY_INIT_TIMEOUT — seconds to wait for child initialize response (default 60)
"""

import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROXY_VERSION = "0.1.0"

_LOG_REL = os.path.join(".brain", "local", "mcp-proxy.log")
_LOG_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
_LOG_BACKUP_COUNT = 1

_DEFAULT_BACKOFF = [0, 4, 8, 16, 32]
_CHILD_ALIVE_RESET_SECS = 60  # reset backoff if child lives this long
_EXIT_CODE_VERSION_DRIFT = 10
_EXIT_CODE_CLEAN = 0

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


def _inject_proxy_drift_note(response: dict, old_ver: str, new_ver: str) -> dict:
    """
    If response has result.content[].type=="text", inject a drift note into
    the first text content item. Returns a (possibly modified) shallow copy.
    """
    try:
        content = response.get("result", {}).get("content", [])
        if not isinstance(content, list):
            return response
        for i, item in enumerate(content):
            if isinstance(item, dict) and item.get("type") == "text":
                note = (
                    f"\n\nNote: MCP proxy has been upgraded ({old_ver} → {new_ver}). "
                    "Restart MCP server via /mcp to load new proxy."
                )
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


# ---------------------------------------------------------------------------
# ChildProcess
# ---------------------------------------------------------------------------

class ChildProcess:
    """Manages a single child server subprocess."""

    def __init__(self, python_path: str, server_script: str):
        self.python_path = python_path
        self.server_script = server_script
        self._proc: subprocess.Popen | None = None
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> None:
        """Spawn the child process."""
        env = os.environ.copy()
        self._proc = subprocess.Popen(
            [self.python_path, self.server_script],
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
            "child started: pid=%d cmd=[%s %s]",
            self._proc.pid, self.python_path, self.server_script,
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


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------

class Proxy:
    """
    Main proxy logic. Owns the stdio channel (sys.stdin.buffer / sys.stdout.buffer).
    Spawns ChildProcess, forwards messages, handles restarts with backoff.
    """

    def __init__(self, python_path: str, server_script: str, vault_root: str):
        self.python_path = python_path
        self.server_script = server_script
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

        # In-flight request tracking — IDs of requests forwarded to the child
        # that haven't been answered yet. Protected by _inflight_lock.
        self._inflight_ids: set[int | str] = set()
        self._inflight_lock = threading.Lock()

        # Synchronization
        self._child_ready = threading.Event()  # set when child is assigned
        self._last_version_check: float = 0.0  # monotonic timestamp for cooldown

        # Shutdown flag
        self._shutdown = False

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _start_child(self) -> bool:
        """
        Spawn child and perform initialize handshake.
        Returns True on success, False on failure (timeout or crash).
        """
        child = ChildProcess(self.python_path, self.server_script)
        child.start()
        self._child_start_time = time.monotonic()
        self._last_launched_version = _read_brain_version_from_disk(self.vault_root)

        # Check for proxy drift
        self._check_proxy_drift()

        # If we have a stored initialize request, replay it
        if self._init_request is not None:
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
                notification = {
                    "jsonrpc": "2.0",
                    "method": "notifications/tools/list_changed",
                }
                _write_line(sys.stdout.buffer, notification)
                _log().info("sent notifications/tools/list_changed to client")
            except Exception as e:
                _log().error("failed to send list_changed notification: %s", e)

        with self._child_lock:
            self._child = child
        self._child_ready.set()
        return True

    def _read_with_timeout(self, child: ChildProcess, timeout: int) -> dict | None:
        """
        Read one JSON line from child stdout within timeout seconds.
        Returns parsed dict or None on timeout/error.
        """
        result: list[dict | None] = [None]
        error: list[Exception | None] = [None]
        done = threading.Event()

        def _read():
            try:
                line = child.readline()
                if line:
                    result[0] = json.loads(line.decode("utf-8").strip())
            except Exception as e:
                error[0] = e
            finally:
                done.set()

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        if not done.wait(timeout=timeout):
            _log().warning("child initialize timed out after %ds", timeout)
            return None
        if error[0]:
            _log().error("error reading child initialize response: %s", error[0])
            return None
        return result[0]

    def _check_proxy_drift(self) -> None:
        """Read proxy version from disk and set _proxy_drift if different."""
        on_disk = _read_proxy_version_from_disk(self.proxy_script)
        if on_disk is None:
            return
        if on_disk != PROXY_VERSION:
            if not self._proxy_drift:
                _log().warning(
                    "proxy drift detected: running=%s disk=%s", PROXY_VERSION, on_disk
                )
            self._proxy_drift = True
            self._proxy_version_on_disk = on_disk
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

    def _handle_child_exit(self, exit_code: int) -> None:
        """
        Called when child exits. Decides whether to restart, apply backoff, or give up.
        Blocks during backoff delay.
        """
        _log().info("child exited with code %d", exit_code)

        if exit_code == _EXIT_CODE_CLEAN:
            _log().info("clean child exit — proxy shutting down")
            self._shutdown = True
            return

        # Reset backoff if child was healthy long enough
        if self._should_reset_backoff():
            _log().info("child was healthy for >%ds — resetting backoff", _CHILD_ALIVE_RESET_SECS)
            self._backoff_slot = 0
            self._gave_up = False

        if exit_code == _EXIT_CODE_VERSION_DRIFT:
            _log().info("exit code %d: version drift — restarting immediately", exit_code)
            # Restart immediately (no backoff delay for planned restarts)
            success = self._start_child()
            if success:
                return
            # First attempt failed — fall through to backoff retry loop so the
            # proxy keeps trying instead of entering limbo (child=None, not
            # gave_up, no one retrying). Reset backoff since this is a fresh
            # failure sequence.
            _log().warning("version-drift restart failed — falling through to backoff")
            self._backoff_slot = 0
            self._gave_up = False

        # Crash or failed drift restart — apply backoff
        if self._backoff_slot >= len(self._backoff_schedule):
            _log().error("backoff exhausted after %d attempts — giving up", self._backoff_slot)
            self._gave_up = True
            return

        delay = self._backoff_schedule[self._backoff_slot]
        _log().warning(
            "child restart — backoff slot %d/%d, waiting %ds",
            self._backoff_slot, len(self._backoff_schedule) - 1, delay,
        )
        if delay > 0:
            time.sleep(delay)
        self._backoff_slot += 1
        success = self._start_child()
        if not success:
            if self._backoff_slot >= len(self._backoff_schedule):
                _log().error("giving up after failed restart")
                self._gave_up = True

    def _advance_backoff(self) -> None:
        self._backoff_slot += 1
        if self._backoff_slot >= len(self._backoff_schedule):
            _log().error("giving up after failed restart")
            self._gave_up = True

    def _try_version_reset(self) -> bool:
        """
        After give-up, check if brain-core VERSION changed. If so, reset backoff
        and attempt restart. Returns True if restart succeeded.
        Rate-limited to one disk read per 5 seconds.
        """
        now = time.monotonic()
        if now - self._last_version_check < 5.0:
            return False
        self._last_version_check = now

        current_version = _read_brain_version_from_disk(self.vault_root)
        if current_version != self._last_launched_version:
            _log().info(
                "brain-core VERSION changed (%s → %s) — resetting backoff",
                self._last_launched_version, current_version,
            )
            self._gave_up = False
            self._backoff_slot = 0
            success = self._start_child()
            if success:
                return True
            self._advance_backoff()
        return False

    # ------------------------------------------------------------------
    # Reader thread (child stdout → proxy stdout)
    # ------------------------------------------------------------------

    def _drain_inflight(self) -> None:
        """Send error responses for all in-flight requests (child died mid-request)."""
        with self._inflight_lock:
            orphans = list(self._inflight_ids)
            self._inflight_ids.clear()
        for orphan_id in orphans:
            _log().warning("orphaned in-flight request id=%s — sending error to client", orphan_id)
            try:
                _write_line(
                    sys.stdout.buffer,
                    _make_error_response(orphan_id, -32603, "server exited mid-request, restarting"),
                )
            except Exception as e:
                _log().error("failed to send orphan error for id=%s: %s", orphan_id, e)

    def _reader_thread(self) -> None:
        """
        Continuously reads from child stdout and writes to proxy stdout.
        Handles child exit and triggers restart logic.
        """
        while not self._shutdown:
            child = self._get_child()
            if child is None:
                self._child_ready.wait(timeout=1.0)
                self._child_ready.clear()
                continue

            line = child.readline()
            if line is None:
                # Child stdout closed — wait for exit code
                exit_code = child.wait()
                _log().info("child stdout EOF, exit code=%s", exit_code)
                with self._child_lock:
                    self._child = None
                self._child_ready.clear()

                # Send error responses for any requests the child never answered
                self._drain_inflight()

                if exit_code is None:
                    exit_code = 1  # treat as crash

                self._handle_child_exit(exit_code)
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
                    self._inflight_ids.discard(msg_id)

            # Capture initialize response (first time only)
            if (
                self._init_response is None
                and self._init_request is not None
                and msg_id == self._init_request.get("id")
                and "result" in obj
            ):
                self._init_response = obj
                _log().info("captured initialize response from child")

            # Inject proxy drift note if applicable
            if self._proxy_drift and self._proxy_version_on_disk:
                obj = _inject_proxy_drift_note(obj, PROXY_VERSION, self._proxy_version_on_disk)

            try:
                _write_line(sys.stdout.buffer, obj)
            except BrokenPipeError:
                _log().warning("client disconnected (broken pipe on stdout)")
                self._shutdown = True
                return
            except Exception as e:
                _log().error("error writing to client stdout: %s", e)

    def _get_child(self) -> ChildProcess | None:
        with self._child_lock:
            return self._child

    # ------------------------------------------------------------------
    # Main loop (proxy stdin → child stdin)
    # ------------------------------------------------------------------

    def _error_response_for_dead_child(self, msg_id: int | str | None) -> dict:
        if self._gave_up:
            message = (
                f"server failed to start after {len(self._backoff_schedule)} attempts. "
                "Restart MCP via /mcp."
            )
        else:
            message = "server restarting, please retry"
        return _make_error_response(msg_id, -32603, message)

    def run(self) -> None:
        """Main proxy loop. Reads from stdin, forwards to child."""
        # Start reader thread
        reader = threading.Thread(
            target=self._reader_thread, daemon=True, name="child-reader"
        )
        reader.start()

        stdin = sys.stdin.buffer

        while not self._shutdown:
            try:
                line = stdin.readline()
            except Exception as e:
                _log().error("error reading from stdin: %s", e)
                break

            if not line:
                _log().info("stdin EOF — proxy shutting down")
                self._shutdown = True
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

            # Capture initialize request
            if method == "initialize" and self._init_request is None:
                self._init_request = obj
                _log().info("captured initialize request from client")

            child = self._get_child()

            # If child is dead
            if child is None or child.poll() is not None:
                with self._child_lock:
                    self._child = None

                # For tool calls after give-up, check if VERSION changed
                if self._gave_up and method == "tools/call":
                    self._try_version_reset()
                    child = self._get_child()

                if child is None or (child.poll() is not None):
                    # Still dead — return error for requests, drop notifications
                    if is_request:
                        error_resp = self._error_response_for_dead_child(msg_id)
                        try:
                            _write_line(sys.stdout.buffer, error_resp)
                        except Exception as e:
                            _log().error("error writing error response: %s", e)
                    elif is_notification:
                        _log().debug("dropping notification (child dead): method=%s", method)
                    continue

            # Forward to child
            if is_request:
                with self._inflight_lock:
                    self._inflight_ids.add(msg_id)
            try:
                child.send(obj)
            except BrokenPipeError:
                _log().warning("child stdin broken pipe — child likely died")
                with self._child_lock:
                    if self._child is child:
                        self._child = None
                if is_request:
                    # Remove from in-flight tracking before sending error.
                    # If _drain_inflight() already claimed this ID, skip the
                    # error to avoid sending a duplicate response to the client.
                    with self._inflight_lock:
                        was_tracked = msg_id in self._inflight_ids
                        self._inflight_ids.discard(msg_id)
                    if was_tracked:
                        try:
                            _write_line(
                                sys.stdout.buffer,
                                self._error_response_for_dead_child(msg_id),
                            )
                        except Exception:
                            pass
            except Exception as e:
                _log().error("error sending to child: %s", e)

        # Shutdown — kill child if still running
        child = self._get_child()
        if child:
            _log().info("proxy shutting down — killing child pid=%s", child.pid)
            child.kill()

        _log().info("proxy exited")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) != 3:
        print(
            f"Usage: {sys.argv[0]} <python_path> <server_script>",
            file=sys.stderr,
        )
        sys.exit(1)

    python_path = sys.argv[1]
    server_script = sys.argv[2]

    vault_root = os.environ.get("BRAIN_VAULT_ROOT", "")
    if not vault_root:
        # Fall back to cwd if vault root not set
        vault_root = os.getcwd()

    global _logger
    _logger = _setup_logging(vault_root)
    _log().info(
        "proxy starting: version=%s python=%s server=%s vault=%s",
        PROXY_VERSION, python_path, server_script, vault_root,
    )

    proxy = Proxy(python_path, server_script, vault_root)

    # Start the child for the first time
    _log().info("spawning initial child process")
    success = proxy._start_child()
    if not success:
        _log().error("initial child start failed — applying backoff")
        # backoff_slot starts at 0; attempt was just made with slot 0 delay (0s)
        proxy._backoff_slot = 1
        # Reader thread will handle restarts once running
        # But we need reader running to notice exits — start it first

    proxy.run()


if __name__ == "__main__":
    main()
