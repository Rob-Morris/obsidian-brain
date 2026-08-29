"""Typed ``workspace.setup`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._caller_workspace import (
    CallerWorkspacePayload,
    caller_workspace_entry,
    decode_workspace_binding,
    execute_workspace_lifecycle,
    lifecycle_effects,
    validate_workspace_binding_request,
    workspace_dir,
)
from ..context import InvocationContext
from ..results import Error


@dataclass(frozen=True, slots=True)
class WorkspaceSetupRequest:
    COMMAND_ID: ClassVar[str] = "workspace.setup"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = CallerWorkspacePayload

    brain_id: str | None = None
    slug: str | None = None
    force: bool = False

    def __post_init__(self) -> None:
        validate_workspace_binding_request(self)


def execute(context: InvocationContext, request: WorkspaceSetupRequest):
    import setup

    target = workspace_dir(context, WorkspaceSetupRequest)
    if isinstance(target, Error):
        return target
    return execute_workspace_lifecycle(
        context,
        request,
        operation="setup",
        invoke=lambda: setup._setup_workspace_core(
            context.selected_brain.vault_root,
            workspace_dir=target,
            brain_id=request.brain_id,
            slug=request.slug,
            force=request.force,
        ),
        effect_subjects=lambda result: lifecycle_effects(
            "caller-workspace:.brain/local/workspace.yaml",
            result,
        ),
        lock_root=target,
    )


def decode(payload: Mapping[str, object]) -> WorkspaceSetupRequest:
    return decode_workspace_binding(payload, WorkspaceSetupRequest)


def catalogue_entry():
    return caller_workspace_entry(WorkspaceSetupRequest, execute)
