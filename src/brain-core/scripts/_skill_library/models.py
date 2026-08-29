"""Immutable values used by the skill-library domain."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SkillSubstrate(str, Enum):
    """Physical ownership substrate for an installed Brain skill."""
    CORE = "core"
    USER = "user"


class SkillState(str, Enum):
    """Observed lifecycle state of one core or user skill entry."""
    CORE = "core"
    USER_OWNED = "user_owned"
    IN_SYNC = "in_sync"
    UPDATE_READY = "update_ready"
    LOCALLY_CUSTOMISED = "locally_customised"
    CONFLICT = "conflict"
    SOURCE_MISSING = "source_missing"


class SkillMutationAction(str, Enum):
    """Closed result vocabulary for user-skill lifecycle mutations."""
    DETACHED = "detached"
    MATERIALISED_FOR_EDIT = "materialised_for_edit"
    INSTALLED = "installed"
    REBASELINED = "rebaselined"
    CONFLICT_STAGED = "conflict_staged"
    REPLACED = "replaced"
    UPDATED = "updated"


class CoreReconciliationAction(str, Enum):
    """Closed result vocabulary for core-upgrade reconciliation."""
    COLLAPSED_TO_CORE = "collapsed_to_core"


@dataclass(frozen=True, slots=True)
class PackageFile:
    """Identity-bearing metadata for one regular package file."""
    path: str
    sha256: str
    size: int
    executable: bool


@dataclass(frozen=True, slots=True)
class PackageSnapshot:
    """Validated identity and source root for a complete skill package."""
    name: str
    root: Path
    package_sha256: str
    files: tuple[PackageFile, ...]

    @property
    def executable_files(self) -> tuple[str, ...]:
        """Return package-relative paths carrying an executable mode bit."""
        return tuple(item.path for item in self.files if item.executable)


@dataclass(frozen=True, slots=True)
class SourceCheckout:
    """Validated package acquired from one resolved Git source revision."""
    repository: str
    configured_ref: str
    resolved_commit: str
    skill_path: str
    package: PackageSnapshot


@dataclass(frozen=True, slots=True)
class SkillStatus:
    """Status projection for one substrate-specific skill entry."""
    name: str
    substrate: SkillSubstrate
    effective: bool
    shadowed: bool
    state: SkillState
    package_sha256: str | None
    source_repository: str | None = None
    configured_ref: str | None = None
    resolved_commit: str | None = None
    available_commit: str | None = None
    core_lineage: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class SkillMutation:
    """Committed result of a user-skill lifecycle operation."""
    name: str
    action: SkillMutationAction
    state: SkillState
    package_sha256: str | None
    resolved_commit: str | None
    changed_paths: tuple[str, ...]
    backup_path: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CoreReconciliation:
    """Auditable result of collapsing one redundant core-derived override."""
    name: str
    action: CoreReconciliationAction
    archived_path: str
    detail: str | None = None
