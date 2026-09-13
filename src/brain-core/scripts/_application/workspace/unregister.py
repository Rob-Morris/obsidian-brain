"""Typed ``workspace.unregister`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._caller_workspace import (
    CallerWorkspacePayload,
    caller_workspace_entry,
    execute_workspace_lifecycle,
    reject_unexpected,
    require_string,
)
from ..context import InvocationContext
from ..types import validate_slug


@dataclass(frozen=True, slots=True)
class WorkspaceUnregisterRequest:
    COMMAND_ID: ClassVar[str] = "workspace.unregister"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = CallerWorkspacePayload

    slug: str

    def __post_init__(self) -> None:
        validate_slug(self.slug)


def execute(context: InvocationContext, request: WorkspaceUnregisterRequest):
    import workspace_registry

    def unregister(before_write):
        workspace_registry.unregister_workspace(
            context.selected_brain.vault_root,
            request.slug,
            **({"before_write": before_write} if before_write is not None else {}),
        )
        return {
            "status": "ok",
            "steps": [
                {
                    "name": "workspace_registry",
                    "status": "changed",
                    "message": f"Unregistered linked workspace {request.slug}.",
                }
            ],
        }

    return execute_workspace_lifecycle(
        context,
        request,
        operation="unregister",
        invoke=unregister,
        effect_subjects=lambda _result: (
            f"caller-workspace-registration:{request.slug}",
        ),
        lock_root=context.selected_brain.vault_root,
    )


def decode(payload: Mapping[str, object]) -> WorkspaceUnregisterRequest:
    reject_unexpected(payload, {"slug"})
    return WorkspaceUnregisterRequest(require_string(payload.get("slug"), "slug"))


def catalogue_entry():
    return caller_workspace_entry(WorkspaceUnregisterRequest, execute)
