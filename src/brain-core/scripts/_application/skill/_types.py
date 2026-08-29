"""Shared typed values for Brain skill lifecycle commands."""

from __future__ import annotations

from dataclasses import dataclass
from _skill_library.models import (
    SkillMutation,
    SkillState,
    SkillStatus,
    SkillSubstrate,
)


@dataclass(frozen=True, slots=True)
class SkillStatusItem:
    name: str
    substrate: SkillSubstrate
    effective: bool
    shadowed: bool
    state: SkillState
    package_sha256: str | None
    source_repository: str | None
    configured_ref: str | None
    resolved_commit: str | None
    available_commit: str | None
    core_lineage: str | None
    detail: str | None


@dataclass(frozen=True, slots=True)
class SkillStatusPayload:
    items: tuple[SkillStatusItem, ...]
    total: int
    refreshed: bool


@dataclass(frozen=True, slots=True)
class SkillMutationPayload:
    name: str
    action: str
    state: SkillState
    package_sha256: str | None
    resolved_commit: str | None
    changed_paths: tuple[str, ...]
    backup_path: str | None
    detail: str | None


def status_payload(
    rows: tuple[SkillStatus, ...], *, refreshed: bool
) -> SkillStatusPayload:
    return SkillStatusPayload(
        tuple(
            SkillStatusItem(
                item.name,
                SkillSubstrate(item.substrate.value),
                item.effective,
                item.shadowed,
                SkillState(item.state.value),
                item.package_sha256,
                item.source_repository,
                item.configured_ref,
                item.resolved_commit,
                item.available_commit,
                item.core_lineage,
                item.detail,
            )
            for item in rows
        ),
        len(rows),
        refreshed,
    )


def mutation_payload(value: SkillMutation) -> SkillMutationPayload:
    return SkillMutationPayload(
        value.name,
        value.action.value,
        SkillState(value.state.value),
        value.package_sha256,
        value.resolved_commit,
        value.changed_paths,
        value.backup_path,
        value.detail,
    )
