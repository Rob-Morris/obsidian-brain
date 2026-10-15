"""Dependency-free workspace policy values shared across runtime tiers."""

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    parent: str | None = None
    tags: tuple[str, ...] = ()


class WorkspaceMatch(str, Enum):
    """Discovery scope: primary membership alone or its union with associations."""

    RELEVANT = "relevant"
    PRIMARY = "primary"
