"""Dependency-free workspace policy values shared across runtime tiers."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    parent: str | None = None
    tags: tuple[str, ...] = ()
