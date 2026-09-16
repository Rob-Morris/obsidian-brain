"""Explicitly reassign a complete owned subtree without inferring membership."""

from dataclasses import dataclass, replace
from typing import ClassVar, Mapping

from .._decoding import reject_unexpected
from .._artefact_transition import execute_transition, catalogue_entry as transition_entry, PathChange, validate_string
from ..workspace_context import WorkspaceAwareRequest, WorkspaceMutationPayload, workspace_request_decoder, validate_workspace_request
from ..preparation_transition import TransitionPreparation
from ..workspace_reassignment import plan_reassignment


@dataclass(frozen=True, slots=True)
class ArtefactSetWorkspacePayload(WorkspaceMutationPayload):
    path: str
    workspace: str | None
    subjects: tuple[str, ...]
    changed_subjects: tuple[str, ...]
    moves: tuple[PathChange, ...]
    links_updated: int
    changed: bool


@dataclass(frozen=True, slots=True)
class ArtefactSetWorkspaceRequest(WorkspaceAwareRequest):
    COMMAND_ID: ClassVar[str] = "artefact.set-workspace"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactSetWorkspacePayload

    path: str
    recursive: bool = False
    parent: str | None = None
    clear_parent: bool = False

    def __post_init__(self):
        validate_workspace_request(self)
        validate_string(self.COMMAND_ID, "path", self.path)
        for name in ("recursive", "clear_parent"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if self.parent is not None:
            validate_string(self.COMMAND_ID, "parent", self.parent)
            if self.clear_parent:
                raise ValueError("parent and clear_parent are mutually exclusive")


def execute(context, request):
    import edit
    return execute_transition(context, request, planner=plan_reassignment, apply_plan=edit.apply_artefact_transition,
        payload_builder=lambda result: ArtefactSetWorkspacePayload(**{**result,
            "moves": tuple(PathChange(**item) for item in result["moves"])}),
        effect_subject=lambda payload: next(iter(payload.changed_subjects), None),
        effect_subjects=lambda payload: payload.changed_subjects)


@workspace_request_decoder
def decode(payload: Mapping[str, object]):
    reject_unexpected(payload, {"path", "recursive", "parent", "clear_parent"})
    return ArtefactSetWorkspaceRequest(payload.get("path"), payload.get("recursive", False),
        payload.get("parent"), payload.get("clear_parent", False))


def catalogue_entry():
    return replace(transition_entry(ArtefactSetWorkspaceRequest, execute),
        preparation=TransitionPreparation(plan_reassignment))
