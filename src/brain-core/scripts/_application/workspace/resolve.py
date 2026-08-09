"""Typed exact-slug ``workspace.resolve`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
    decode_reference,
    resolver_entry as _resolver_entry,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok
from ..types import validate_slug
from .read import WorkspaceMode


@dataclass(frozen=True, slots=True)
class WorkspaceResolvePayload:
    slug: str
    mode: WorkspaceMode
    path: str


@dataclass(frozen=True, slots=True)
class WorkspaceResolveRequest:
    COMMAND_ID: ClassVar[str] = "workspace.resolve"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = WorkspaceResolvePayload

    reference: str

    def __post_init__(self) -> None:
        validate_slug(self.reference)


def execute(context: InvocationContext, request: WorkspaceResolveRequest):
    import workspace_registry

    try:
        resource = workspace_registry.resolve_workspace_strict(
            context.selected_brain.vault_root,
            request.reference,
        )
    except workspace_registry.UnknownWorkspaceError as exc:
        return command_error(
            WorkspaceResolveRequest,
            ErrorCode.NOT_FOUND,
            str(exc),
            "reference",
        )
    except ValueError as exc:
        return command_error(
            WorkspaceResolveRequest,
            ErrorCode.CONFLICT,
            str(exc),
            None,
        )
    return Ok(
        WorkspaceResolveRequest.COMMAND_ID,
        WorkspaceResolveRequest.COMMAND_VERSION,
        WorkspaceResolvePayload(
            resource["slug"],
            WorkspaceMode(resource["mode"]),
            resource["path"],
        ),
    )


def decode(payload: Mapping[str, object]) -> WorkspaceResolveRequest:
    return decode_reference(payload, WorkspaceResolveRequest)


def catalogue_entry():
    return _catalogue_entry(WorkspaceResolveRequest, execute)


def resolver_entry():
    return _resolver_entry(WorkspaceResolveRequest, decode)
