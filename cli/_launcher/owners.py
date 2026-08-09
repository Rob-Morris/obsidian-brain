"""Static bindings from launcher requests to their internal executors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import agent_skill, doctor, lifecycle, machine, managed_runtime, mcp, operator, registry, runtime, version
from .context import LauncherContext
from .contracts import CommandResult, validate_command_id


Executor = Callable[[LauncherContext, object], CommandResult]


@dataclass(frozen=True, slots=True)
class LauncherOwner:
    request_type: type
    result_type: type
    owner_ref: str
    executor: Executor

    @property
    def command_id(self) -> str:
        return self.request_type.COMMAND_ID

    @property
    def command_version(self) -> int:
        return self.request_type.COMMAND_VERSION

    def __post_init__(self) -> None:
        validate_command_id(self.command_id)
        if self.command_version < 1 or not self.owner_ref.strip():
            raise ValueError("launcher owners require version and owner_ref")
        if self.request_type.RESULT_TYPE is not self.result_type:
            raise ValueError("launcher request/result ownership must agree")


@dataclass(frozen=True, slots=True)
class LauncherOwners:
    entries: tuple[LauncherOwner, ...]

    def __post_init__(self) -> None:
        ids = [entry.command_id for entry in self.entries]
        request_types = [entry.request_type for entry in self.entries]
        if ids != sorted(ids):
            raise ValueError("launcher owners must be sorted by command ID")
        if len(ids) != len(set(ids)) or len(request_types) != len(set(request_types)):
            raise ValueError("launcher owners cannot repeat command or request identity")

    def resolve(self, request: object) -> LauncherOwner:
        owner = next(
            (entry for entry in self.entries if entry.request_type is type(request)),
            None,
        )
        if owner is None:
            raise KeyError(f"unregistered launcher request: {type(request).__name__}")
        return owner


LAUNCHER_OWNERS = LauncherOwners(
    tuple(
        sorted(
            (
                agent_skill.configure_owner(),
                doctor.doctor_owner(),
                lifecycle.install_owner(),
                lifecycle.uninstall_owner(),
                lifecycle.upgrade_owner(),
                machine.migrate_legacy_owner(),
                machine.prune_runtimes_owner(),
                mcp.configure_owner(),
                mcp.repair_owner(),
                registry.backfill_owner(),
                registry.clear_default_owner(),
                registry.get_default_owner(),
                registry.list_owner(),
                registry.prune_owner(),
                registry.register_owner(),
                registry.resolve_owner(),
                registry.set_default_owner(),
                registry.unregister_owner(),
                version.version_owner(),
                managed_runtime.resolve_owner(),
                managed_runtime.resolve_runnable_owner(),
                operator.generate_key_owner(),
                runtime.repair_owner(),
            ),
            key=lambda owner: owner.command_id,
        )
    )
)
