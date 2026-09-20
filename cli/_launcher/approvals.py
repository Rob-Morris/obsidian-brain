"""Machine-local entry points for opt-in Brain client approval management."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

from .contracts import CommittedEffect, ErrorCode, Ok, no_effect_error


class ApprovalClient(str, Enum):
    CODEX = "codex"
    CLAUDE = "claude"
    ALL = "all"


class ApprovalSurface(str, Enum):
    MCP = "mcp"
    CLI = "cli"


class ApprovalScope(str, Enum):
    USER = "user"
    PROJECT = "project"
    LOCAL = "local"


class ApprovalAction(str, Enum):
    CONFIGURE = "configure"
    REPAIR = "repair"
    ADOPT = "adopt"
    DETACH = "detach"
    REMOVE = "remove"
    RECOVER = "recover"


@dataclass(frozen=True, slots=True)
class ApprovalItemStatus:
    identity: str
    state: str


@dataclass(frozen=True, slots=True)
class ApprovalTargetStatus:
    client: str
    scope: str
    surface: str
    path: str
    state: str
    items: tuple[ApprovalItemStatus, ...]
    activation: str


@dataclass(frozen=True, slots=True)
class ApprovalsPayload:
    policy: str
    targets: tuple[ApprovalTargetStatus, ...]
    changed_paths: tuple[str, ...]
    complete: bool = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "complete", all(item.state not in {"blocked", "conflicted", "overridden"} for item in self.targets))


@dataclass(frozen=True, slots=True)
class ApprovalsInspectRequest:
    COMMAND_ID: ClassVar[str] = "approvals.inspect"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ApprovalsPayload

    client: ApprovalClient = ApprovalClient.ALL
    scope: ApprovalScope = ApprovalScope.USER
    surfaces: tuple[ApprovalSurface, ...] = (ApprovalSurface.MCP, ApprovalSurface.CLI)

    def __post_init__(self):
        _validate(self.client, self.scope, self.surfaces)


@dataclass(frozen=True, slots=True)
class ApprovalsConfigureRequest:
    COMMAND_ID: ClassVar[str] = "approvals.configure"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ApprovalsPayload

    client: ApprovalClient
    scope: ApprovalScope
    surfaces: tuple[ApprovalSurface, ...] = field(metadata={"example": (ApprovalSurface.MCP,)})
    action: ApprovalAction = ApprovalAction.CONFIGURE
    adopt_items: tuple[str, ...] = ()
    restore_items: tuple[str, ...] = ()

    def __post_init__(self):
        _validate(self.client, self.scope, self.surfaces)
        if not isinstance(self.action, ApprovalAction):
            raise ValueError("approval action must be explicit and supported")
        if self.adopt_items and self.action is not ApprovalAction.ADOPT:
            raise ValueError("adopt_items require the adopt action")
        if self.restore_items and self.action not in {ApprovalAction.REPAIR, ApprovalAction.CONFIGURE}:
            raise ValueError("restore_items require repair or configure")
        for values in (self.adopt_items, self.restore_items):
            if not isinstance(values, tuple) or any(not isinstance(item, str) or not item for item in values):
                raise ValueError("approval item selections must be exact non-empty identities")
            if len(values) != len(set(values)):
                raise ValueError("approval item selections must be unique")


def _validate(client, scope, surfaces):
    if not isinstance(client, ApprovalClient) or not isinstance(scope, ApprovalScope):
        raise ValueError("select an approval client and scope explicitly")
    if not isinstance(surfaces, tuple) or not surfaces or any(not isinstance(s, ApprovalSurface) for s in surfaces):
        raise ValueError("select MCP, CLI or both approval surfaces explicitly")
    if len(surfaces) != len(set(surfaces)):
        raise ValueError("approval surfaces must be unique")
    if scope is ApprovalScope.LOCAL and client is ApprovalClient.CODEX:
        raise ValueError("Codex does not support local approval scope")


def execute_inspect(context, request):
    from .approval_management import manage
    try:
        return Ok(request.COMMAND_ID, request.COMMAND_VERSION, manage(context, request, inspect=True))
    except (OSError, ValueError, RuntimeError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))


def execute_configure(context, request):
    from .approval_management import manage
    from _bootstrap.file_transaction import FileTransactionError
    from .contracts import Partial, CommandError, RecoveryRequiredDetails, InstructionNextAction
    try:
        payload = manage(context, request)
        effects = tuple(CommittedEffect(request.COMMAND_ID, path) for path in payload.changed_paths) if not context.dry_run else ()
        return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload, effects)
    except FileTransactionError as exc:
        if exc.surviving_paths:
            error = CommandError(ErrorCode.CONFLICT, str(exc), RecoveryRequiredDetails(tuple(str(p) for p in exc.surviving_paths), str(exc)),
                                 InstructionNextAction("Inspect the approval transaction and recover explicitly before retrying."))
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION, error,
                           tuple(CommittedEffect(request.COMMAND_ID, str(p)) for p in exc.surviving_paths))
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    except (OSError, ValueError, RuntimeError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))


def inspect_owner():
    from .owners import LauncherOwner
    return LauncherOwner(ApprovalsInspectRequest, ApprovalsPayload, "_launcher.approvals:inspect", execute_inspect)


def configure_owner():
    from .owners import LauncherOwner
    return LauncherOwner(ApprovalsConfigureRequest, ApprovalsPayload, "_launcher.approvals:configure", execute_configure)
