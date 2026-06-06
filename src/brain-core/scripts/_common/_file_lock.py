"""Cross-platform file locking helpers."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import sys
from typing import Iterator


@contextlib.contextmanager
def exclusive_file_lock(lock_path: str | Path) -> Iterator[None]:
    """Hold an exclusive file lock on ``lock_path`` for this process.

    POSIX ``flock`` blocks until the lock is available. Windows byte-range
    locking fails after the OS timeout. Both cases fail loudly with the lock
    path when acquisition cannot complete.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        # ``a+b`` preserves an existing lock byte; msvcrt.locking cannot lock
        # an empty byte range, so initialise one byte on first use.
        with path.open("a+b") as lock:
            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            import msvcrt

            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            except OSError as exc:
                raise RuntimeError(f"could not acquire exclusive lock on {path}") from exc
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    with path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise RuntimeError(f"could not acquire exclusive lock on {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
