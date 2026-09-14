"""Typed ``artefact.delete`` owner."""

from __future__ import annotations

from dataclasses import replace
from ..types import InitialAuthorisationClass

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._artefact_transition import (
    ArtefactDeletePayload,
    catalogue_entry as transition_catalogue_entry,
    decode_path_recursive,
    execute_transition,
    validate_recursive,
    validate_string,
)
from ..context import InvocationContext
from ..types import Authority


@dataclass(frozen=True, slots=True)
class ArtefactDeleteRequest:
    COMMAND_ID: ClassVar[str] = "artefact.delete"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = ArtefactDeletePayload

    path: str
    recursive: bool = False

    def __post_init__(self) -> None:
        validate_string(self.COMMAND_ID, "path", self.path)
        validate_recursive(self.COMMAND_ID, self.recursive)


def execute(context: InvocationContext, request: ArtefactDeleteRequest):
    import rename

    return execute_transition(
        context,
        request,
        operation=None, planner=plan_operation, apply_plan=rename.apply_artefact_delete,
        payload_builder=lambda result: ArtefactDeletePayload(
            request.path,
            tuple(result["deleted"]),
            result["links_replaced"],
            tuple(result["orphaned_attachment_scopes"]),
        ),
        effect_subject=lambda payload: payload.path,
    )

def decode(payload: Mapping[str, object]) -> ArtefactDeleteRequest:
    return decode_path_recursive(payload, ArtefactDeleteRequest)


def catalogue_entry():
    from dataclasses import replace
    from ..preparation_transition import TransitionPreparation

    return replace(replace(transition_catalogue_entry(
        ArtefactDeleteRequest,
        execute,
        authority=Authority.ADMINISTRATOR,
    ), preparation=TransitionPreparation(plan_operation)), initial_class=InitialAuthorisationClass.EXCEPTIONAL)


def plan_operation(context, request, router, *, frozen_inputs=None):
    import rename

    return rename.plan_artefact_delete(str(context.selected_brain.vault_root), request.path,
                                      router, request.recursive, prune_router=router), dict(frozen_inputs or {})
