"""Typed machine Brain-registry read owners."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import ClassVar

from .context import LauncherContext
from .contracts import ErrorCode, Ok, no_effect_error


_BRAIN_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_brain_id(brain_id: object) -> None:
    if not isinstance(brain_id, str) or not _BRAIN_ID.fullmatch(brain_id):
        raise ValueError("brain_id must be a canonical lower-case slug")


@dataclass(frozen=True, slots=True)
class BrainRegistryEntry:
    brain_id: str
    kind: str
    value: str
    stale: bool | None
    is_default: bool
    status: str | None = None

    def __post_init__(self) -> None:
        _validate_brain_id(self.brain_id)
        if not self.kind.strip() or not self.value.strip():
            raise ValueError("launcher registry entries require kind and value")
        if self.stale is not None and not isinstance(self.stale, bool):
            raise ValueError("launcher registry stale state must be boolean or null")
        if not isinstance(self.is_default, bool):
            raise ValueError("launcher registry default state must be boolean")
        if self.status is not None and not self.status.strip():
            raise ValueError("launcher registry status must be non-empty")


@dataclass(frozen=True, slots=True)
class BrainGetDefaultPayload:
    brain_id: str | None

    def __post_init__(self) -> None:
        if self.brain_id is not None:
            _validate_brain_id(self.brain_id)


@dataclass(frozen=True, slots=True)
class BrainListPayload:
    entries: tuple[BrainRegistryEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple) or any(
            not isinstance(entry, BrainRegistryEntry) for entry in self.entries
        ):
            raise ValueError("brain.list entries must be typed registry entries")
        ids = [entry.brain_id for entry in self.entries]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("brain.list entries must be sorted and unique")


@dataclass(frozen=True, slots=True)
class BrainResolvePayload:
    brain_id: str
    vault_root: str

    def __post_init__(self) -> None:
        _validate_brain_id(self.brain_id)
        if not isinstance(self.vault_root, str) or not Path(self.vault_root).is_absolute():
            raise ValueError("brain.resolve vault_root must be absolute")


@dataclass(frozen=True, slots=True)
class BrainGetDefaultRequest:
    COMMAND_ID: ClassVar[str] = "brain.get-default"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BrainGetDefaultPayload


@dataclass(frozen=True, slots=True)
class BrainListRequest:
    COMMAND_ID: ClassVar[str] = "brain.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BrainListPayload


@dataclass(frozen=True, slots=True)
class BrainResolveRequest:
    COMMAND_ID: ClassVar[str] = "brain.resolve"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BrainResolvePayload

    brain_id: str

    def __post_init__(self) -> None:
        _validate_brain_id(self.brain_id)


def execute_get_default(_context: LauncherContext, request: BrainGetDefaultRequest):
    import vault_registry

    try:
        value = vault_registry.get_default()
    except (vault_registry.RegistryReadError, OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, BrainGetDefaultPayload(value))


def execute_list(_context: LauncherContext, request: BrainListRequest):
    import vault_registry

    try:
        raw_entries = vault_registry.list_entries()
    except (vault_registry.RegistryReadError, OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    entries = tuple(
        BrainRegistryEntry(
            item["alias"],
            item["kind"],
            item["value"],
            item.get("stale"),
            bool(item["default"]),
            item.get("status"),
        )
        for item in raw_entries
    )
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, BrainListPayload(entries))


def execute_resolve(_context: LauncherContext, request: BrainResolveRequest):
    import vault_registry

    try:
        vault_root = vault_registry.resolve(request.brain_id)
    except (vault_registry.RegistryReadError, OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    if vault_root is None:
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            f"No registered local Brain has ID {request.brain_id!r}.",
            "brain_id",
        )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        BrainResolvePayload(request.brain_id, vault_root),
    )


def get_default_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainGetDefaultRequest,
        BrainGetDefaultPayload,
        "_launcher.registry:get_default",
        execute_get_default,
    )


def list_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainListRequest,
        BrainListPayload,
        "_launcher.registry:list",
        execute_list,
    )


def resolve_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainResolveRequest,
        BrainResolvePayload,
        "_launcher.registry:resolve",
        execute_resolve,
    )
