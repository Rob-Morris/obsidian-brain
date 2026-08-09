"""Closed command-contract vocabularies shared by application modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
import re


_COMMAND_ID = re.compile(
    r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
)


def validate_command_id(command_id: str) -> None:
    if not _COMMAND_ID.fullmatch(command_id):
        raise ValueError(
            "command_id must be a canonical lower-case noun.verb identifier"
        )


class DependencyTier(IntEnum):
    """Ordered execution dependencies; higher values include lower tiers."""

    BOOTSTRAP = 0
    PORTABLE = 1
    MANAGED = 2

    def supports(self, required: "DependencyTier") -> bool:
        return self >= required


class Locality(str, Enum):
    SELECTED_BRAIN_LOCAL = "selected_brain_local"
    CALLER_LOCAL = "caller_local"
    MACHINE_LOCAL = "machine_local"


class Authority(str, Enum):
    READER = "reader"
    CONTRIBUTOR = "contributor"
    OPERATOR = "operator"


class EffectClass(str, Enum):
    NONE = "none"
    DERIVED_CACHE_WRITE = "derived_cache_write"
    SELECTED_BRAIN_MUTATION = "selected_brain_mutation"
    CALLER_LOCAL_MUTATION = "caller_local_mutation"
    MACHINE_MUTATION = "machine_mutation"


class RetryClass(str, Enum):
    SAFE = "safe"
    RECEIPT_REQUIRED = "receipt_required"


class Projection(str, Enum):
    MCP = "mcp"
    CLI = "cli"
    SCRIPT = "script"
    PYTHON = "python"
    LAUNCHER = "launcher"


@dataclass(frozen=True, slots=True)
class ProjectionEligibility:
    projection: Projection
    supported: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.supported and self.reason is not None:
            raise ValueError("supported projection cannot carry an exclusion reason")
        if not self.supported and (self.reason is None or not self.reason.strip()):
            raise ValueError("unsupported projection requires a reason")


class Availability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class SnapshotFreshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"
