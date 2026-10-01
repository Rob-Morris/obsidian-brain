"""The coarse maintenance advisory carried by bootstrap surfaces (D17)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from _bootstrap.maintenance_summary import brain_paths, read_advisory


@dataclass(frozen=True, slots=True)
class MaintenanceAdvisory:
    """Counts from the last maintenance pass; absent when no pass has run."""

    needs_person: int
    claim_expired: int
    failed: int
    deferred: int
    blocked: str | None
    finished_at: str
    age_seconds: int


def read_maintenance_advisory(vault_root: Path, now: datetime) -> MaintenanceAdvisory | None:
    value = read_advisory(brain_paths(vault_root).last_pass, now)
    return None if value is None else MaintenanceAdvisory(**value)
