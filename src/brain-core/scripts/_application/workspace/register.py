"""Typed ``workspace.register`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._caller_workspace import (
    CallerWorkspacePayload,
    caller_workspace_entry,
    execute_workspace_lifecycle,
    reject_unexpected,
    require_string,
    workspace_dir,
)
from ..context import InvocationContext
from ..results import Error
from ..types import validate_slug


@dataclass(frozen=True, slots=True)
class WorkspaceRegisterRequest:
    COMMAND_ID: ClassVar[str] = "workspace.register"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = CallerWorkspacePayload

    slug: str

    def __post_init__(self) -> None:
        validate_slug(self.slug)


def execute(context: InvocationContext, request: WorkspaceRegisterRequest):
    import workspace_registry

    target = workspace_dir(context, WorkspaceRegisterRequest)
    if isinstance(target, Error):
        return target

    def register():
        result = workspace_registry.register_workspace(
            context.selected_brain.vault_root,
            request.slug,
            target,
        )
        return {
            "status": "ok",
            "steps": [
                {
                    "name": "workspace_registry",
                    "status": "changed",
                    "message": (
                        f"{result['action'].title()} linked workspace "
                        f"{request.slug}."
                    ),
                }
            ],
        }

    return execute_workspace_lifecycle(
        context,
        request,
        operation="register",
        invoke=register,
        effect_subjects=lambda _result: (
            f"caller-workspace-registration:{request.slug}",
        ),
        lock_root=context.selected_brain.vault_root,
    )


def decode(payload: Mapping[str, object]) -> WorkspaceRegisterRequest:
    reject_unexpected(payload, {"slug"})
    return WorkspaceRegisterRequest(require_string(payload.get("slug"), "slug"))


def catalogue_entry():
    return caller_workspace_entry(WorkspaceRegisterRequest, execute)
