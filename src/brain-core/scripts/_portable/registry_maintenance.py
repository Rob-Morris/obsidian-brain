"""Portable linked-workspace registry repair semantics."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile

from _bootstrap.diagnostics import RegistryCondition, RegistryInspection, inspect_registry
import workspace_registry


@dataclass(frozen=True, slots=True)
class DroppedRow:
    """A row the repair removed, with the folder to run ``workspace setup`` from to link it again."""

    key: str
    path: str


@dataclass(frozen=True, slots=True)
class RegistryMaintenanceResult:
    status: str
    reason: str
    dry_run: bool
    entry_count: int
    backup_path: str | None = None
    dropped: tuple[DroppedRow, ...] = ()


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


def _admit(inspection: RegistryInspection, allow_row_loss: bool) -> None:
    """Refuse, with no effect, a file whose rows cannot be read unless the person accepted losing them."""
    if inspection.condition is RegistryCondition.UNREADABLE:
        raise OSError(inspection.message)
    if inspection.condition is RegistryCondition.UNPARSEABLE and not allow_row_loss:
        raise ValueError(inspection.message)


def _disagreeing(verification) -> list:
    """The verified rows whose manifest positively names another Brain or workspace."""
    from _bootstrap.workspace_binding import LINK_DISAGREEMENT

    return [row for row in verification.rows if row.classification.verdict in LINK_DISAGREEMENT]


def _still_disagrees(root: Path, rows: dict, planned) -> bool:
    """Under the vault lock: the row still records the same folder, and a fresh read of the
    byte-identical manifest still positively disagrees."""
    from _bootstrap.workspace_binding import LINK_DISAGREEMENT, classify_link

    if not workspace_registry.row_records(rows.get(planned.key), planned.path):
        return False
    fresh = classify_link(root, Path(planned.path), planned.key)
    return (fresh.verdict in LINK_DISAGREEMENT and fresh.snapshot is not None
            and planned.classification.snapshot is not None
            and fresh.snapshot.confirms(planned.classification.snapshot))


def repair_registry(
    vault_root: str | Path,
    *,
    dry_run: bool,
    allow_row_loss: bool = False,
    before_write=None,
) -> RegistryMaintenanceResult:
    """Re-derive the selected Brain's linked-workspace registry from its manifests.

    Never adds a row, and never loses one unattended:
    - A file that could not be read is refused with no effect. A file whose
      rows cannot be read (not UTF-8, not JSON, or the wrong shape) is refused
      unless ``allow_row_loss``, the person's explicit choice, rebuilds it empty.
    - A malformed file whose rows can be read is rebuilt without the rows that
      name no usable folder (``workspace_registry.salvage_row``).
    - A row is dropped only when the manifest in its folder positively names
      another Brain or workspace.

    Rows are classified outside the vault lock. Under it, the registry is read
    again and a row is dropped only if it still records the same folder and a
    second read of that folder's manifest is byte-identical to the one
    classified and still disagrees. ``workspace.setup`` writes the row and the
    manifest under the same vault lock, so it is never caught between them.
    No lock is taken in, and nothing is written to, any workspace folder.
    The write is a compare-and-swap over the bytes read under the lock, so a
    row another writer (MCP reverse registration) committed meanwhile is never
    overwritten: the repair raises ``RegistryChangedError`` with no effect and
    the next pass re-detects. A lossy rebuild is admitted under the lock only
    when the file was already unparseable when the caller read it.
    ``before_write`` runs under the lock immediately before the write. A
    malformed file is backed up first: the backup is staged beside the registry
    and replaces the previous one only after the save, so a failed repair never
    costs the earlier backup.
    """
    from _common import vault_mutation_lock

    root = Path(vault_root)
    inspection = inspect_registry(root)
    _admit(inspection, allow_row_loss)
    verification = workspace_registry.verify_rows(root, inspection.rows)
    planned = _disagreeing(verification)
    if inspection.healthy and not planned:
        reason = verification.reason.describe() if verification.reason else "Every row agrees with its manifest."
        return RegistryMaintenanceResult("noop", reason, dry_run, len(inspection.rows))
    if dry_run:
        dropped = tuple(DroppedRow(row.key, row.path) for row in planned)
        return RegistryMaintenanceResult("planned", _reason(inspection), True,
                                         len(inspection.rows) - len(dropped), dropped=dropped)

    with vault_mutation_lock(root):
        current = inspect_registry(root)
        # A lossy rebuild is admitted only for the unparseable file the caller acted on, never for one
        # that tore after it was read healthy.
        if current.condition is RegistryCondition.UNPARSEABLE and inspection.condition is not RegistryCondition.UNPARSEABLE:
            raise workspace_registry.RegistryChangedError(
                "The linked workspace registry changed while the repair ran and its rows can no longer be read; "
                "nothing was written. Run the repair again to see its current state.")
        _admit(current, allow_row_loss)
        dropped = tuple(DroppedRow(row.key, row.path) for row in planned
                        if _still_disagrees(root, current.rows, row))
        if current.healthy and not dropped:
            return RegistryMaintenanceResult(
                "noop", "No row still disagrees with its manifest, so none was removed.", False, len(current.rows))
        if before_write is not None:
            before_write()
        kept = {key: entry for key, entry in current.rows.items() if key not in {row.key for row in dropped}}
        if not current.backup_required:
            workspace_registry.replace_registry(root, kept, expected=current.content)
            return RegistryMaintenanceResult("changed", _reason(current), False, len(kept), dropped=dropped)
        backup = _stage_and_save(root, current, kept).relative_to(root).as_posix()
    reason = (f"Rebuilt the unparseable registry empty and kept its previous content as {backup}; run brain "
              "workspace setup from each linked folder to link it again."
              if current.condition is RegistryCondition.UNPARSEABLE else _reason(current))
    return RegistryMaintenanceResult("changed", reason, False, len(kept), backup, dropped)


def _reason(inspection: RegistryInspection) -> str:
    """Why the registry changes: the file's own problem first, else the disagreeing rows."""
    if not inspection.healthy:
        return inspection.message
    return "Rows disagree with their manifests; `dropped` names each one and the folder it recorded."


def _stage_and_save(root: Path, inspection: RegistryInspection, kept: dict) -> Path:
    """Save ``kept`` over the exact bytes inspected, and put those bytes in place as the one backup."""
    path = inspection.path
    backup = _backup_path(path)
    descriptor, staged_name = tempfile.mkstemp(prefix=f".{backup.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(inspection.content)
        workspace_registry.replace_registry(root, kept, expected=inspection.content)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    try:
        staged.replace(backup)
    except OSError as exc:
        raise RegistryRepairPartialError(staged.relative_to(root).as_posix(), exc) from exc
    return backup
