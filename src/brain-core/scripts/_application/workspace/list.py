"""Typed ``workspace.list`` command and internal executor."""

from __future__ import annotations

from .._decoding import decode_empty
from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok
from .read import WorkspaceReadPayload, _payload


@dataclass(frozen=True, slots=True)
class WorkspaceListPayload:
    items: tuple[WorkspaceReadPayload, ...]
    total: int


@dataclass(frozen=True, slots=True)
class WorkspaceListRequest:
    COMMAND_ID: ClassVar[str] = "workspace.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = WorkspaceListPayload


def execute(context: InvocationContext, _request: WorkspaceListRequest):
    import workspace_registry

    try:
        resources = workspace_registry.list_workspaces_strict(
            context.selected_brain.vault_root
        )
    except ValueError as exc:
        return command_error(WorkspaceListRequest, ErrorCode.CONFLICT, str(exc), None)
    items = tuple(
        _payload(resource)
        for resource in sorted(resources, key=lambda item: item["slug"].casefold())
    )
    return Ok(
        WorkspaceListRequest.COMMAND_ID,
        WorkspaceListRequest.COMMAND_VERSION,
        WorkspaceListPayload(items, len(items)),
    )


def decode(payload: Mapping[str, object]) -> WorkspaceListRequest:
    return decode_empty(payload, WorkspaceListRequest)


def catalogue_entry():
    return _catalogue_entry(WorkspaceListRequest, execute)
