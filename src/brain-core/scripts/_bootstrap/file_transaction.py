"""Deterministic, rollback-capable bootstrap file transactions."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import tempfile


@dataclass(frozen=True, slots=True)
class FileChange:
    path: Path
    before: bytes | None
    after: bytes | None


class FileTransactionError(RuntimeError):
    """A transaction could not commit, with any surviving effects identified."""

    def __init__(self, message: str, surviving_paths: tuple[Path, ...] = ()) -> None:
        super().__init__(message)
        self.surviving_paths = surviving_paths


class FilePlan:
    """Build a complete fixed-file mutation before the first write."""

    def __init__(self) -> None:
        self._before: dict[Path, bytes | None] = {}
        self._after: dict[Path, bytes | None] = {}

    def read_bytes(self, path: Path) -> bytes | None:
        path = _normalise(path)
        if path in self._after:
            return self._after[path]
        if path not in self._before:
            try:
                _refuse_symlink_path(path)
                self._before[path] = path.read_bytes()
            except FileNotFoundError:
                self._before[path] = None
            except IsADirectoryError as exc:
                raise ValueError(f"Expected an MCP state file, found a directory: {path}") from exc
        return self._before[path]

    def read_text(self, path: Path) -> str | None:
        content = self.read_bytes(path)
        if content is None:
            return None
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"MCP state is not UTF-8 text: {path}") from exc

    def write_text(self, path: Path, content: str) -> None:
        if not isinstance(content, str):
            raise TypeError("file transaction content must be text")
        path = _normalise(path)
        self.read_bytes(path)
        self._after[path] = content.encode("utf-8")

    def delete(self, path: Path) -> None:
        path = _normalise(path)
        self.read_bytes(path)
        self._after[path] = None

    def changes(self) -> tuple[FileChange, ...]:
        return tuple(
            FileChange(path, self._before[path], after)
            for path, after in sorted(self._after.items(), key=lambda item: str(item[0]))
            if self._before[path] != after
        )


def _write_bytes(path: Path, content: bytes) -> None:
    _refuse_symlink_path(path)
    existing_mode = None
    try:
        existing_mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    _refuse_symlink_path(path)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        if existing_mode is not None and hasattr(os, "fchmod"):
            os.fchmod(fd, existing_mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _apply(path: Path, content: bytes | None) -> None:
    _refuse_symlink_path(path)
    if content is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    _write_bytes(path, content)


def _current(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _normalise(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _refuse_symlink_path(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError(f"Refusing to mutate symlinked MCP state: {candidate}")


def _missing_parent_dirs(path: Path) -> tuple[Path, ...]:
    missing: list[Path] = []
    current = path.parent
    while not current.exists():
        missing.append(current)
        current = current.parent
    return tuple(missing)


def apply_file_changes(changes: tuple[FileChange, ...]) -> None:
    """Apply all changes or restore every known original file state."""
    for change in changes:
        try:
            _refuse_symlink_path(change.path)
            current = _current(change.path)
        except OSError as exc:
            raise FileTransactionError(
                f"Could not verify MCP state after preflight: {change.path}: {exc}"
            ) from exc
        if current != change.before:
            raise FileTransactionError(
                f"MCP state changed after preflight: {change.path}"
            )

    applied: list[FileChange] = []
    created_dirs = tuple(
        sorted(
            {directory for change in changes for directory in _missing_parent_dirs(change.path)},
            key=lambda path: len(path.parts),
            reverse=True,
        )
    )
    try:
        for change in changes:
            _apply(change.path, change.after)
            applied.append(change)
    except Exception as exc:
        rollback_errors: list[str] = []
        for change in reversed(applied):
            try:
                _apply(change.path, change.before)
            except Exception as rollback_exc:
                rollback_errors.append(f"{change.path}: {rollback_exc}")

        for directory in created_dirs:
            try:
                directory.rmdir()
            except FileNotFoundError:
                pass
            except OSError as rollback_exc:
                rollback_errors.append(f"{directory}: {rollback_exc}")

        surviving: list[Path] = []
        for change in applied:
            try:
                if _current(change.path) != change.before:
                    surviving.append(change.path)
            except OSError:
                surviving.append(change.path)
        surviving.extend(directory for directory in created_dirs if directory.exists())
        suffix = (
            " Rollback also failed: " + "; ".join(rollback_errors)
            if rollback_errors
            else " All written files were restored."
        )
        raise FileTransactionError(
            f"MCP file transaction failed: {exc}.{suffix}",
            tuple(sorted(set(surviving), key=str)),
        ) from exc
