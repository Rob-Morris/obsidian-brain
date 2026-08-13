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
        operation=lambda root, router: rename.rename_artefact(
            root, router, request.source, request.dest
        ),
        payload_builder=lambda result: ArtefactRenamePayload(**result),
        effect_subject=lambda payload: payload.new_path,
    )


def decode(payload: Mapping[str, object]) -> ArtefactRenameRequest:
    return decode_required_strings(payload, ArtefactRenameRequest, ("source", "dest"))


def catalogue_entry():
    return transition_catalogue_entry(ArtefactRenameRequest, execute)
