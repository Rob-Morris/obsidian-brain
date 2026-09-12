"""Non-blocking, coalescing session-mirror persistence for long-lived MCP."""

from __future__ import annotations

import atexit
from collections.abc import Mapping
import copy
import logging
from pathlib import Path
import threading
import time


class SessionMirrorWorker:
    """Publish the latest session model without blocking the MCP request path."""

    def __init__(
        self,
        vault_root: Path,
        *,
        drain_timeout: float = 2.0,
        monotonic_clock=time.monotonic,
    ):
        self._vault_root = vault_root
        self._drain_timeout = drain_timeout
        self._monotonic_clock = monotonic_clock
        self._condition = threading.Condition()
        self._pending: tuple[int, dict[str, object]] | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._accepted_generation = 0
        self._persisted_generation = 0
        self._close_deadline: float | None = None
        self._sweep_orphaned_tmpfiles()
        atexit.register(self.close)

    def publish(self, model: Mapping[str, object]) -> None:
        """Coalesce pending work to the latest immutable model and return."""

        request = copy.deepcopy(dict(model))
        with self._condition:
            if self._closed:
                return
            self._ensure_started_locked()
            self._accepted_generation += 1
            self._pending = (self._accepted_generation, request)
            self._condition.notify()

    def close(self) -> bool:
        """Request a final drain and report whether it completed by the deadline."""

        with self._condition:
            if not self._closed:
                self._closed = True
                self._close_deadline = (
                    self._monotonic_clock() + max(0.0, self._drain_timeout)
                )
                self._condition.notify_all()
            thread = self._thread
            deadline = self._close_deadline
        if thread is None:
            return True
        assert deadline is not None
        remaining = max(0.0, deadline - self._monotonic_clock())
        if remaining:
            thread.join(timeout=remaining)
        with self._condition:
            return (
                not thread.is_alive()
                and self._persisted_generation >= self._accepted_generation
            )

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
            with self._condition:
                self._condition.wait_for(
                    lambda: self._pending is not None or self._closed
                )
                if self._pending is None:
                    return
                generation, request = self._pending
                self._pending = None
            try:
                session.persist_session_markdown(request, self._vault_root)
            except Exception:
                logging.getLogger("brain.session-mirror").warning(
                    "session mirror refresh failed",
                    exc_info=True,
                )
            else:
                with self._condition:
                    self._persisted_generation = max(
                        self._persisted_generation,
                        generation,
                    )
                    self._condition.notify_all()

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
