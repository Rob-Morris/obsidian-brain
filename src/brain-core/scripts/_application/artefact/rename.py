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


@dataclass(frozen=True, slots=True)
class ArtefactRenameRequest:
    COMMAND_ID: ClassVar[str] = "artefact.rename"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactRenamePayload

    source: str
    dest: str

    def __post_init__(self) -> None:
        validate_string(self.COMMAND_ID, "source", self.source)
        validate_string(self.COMMAND_ID, "dest", self.dest)


def execute(context: InvocationContext, request: ArtefactRenameRequest):
    import rename

    return execute_transition(
        context,
        request,
        operation=None, planner=plan_operation, apply_plan=rename.apply_artefact_rename,
        payload_builder=lambda result: ArtefactRenamePayload(**result),
        effect_subject=lambda payload: payload.new_path,
    )


def decode(payload: Mapping[str, object]) -> ArtefactRenameRequest:
    return decode_required_strings(payload, ArtefactRenameRequest, ("source", "dest"))


def catalogue_entry():
    from dataclasses import replace
    from ..preparation_transition import TransitionPreparation

    return replace(transition_catalogue_entry(ArtefactRenameRequest, execute),
                   preparation=TransitionPreparation(plan_operation))


def plan_operation(context, request, router, *, frozen_inputs=None):
    import rename

    return rename.plan_artefact_rename(str(context.selected_brain.vault_root), router,
                                      request.source, request.dest), dict(frozen_inputs or {})
