"""Typed ``artefact.reparent-children`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from ..workspace_context import WorkspaceMutationPayload
from enum import Enum
from typing import ClassVar, Mapping

from .._artefact_transition import (
    PathChange,
    catalogue_entry as transition_catalogue_entry,
    execute_transition,
    validate_string,
)
from ..context import InvocationContext
from ..workspace_context import WorkspaceAwareRequest, workspace_request_decoder, validate_workspace_request


class ReparentChildrenMode(str, Enum):
    SOURCE_PARENT = "source-parent"
    TOP_LEVEL = "top-level"
    PARENT = "parent"


@dataclass(frozen=True, slots=True)
class ReparentedChild:
    key: str
    old_path: str


@dataclass(frozen=True, slots=True)
class ArtefactReparentChildrenPayload(WorkspaceMutationPayload):
    source: str
    to: str | None
    children: tuple[ReparentedChild, ...]
    moves: tuple[PathChange, ...]
    links_updated: int


@dataclass(frozen=True, slots=True)
class ArtefactReparentChildrenRequest(WorkspaceAwareRequest):
    COMMAND_ID: ClassVar[str] = "artefact.reparent-children"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = ArtefactReparentChildrenPayload

    source: str
    mode: ReparentChildrenMode
    parent: str | None = None

    def __post_init__(self) -> None:
        validate_workspace_request(self)
        validate_string(self.COMMAND_ID, "source", self.source)
        if not isinstance(self.mode, ReparentChildrenMode):
            raise ValueError("artefact.reparent-children mode is invalid")
        if self.mode is ReparentChildrenMode.PARENT:
            if self.parent is None:
                raise ValueError("parent mode requires a parent")
            validate_string(self.COMMAND_ID, "parent", self.parent)
        elif self.parent is not None:
            raise ValueError(f"{self.mode.value} mode does not accept parent")


def execute(context: InvocationContext, request: ArtefactReparentChildrenRequest):
    import edit

    return execute_transition(
        context,
        request,
        planner=plan_operation, apply_plan=edit.apply_artefact_transition,
        payload_builder=_payload,
        effect_subject=lambda payload: payload.source if payload.children else None,
    )


@workspace_request_decoder
def decode(payload: Mapping[str, object]) -> ArtefactReparentChildrenRequest:
    reject_unexpected(payload, {"source", "mode", "parent"})
    for field in ("source", "mode"):
        if field not in payload:
            raise ValueError(f"{field} is required")
        if not isinstance(payload[field], str):
            raise ValueError(f"{field} must be a string")
    try:
        mode = ReparentChildrenMode(payload["mode"])
    except ValueError as exc:
        raise ValueError(
            "mode must be 'source-parent', 'top-level', or 'parent'"
        ) from exc
    parent = payload.get("parent")
    if parent is not None and not isinstance(parent, str):
        raise ValueError("parent must be a string")
    return ArtefactReparentChildrenRequest(payload["source"], mode, parent)


def _target(request: ArtefactReparentChildrenRequest) -> tuple[str | None, bool]:
    if request.mode is ReparentChildrenMode.SOURCE_PARENT:
        return None, False
    if request.mode is ReparentChildrenMode.TOP_LEVEL:
        return None, True
    return request.parent, True


def _payload(result: dict) -> ArtefactReparentChildrenPayload:
    return ArtefactReparentChildrenPayload(
        source=result["source"],
        to=result["to"],
        children=tuple(
            ReparentedChild(item["key"], item["old_path"])
            for item in result["children"]
        ),
        moves=tuple(
            PathChange(item["source"], item["dest"])
            for item in result["moves"]
        ),
        links_updated=result["links_updated"],
    )


def catalogue_entry():
    from dataclasses import replace
    from ..preparation_transition import TransitionPreparation

    return replace(transition_catalogue_entry(ArtefactReparentChildrenRequest, execute),
                   preparation=TransitionPreparation(plan_operation))


def plan_operation(context, request, router, *, frozen_inputs=None):
    import edit

    target, provided = _target(request)
    return edit.plan_reparent_children(str(context.selected_brain.vault_root), router,
                                      request.source, target, to_provided=provided), dict(frozen_inputs or {})
