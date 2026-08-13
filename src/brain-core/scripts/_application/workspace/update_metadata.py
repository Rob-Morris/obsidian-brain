"""Typed ``workspace.update-metadata`` owner."""

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


@dataclass(frozen=True, slots=True)
class WorkspaceMetadataLink:
    name: str
    value: str

    def __post_init__(self) -> None:
        require_string(self.name, "link name")
        require_string(self.value, "link value")


@dataclass(frozen=True, slots=True)
class WorkspaceUpdateMetadataRequest:
    COMMAND_ID: ClassVar[str] = "workspace.update-metadata"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = CallerWorkspacePayload
    MINIMAL_EXAMPLE: ClassVar[dict[str, bool]] = {"clear_tags": True}

    tags: tuple[str, ...] = ()
    clear_tags: bool = False
    links: tuple[WorkspaceMetadataLink, ...] = ()
    clear_links: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.tags, tuple):
            raise ValueError("tags must be a tuple")
        if any(not isinstance(tag, str) or not tag.strip() for tag in self.tags):
            raise ValueError("tags must contain non-empty strings")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("tags must not contain duplicates")
        if not isinstance(self.links, tuple) or any(
            not isinstance(link, WorkspaceMetadataLink) for link in self.links
        ):
            raise ValueError("links must contain WorkspaceMetadataLink values")
        names = [link.name for link in self.links]
        if len(names) != len(set(names)):
            raise ValueError("links must not contain duplicate names")
        if not isinstance(self.clear_tags, bool) or not isinstance(
            self.clear_links,
            bool,
        ):
            raise ValueError("clear_tags and clear_links must be booleans")
        if not self.tags and not self.links and not self.clear_tags and not self.clear_links:
            raise ValueError("workspace.update-metadata requires at least one change")


def execute(context: InvocationContext, request: WorkspaceUpdateMetadataRequest):
    import configure

    target = workspace_dir(context, WorkspaceUpdateMetadataRequest)
    if isinstance(target, Error):
        return target
    return execute_workspace_lifecycle(
        context,
        request,
        operation="update-metadata",
        invoke=lambda: configure.configure_workspace_metadata_action(
            context.selected_brain.vault_root,
            workspace_dir=target,
            tags=list(request.tags),
            clear_tags=request.clear_tags,
            links=[f"{link.name}={link.value}" for link in request.links],
            clear_links=request.clear_links,
        ),
        effect_subjects=lambda result: lifecycle_effects(
            "caller-workspace:.brain/local/workspace.yaml",
            result,
        ),
        lock_root=target,
    )


def decode(payload: Mapping[str, object]) -> WorkspaceUpdateMetadataRequest:
    reject_unexpected(payload, {"tags", "clear_tags", "links", "clear_links"})
    raw_tags = payload.get("tags", ())
    if not isinstance(raw_tags, (list, tuple)):
        raise ValueError("tags must be a list of strings")
    tags = tuple(require_string(tag, "tag") for tag in raw_tags)
    raw_links = payload.get("links", {})
    if not isinstance(raw_links, Mapping):
        raise ValueError("links must be an object of name/value strings")
    links = [
        WorkspaceMetadataLink(
            require_string(name, "link name"),
            require_string(value, "link value"),
        )
        for name, value in raw_links.items()
    ]
    links.sort(key=lambda link: link.name)
    return WorkspaceUpdateMetadataRequest(
        tags,
        optional_bool(payload.get("clear_tags"), "clear_tags"),
        tuple(links),
        optional_bool(payload.get("clear_links"), "clear_links"),
    )


def catalogue_entry():
    return caller_workspace_entry(WorkspaceUpdateMetadataRequest, execute)
