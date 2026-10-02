"""Portable linked-workspace registry repair semantics."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile

from _bootstrap.diagnostics import inspect_registry
import workspace_registry


@dataclass(frozen=True, slots=True)
class RegistryMaintenanceResult:
    status: str
    reason: str
    dry_run: bool
    entry_count: int
    backup_path: str | None = None


class RegistryRepairPartialError(RuntimeError):
    """Raised when the registry was rewritten but its backup could not be put in place."""

    def __init__(self, backup_path: str, cause: BaseException):
        self.backup_path = backup_path
        super().__init__(
            "Registry repair rewrote the registry but could not move the preserved copy "
            f"into place; it remains at {backup_path}: {cause}"
        )


def _backup_path(path: Path) -> Path:
    """One fixed-name backup beside the registry; a later repair replaces it."""
    return path.with_name(f"{path.name}.bak")


def repair_registry(
    vault_root: str | Path,
    *,
    dry_run: bool,
    before_write=None,
) -> RegistryMaintenanceResult:
    """Normalise the selected Brain's linked-workspace registry.

    An unreadable file is refused with no effect: only a file that was read and
    found malformed is backed up and rebuilt. The backup is staged beside the
    registry and replaces the previous one only after the rebuilt registry is
    saved, so a failed repair never costs the earlier backup.
    """
    root = Path(vault_root)
    state = inspect_registry(root)
    if state.get("readable") is False:
        raise OSError(state["message"])
    workspaces = state["canonical"]["workspaces"]
    entry_count = len(workspaces)
    if state["healthy"]:
        return RegistryMaintenanceResult("noop", state["message"], dry_run, entry_count)
    if dry_run:
        return RegistryMaintenanceResult("planned", state["message"], True, entry_count)

    path = state["path"]
    if before_write is not None:
        before_write()
    if not state.get("backup_required"):
        workspace_registry.save_registry(root, workspaces)
        return RegistryMaintenanceResult("changed", state["message"], False, entry_count)

    backup = _backup_path(path)
    descriptor, staged_name = tempfile.mkstemp(prefix=f".{backup.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(path.read_bytes())
        workspace_registry.save_registry(root, workspaces)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    try:
        staged.replace(backup)
    except OSError as exc:
        raise RegistryRepairPartialError(staged.relative_to(root).as_posix(), exc) from exc
    return RegistryMaintenanceResult(
        "changed", state["message"], False, entry_count, backup.relative_to(root).as_posix())
