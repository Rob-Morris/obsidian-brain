"""Non-blocking, coalescing session-mirror persistence for long-lived MCP."""

from __future__ import annotations

import atexit
from collections.abc import Mapping
import copy
import logging
from pathlib import Path
import queue
import threading


_SHUTDOWN = object()


class SessionMirrorWorker:
    """Publish the latest session model without blocking the MCP request path."""

    def __init__(self, vault_root: Path, *, drain_timeout: float = 2.0):
        self._vault_root = vault_root
        self._drain_timeout = drain_timeout
        self._queue: queue.Queue[object] = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._sweep_orphaned_tmpfiles()
        atexit.register(self.close)

    def publish(self, model: Mapping[str, object]) -> None:
        """Coalesce pending work to the latest immutable model and return."""

        with self._lock:
            if self._closed:
                return
            self._ensure_started_locked()
            request = copy.deepcopy(dict(model))
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(request)
            except queue.Full:
                # The worker claimed the previous item between the non-blocking
                # drain and enqueue. Its write converges safely; a later call
                # will publish the newest model.
                pass

    def close(self) -> None:
        """Bound shutdown latency even when durable storage is stalled."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            if thread is None or not thread.is_alive():
                return
            try:
                self._queue.put_nowait(_SHUTDOWN)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(_SHUTDOWN)
                except queue.Full:
                    return
        thread.join(timeout=self._drain_timeout)

    def _ensure_started_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="brain-session-mirror",
        )
        self._thread.start()

    def _run(self) -> None:
        import session

        while True:
            request = self._queue.get()
            try:
                if request is _SHUTDOWN:
                    return
                try:
                    session.persist_session_markdown(request, self._vault_root)
                except Exception:
                    logging.getLogger("brain.session-mirror").warning(
                        "session mirror refresh failed",
                        exc_info=True,
                    )
            finally:
                self._queue.task_done()

    def _sweep_orphaned_tmpfiles(self) -> None:
        brain = self._vault_root / ".brain"
        local = brain / "local"
        if brain.is_symlink() or local.is_symlink():
            return
        try:
            candidates = tuple(local.glob("session.md.*.tmp"))
        except OSError:
            return
        for candidate in candidates:
            try:
                candidate.unlink()
            except OSError:
                pass
