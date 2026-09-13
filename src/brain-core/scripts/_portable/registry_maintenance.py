"""Portable linked-workspace registry repair semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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
    """Raised when malformed state was preserved but restoration failed."""

    def __init__(self, backup_path: str, cause: BaseException):
        self.backup_path = backup_path
        super().__init__(
            "Registry repair failed after preserving the malformed registry; "
            f"the preserved copy remains at {backup_path}: {cause}"
        )


def _backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    candidate = path.with_name(f"{path.name}.{stamp}.bak")
    suffix = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.{stamp}-{suffix}.bak")
        suffix += 1
    return candidate


def repair_registry(
    vault_root: str | Path,
    *,
    dry_run: bool,
    before_write=None,
) -> RegistryMaintenanceResult:
    """Normalise the selected Brain's linked-workspace registry."""
    root = Path(vault_root)
    state = inspect_registry(root)
    workspaces = state["canonical"]["workspaces"]
    entry_count = len(workspaces)
    if state["healthy"]:
        return RegistryMaintenanceResult(
            "noop",
            state["message"],
            dry_run,
            entry_count,
        )
    if dry_run:
        return RegistryMaintenanceResult(
            "planned",
            state["message"],
            True,
            entry_count,
        )

    path = state["path"]
    if before_write is not None:
        before_write()
    if state.get("backup_required") and path.is_file():
        backup = _backup_path(path)
        backup_relative = backup.relative_to(root).as_posix()
        path.rename(backup)
        try:
            workspace_registry.save_registry(root, workspaces)
        except (OSError, ValueError) as exc:
            try:
                backup.rename(path)
            except OSError as restore_exc:
                raise RegistryRepairPartialError(
                    backup_relative,
                    restore_exc,
                ) from exc
            raise
        return RegistryMaintenanceResult(
            "changed",
            state["message"],
            False,
            entry_count,
            backup_relative,
        )

    workspace_registry.save_registry(root, workspaces)
    return RegistryMaintenanceResult(
        "changed",
        state["message"],
        False,
        entry_count,
    )
