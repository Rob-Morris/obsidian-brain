"""Typed ``artefact.unarchive`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._artefact_transition import (
    ArtefactUnarchivePayload,
    UninspectedArchiveCandidate,
    catalogue_entry as transition_catalogue_entry,
    decode_path_recursive,
    execute_transition,
    path_changes,
    validate_recursive,
    validate_string,
)
from ..context import InvocationContext
from ..workspace_context import WorkspaceAwareRequest, workspace_request_decoder, validate_workspace_request


@dataclass(frozen=True, slots=True)
class ArtefactUnarchiveRequest(WorkspaceAwareRequest):
    COMMAND_ID: ClassVar[str] = "artefact.unarchive"
    COMMAND_VERSION: ClassVar[int] = 3
    RESULT_TYPE: ClassVar[type] = ArtefactUnarchivePayload

    path: str
    recursive: bool = False

    def __post_init__(self) -> None:
        validate_workspace_request(self)
        validate_string(self.COMMAND_ID, "path", self.path)
        validate_recursive(self.COMMAND_ID, self.recursive)


def execute(context: InvocationContext, request: ArtefactUnarchiveRequest):
    import edit

    return execute_transition(
        context,
        request,
        planner=plan_operation, apply_plan=edit.apply_artefact_transition,
        payload_builder=lambda result: ArtefactUnarchivePayload(
            result["old_path"],
            result["new_path"],
            result["links_updated"],
            path_changes(result["restored"]),
            tuple(
                UninspectedArchiveCandidate(item["path"], item["reason"])
                for item in result.get("uninspected") or ()
            ),
        ),
        effect_subject=lambda payload: payload.new_path,
    )


@workspace_request_decoder
def decode(payload: Mapping[str, object]) -> ArtefactUnarchiveRequest:
    return decode_path_recursive(payload, ArtefactUnarchiveRequest)


def catalogue_entry():
    from dataclasses import replace
    from ..preparation_transition import TransitionPreparation

    return replace(transition_catalogue_entry(ArtefactUnarchiveRequest, execute),
                   preparation=TransitionPreparation(plan_operation))


def plan_operation(context, request, router, *, frozen_inputs=None):
    import edit
    from ..preparation_transition import transition_time

    effective_at, frozen = transition_time(context, frozen_inputs)
    return edit.plan_unarchive(str(context.selected_brain.vault_root), router,
                             request.path, request.recursive, effective_at=effective_at), frozen
