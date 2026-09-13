"""Typed exact-slug ``workspace.read`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
    decode_reference,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok
from ..types import validate_slug


class WorkspaceMode(str, Enum):
    EMBEDDED = "embedded"
    LINKED = "linked"


@dataclass(frozen=True, slots=True)
class WorkspaceReadPayload:
    slug: str
    mode: WorkspaceMode
    path: str
    hub_path: str | None
    title: str
    status: str | None
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceReadRequest:
    COMMAND_ID: ClassVar[str] = "workspace.read"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = WorkspaceReadPayload

    reference: str

    def __post_init__(self) -> None:
        validate_slug(self.reference)


def _payload(resource):
    return WorkspaceReadPayload(
        slug=resource["slug"],
        mode=WorkspaceMode(resource["mode"]),
        path=resource["path"],
        hub_path=resource.get("hub_path") or None,
        title=resource["title"],
        status=resource.get("status") or None,
        tags=tuple(resource.get("tags") or ()),
    )


def read_result(context: InvocationContext, request: WorkspaceReadRequest):
    import workspace_registry

    try:
        resource = workspace_registry.read_workspace_strict(
            context.selected_brain.vault_root,
            request.reference,
        )
    except workspace_registry.UnknownWorkspaceError as exc:
        return command_error(
            WorkspaceReadRequest,
            ErrorCode.NOT_FOUND,
            str(exc),
            "reference",
        )
    except ValueError as exc:
        return command_error(
            WorkspaceReadRequest,
            ErrorCode.CONFLICT,
            str(exc),
            None,
        )
    return Ok(
        WorkspaceReadRequest.COMMAND_ID,
        WorkspaceReadRequest.COMMAND_VERSION,
        _payload(resource),
    )


def execute(context: InvocationContext, request: WorkspaceReadRequest):
    from ..preparation import execute_prepared_read
    return execute_prepared_read(context, request, read_result)


def decode(payload: Mapping[str, object]) -> WorkspaceReadRequest:
    return decode_reference(payload, WorkspaceReadRequest)


def catalogue_entry():
    from dataclasses import replace
    from ..preparation import ResultReadPreparation
    return replace(_catalogue_entry(WorkspaceReadRequest, execute),
                   preparation=ResultReadPreparation(read_result))
