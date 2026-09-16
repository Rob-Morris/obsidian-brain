"""Typed ``artefact.rename`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._artefact_transition import (
    ArtefactRenamePayload,
    catalogue_entry as transition_catalogue_entry,
    decode_required_strings,
    execute_transition,
    validate_string,
)
from ..context import InvocationContext
from ..workspace_context import WorkspaceAwareRequest, workspace_request_decoder, validate_workspace_request


@dataclass(frozen=True, slots=True)
class ArtefactRenameRequest(WorkspaceAwareRequest):
    COMMAND_ID: ClassVar[str] = "artefact.rename"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = ArtefactRenamePayload

    source: str
    dest: str

    def __post_init__(self) -> None:
        validate_workspace_request(self)
        validate_string(self.COMMAND_ID, "source", self.source)
        validate_string(self.COMMAND_ID, "dest", self.dest)


def execute(context: InvocationContext, request: ArtefactRenameRequest):
    import edit

    return execute_transition(
        context,
        request,
        planner=plan_operation, apply_plan=edit.apply_artefact_transition,
        payload_builder=lambda result: ArtefactRenamePayload(**result),
        effect_subject=lambda payload: payload.new_path,
    )


@workspace_request_decoder
def decode(payload: Mapping[str, object]) -> ArtefactRenameRequest:
    return decode_required_strings(payload, ArtefactRenameRequest, ("source", "dest"))


def catalogue_entry():
    from dataclasses import replace
    from ..preparation_transition import TransitionPreparation

    return replace(transition_catalogue_entry(ArtefactRenameRequest, execute),
                   preparation=TransitionPreparation(plan_operation))


def plan_operation(context, request, router, *, frozen_inputs=None):
    import rename
    import edit

    movement = rename.plan_artefact_rename(str(context.selected_brain.vault_root), router,
                                         request.source, request.dest)
    move = movement.moves[0]
    plan = edit.ArtefactTransitionPlan("rename", (),
        movement, {"old_path": move["source"], "new_path": move["dest"]})
    return plan, dict(frozen_inputs or {})
