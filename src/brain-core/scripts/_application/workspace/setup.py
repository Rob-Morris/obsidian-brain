"""Typed ``workspace.setup`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._caller_workspace import (
    CallerWorkspacePayload,
    caller_workspace_entry,
    execute_workspace_lifecycle,
    lifecycle_effects,
    optional_bool,
    reject_unexpected,
    require_string,
    workspace_dir,
)
from ..context import InvocationContext
from ..results import Error
from ..types import validate_slug


@dataclass(frozen=True, slots=True)
class WorkspaceSetupRequest:
    COMMAND_ID: ClassVar[str] = "workspace.setup"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = CallerWorkspacePayload

    brain_id: str | None = None
    slug: str | None = None
    force: bool = False

    def __post_init__(self) -> None:
        require_string(self.brain_id, "brain_id", optional=True)
        require_string(self.slug, "slug", optional=True)
        if self.slug is not None:
            validate_slug(self.slug)
        if not isinstance(self.force, bool):
            raise ValueError("force must be a boolean")


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
    reject_unexpected(payload, {"brain_id", "slug", "force"})
    return WorkspaceSetupRequest(
        require_string(payload.get("brain_id"), "brain_id", optional=True),
        require_string(payload.get("slug"), "slug", optional=True),
        optional_bool(payload.get("force"), "force"),
    )


def catalogue_entry():
    return caller_workspace_entry(WorkspaceSetupRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(WorkspaceSetupRequest, decode)
