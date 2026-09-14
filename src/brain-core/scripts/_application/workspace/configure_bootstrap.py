"""Typed ``workspace.configure-bootstrap`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, Mapping

from .._caller_workspace import (
    CallerWorkspacePayload,
    caller_workspace_entry,
    execute_workspace_lifecycle,
    optional_bool,
    reject_unexpected,
    workspace_dir,
)
from ..context import InvocationContext
from ..results import Error


@dataclass(frozen=True, slots=True)
class WorkspaceConfigureBootstrapRequest:
    COMMAND_ID: ClassVar[str] = "workspace.configure-bootstrap"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = CallerWorkspacePayload

    surface: Literal["all", "agents", "claude", "grok"] = "all"
    remove: bool = False

    def __post_init__(self) -> None:
        if self.surface not in {"all", "agents", "claude", "grok"}:
            raise ValueError("surface must be all, agents, claude or grok")
        if not isinstance(self.remove, bool):
            raise ValueError("remove must be a boolean")


def execute(context: InvocationContext, request: WorkspaceConfigureBootstrapRequest):
    import configure

    target = workspace_dir(context, WorkspaceConfigureBootstrapRequest)
    if isinstance(target, Error):
        return target

    def effects(result):
        subjects = []
        for step in result.get("steps") or ():
            if step.get("status") != "changed":
                continue
            if step.get("name") == "workspace_bootstrap_agents":
                subjects.append("caller-workspace:AGENTS.md")
            elif step.get("name") == "workspace_bootstrap_claude":
                subjects.append("caller-workspace:CLAUDE.md")
            elif step.get("name") == "workspace_bootstrap_grok":
                subjects.append("caller-workspace:.grok/rules/brain.md")
        return tuple(subjects)

    return execute_workspace_lifecycle(
        context,
        request,
        operation="configure-bootstrap",
        invoke=lambda before_write: configure.configure_workspace_bootstrap_action(
            context.selected_brain.vault_root,
            workspace_dir=target,
            before_write=before_write,
            surface=request.surface,
            remove=request.remove,
        ),
        effect_subjects=effects,
        lock_root=target,
    )


def decode(payload: Mapping[str, object]) -> WorkspaceConfigureBootstrapRequest:
    reject_unexpected(payload, {"surface", "remove"})
    surface = payload.get("surface", "all")
    if not isinstance(surface, str):
        raise ValueError("surface must be a string")
    return WorkspaceConfigureBootstrapRequest(
        surface,
        optional_bool(payload.get("remove"), "remove"),
    )


def catalogue_entry():
    return caller_workspace_entry(WorkspaceConfigureBootstrapRequest, execute)
