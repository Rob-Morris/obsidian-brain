"""Typed machine Brain-registry command owners."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import ClassVar

from .context import LauncherContext
from .contracts import (
    CommandError,
    CommittedEffect,
    ErrorCode,
    Ok,
    Partial,
    RequestErrorDetails,
    no_effect_error,
)


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


class RegistryMutationStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


def _validate_mutation_status(status: object) -> None:
    if not isinstance(status, RegistryMutationStatus):
        raise ValueError("registry mutation status must be closed and typed")


def _validate_brain_ids(brain_ids: object) -> None:
    if not isinstance(brain_ids, tuple):
        raise ValueError("registry mutation brain_ids must be a tuple")
    for brain_id in brain_ids:
        _validate_brain_id(brain_id)
    if brain_ids != tuple(sorted(set(brain_ids))):
        raise ValueError("registry mutation brain_ids must be sorted and unique")


@dataclass(frozen=True, slots=True)
class BrainRegisterPayload:
    status: RegistryMutationStatus
    brain_id: str

    def __post_init__(self) -> None:
        _validate_mutation_status(self.status)
        _validate_brain_id(self.brain_id)


@dataclass(frozen=True, slots=True)
class BrainUnregisterPayload:
    status: RegistryMutationStatus
    removed_brain_ids: tuple[str, ...]
    default_pointer_affected: bool

    def __post_init__(self) -> None:
        _validate_mutation_status(self.status)
        _validate_brain_ids(self.removed_brain_ids)
        if not isinstance(self.default_pointer_affected, bool):
            raise ValueError("default pointer affected state must be boolean")


@dataclass(frozen=True, slots=True)
class BrainSetDefaultPayload:
    status: RegistryMutationStatus
    brain_id: str

    def __post_init__(self) -> None:
        _validate_mutation_status(self.status)
        _validate_brain_id(self.brain_id)


@dataclass(frozen=True, slots=True)
class BrainClearDefaultPayload:
    status: RegistryMutationStatus
    brain_id: str | None

    def __post_init__(self) -> None:
        _validate_mutation_status(self.status)
        if self.brain_id is not None:
            _validate_brain_id(self.brain_id)


@dataclass(frozen=True, slots=True)
class RegistryRemoveStalePayload:
    status: RegistryMutationStatus
    removed_brain_ids: tuple[str, ...]
    default_pointer_affected: bool

    def __post_init__(self) -> None:
        _validate_mutation_status(self.status)
        _validate_brain_ids(self.removed_brain_ids)
        if not isinstance(self.default_pointer_affected, bool):
            raise ValueError("default pointer affected state must be boolean")


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


@dataclass(frozen=True, slots=True)
class BrainRegisterRequest:
    COMMAND_ID: ClassVar[str] = "brain.register"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BrainRegisterPayload

    vault_root: Path
    brain_id: str | None = None

    def __post_init__(self) -> None:
        _validate_absolute_path(self.vault_root)
        if self.brain_id is not None:
            _validate_brain_id(self.brain_id)


@dataclass(frozen=True, slots=True)
class BrainUnregisterRequest:
    COMMAND_ID: ClassVar[str] = "brain.unregister"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BrainUnregisterPayload

    vault_root: Path

    def __post_init__(self) -> None:
        _validate_absolute_path(self.vault_root)


@dataclass(frozen=True, slots=True)
class BrainSetDefaultRequest:
    COMMAND_ID: ClassVar[str] = "brain.set-default"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BrainSetDefaultPayload

    brain_id: str

    def __post_init__(self) -> None:
        _validate_brain_id(self.brain_id)


@dataclass(frozen=True, slots=True)
class BrainClearDefaultRequest:
    COMMAND_ID: ClassVar[str] = "brain.clear-default"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BrainClearDefaultPayload


@dataclass(frozen=True, slots=True)
class RegistryRemoveStaleRequest:
    COMMAND_ID: ClassVar[str] = "registry.remove-stale"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RegistryRemoveStalePayload


def _validate_absolute_path(value: object) -> None:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError("vault_root must be an absolute Path")


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


def _mutation_status(context: LauncherContext, changed: bool):
    if not changed:
        return RegistryMutationStatus.NOOP
    if context.dry_run:
        return RegistryMutationStatus.PLANNED
    return RegistryMutationStatus.CHANGED


def _committed_effects(
    request,
    *,
    brain_ids: tuple[str, ...] = (),
    registry_affected: bool = False,
    default_pointer_affected: bool = False,
):
    effects = tuple(
        CommittedEffect(request.COMMAND_ID, f"machine-registry:{brain_id}")
        for brain_id in brain_ids
        if registry_affected
    )
    if default_pointer_affected:
        effects += (CommittedEffect(request.COMMAND_ID, "machine-default"),)
    return effects


def _partial_registry(request, exc):
    effects = tuple(
        CommittedEffect(request.COMMAND_ID, f"machine-registry:{brain_id}")
        for brain_id in exc.committed_brain_ids
    )
    message = str(exc)
    return Partial(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        CommandError(
            ErrorCode.CONFLICT,
            message,
            RequestErrorDetails(None, message),
        ),
        effects,
    )


def _registry_error(request, exc):
    return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))


def execute_register(context: LauncherContext, request: BrainRegisterRequest):
    import vault_registry

    if not vault_registry.is_vault_root(request.vault_root):
        return no_effect_error(
            type(request),
            ErrorCode.INVALID_REQUEST,
            "vault_root must identify an installed local Brain.",
            "vault_root",
        )
    try:
        result = vault_registry.register_action(
            request.vault_root,
            brain_id=request.brain_id,
            dry_run=context.dry_run,
        )
    except (
        vault_registry.RegistryReadError,
        vault_registry.RegistryConflictError,
        ValueError,
    ) as exc:
        return _registry_error(request, exc)
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        BrainRegisterPayload(_mutation_status(context, result.changed), result.brain_id),
        _committed_effects(
            request,
            brain_ids=(result.brain_id,),
            registry_affected=result.changed and not context.dry_run,
        ),
    )


def execute_unregister(context: LauncherContext, request: BrainUnregisterRequest, *, registration_plan=None):
    import vault_registry

    try:
        result = vault_registry.unregister_action(
            request.vault_root,
            dry_run=context.dry_run,
            **({"registration_plan": registration_plan} if registration_plan is not None else {}),
        )
    except vault_registry.RegistryPartialApplyError as exc:
        return _partial_registry(request, exc)
    except (vault_registry.RegistryReadError, ValueError) as exc:
        return _registry_error(request, exc)
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        BrainUnregisterPayload(
            _mutation_status(context, result.changed),
            result.removed_brain_ids,
            result.default_cleared,
        ),
        _committed_effects(
            request,
            brain_ids=result.removed_brain_ids,
            registry_affected=result.changed and not context.dry_run,
            default_pointer_affected=result.default_cleared and not context.dry_run,
        ),
    )


def execute_set_default(context: LauncherContext, request: BrainSetDefaultRequest):
    import vault_registry

    try:
        result = vault_registry.set_default_action(
            request.brain_id,
            dry_run=context.dry_run,
        )
    except (
        vault_registry.RegistryReadError,
        vault_registry.RegistryConflictError,
        ValueError,
    ) as exc:
        return _registry_error(request, exc)
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        BrainSetDefaultPayload(_mutation_status(context, result.changed), result.brain_id),
        _committed_effects(
            request,
            default_pointer_affected=result.changed and not context.dry_run,
        ),
    )


def execute_clear_default(context: LauncherContext, request: BrainClearDefaultRequest):
    import vault_registry

    try:
        result = vault_registry.clear_default_action(dry_run=context.dry_run)
    except (vault_registry.RegistryReadError, ValueError) as exc:
        return _registry_error(request, exc)
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        BrainClearDefaultPayload(
            _mutation_status(context, result.changed),
            result.brain_id,
        ),
        _committed_effects(
            request,
            default_pointer_affected=result.changed and not context.dry_run,
        ),
    )


def execute_remove_stale(context: LauncherContext, request: RegistryRemoveStaleRequest):
    import vault_registry

    try:
        result = vault_registry.prune_action(dry_run=context.dry_run)
    except vault_registry.RegistryPartialApplyError as exc:
        return _partial_registry(request, exc)
    except (vault_registry.RegistryReadError, ValueError) as exc:
        return _registry_error(request, exc)
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        RegistryRemoveStalePayload(
            _mutation_status(context, result.changed),
            result.removed_brain_ids,
            result.default_cleared,
        ),
        _committed_effects(
            request,
            brain_ids=result.removed_brain_ids,
            registry_affected=result.changed and not context.dry_run,
            default_pointer_affected=result.default_cleared and not context.dry_run,
        ),
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


def register_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainRegisterRequest,
        BrainRegisterPayload,
        "_launcher.registry:register",
        execute_register,
    )


def unregister_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainUnregisterRequest,
        BrainUnregisterPayload,
        "_launcher.registry:unregister",
        execute_unregister,
    )


def set_default_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainSetDefaultRequest,
        BrainSetDefaultPayload,
        "_launcher.registry:set_default",
        execute_set_default,
    )


def clear_default_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainClearDefaultRequest,
        BrainClearDefaultPayload,
        "_launcher.registry:clear_default",
        execute_clear_default,
    )


def remove_stale_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        RegistryRemoveStaleRequest,
        RegistryRemoveStalePayload,
        "_launcher.registry:remove_stale",
        execute_remove_stale,
    )
