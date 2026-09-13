"""Cross-platform file locking helpers."""

from __future__ import annotations

import contextlib
import errno
import os
from pathlib import Path
import stat
import sys
import time
from typing import Iterator


class MutationLockError(RuntimeError):
    """A vault mutation lock could not be acquired safely."""


def mutation_lock_error_message(exc: MutationLockError) -> str:
    """Return the consistent actionable message for public mutation surfaces."""
    return f"Vault is busy; retry the mutation. {exc}"


def public_mutation_error_message(exc: BaseException) -> str:
    """Format an expected public mutation failure, including lock guidance."""
    if isinstance(exc, MutationLockError):
        return mutation_lock_error_message(exc)
    return str(exc)


@contextlib.contextmanager
def exclusive_file_lock(
    lock_path: str | Path,
    *,
    timeout: float | None = None,
    follow_symlinks: bool = True,
    create_parent: bool = True,
) -> Iterator[None]:
    """Hold an exclusive file lock on ``lock_path`` for this process.

    Generic locks preserve the historical blocking behaviour. Callers that
    need a bounded wait pass ``timeout`` explicitly; acquisition failures then
    raise ``MutationLockError`` with the lock path and holder details where
    available. Set create_parent=False for owner-lifetime directories that
    must never be recreated after their owner removes them.
    """
    path = Path(lock_path)
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if not follow_symlinks:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError(f"lock endpoint is not a regular file: {path}")
        if not follow_symlinks:
            endpoint = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISREG(endpoint.st_mode)
                or (endpoint.st_dev, endpoint.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise OSError(f"refusing symlinked lock endpoint: {path}")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        lock = os.fdopen(descriptor, "r+b")
        descriptor = -1
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise

    with lock:
        if sys.platform == "win32":
            # Preserve an existing lock byte; msvcrt.locking cannot lock an
            # empty byte range, so initialise one byte on first use.
            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            import msvcrt

            nonblocking_mode = getattr(msvcrt, "LK_NBLCK", None)
            if timeout is None or nonblocking_mode is None:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                except OSError as exc:
                    raise MutationLockError(
                        f"could not acquire exclusive lock on {path}"
                    ) from exc
            else:
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        msvcrt.locking(lock.fileno(), nonblocking_mode, 1)
                        break
                    except OSError as exc:
                        if exc.errno not in (errno.EACCES, errno.EDEADLK):
                            raise MutationLockError(
                                f"could not acquire exclusive lock on {path}: {exc}"
                            ) from exc
                        if time.monotonic() >= deadline:
                            raise MutationLockError(
                                f"timed out after {timeout:g}s acquiring exclusive lock on {path}"
                            )
                        time.sleep(0.05)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                mode = (
                    fcntl.LOCK_EX
                    if deadline is None
                    else fcntl.LOCK_EX | fcntl.LOCK_NB
                )
                fcntl.flock(lock.fileno(), mode)
                break
            except BlockingIOError as exc:
                if deadline is not None and time.monotonic() >= deadline:
                    lock.seek(0)
                    owner = (
                        lock.read().decode("utf-8", "replace").strip()
                        or "unknown owner"
                    )
                    raise MutationLockError(
                        f"timed out after {timeout:g}s acquiring exclusive lock on {path} "
                        f"({owner})"
                    ) from exc
                time.sleep(0.05)
            except OSError as exc:
                raise MutationLockError(
                    f"could not acquire exclusive lock on {path}"
                ) from exc
        try:
            lock.seek(0)
            lock.truncate()
            lock.write(f"pid={os.getpid()} acquired={time.time():.3f}\n".encode())
            lock.flush()
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def vault_mutation_lock(
    vault_root: str | Path, *, timeout: float = 30.0
) -> contextlib.AbstractContextManager[None]:
    """Return the shared cross-process mutation lock for one vault."""
    return exclusive_file_lock(
        Path(vault_root) / ".brain" / "local" / "mutation.lock",
        timeout=timeout,
    )
