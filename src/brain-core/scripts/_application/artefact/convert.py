"""Typed ``artefact.convert`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._decoding import reject_unexpected
from .._artefact_transition import (
    ArtefactConvertPayload,
    PathChange,
    catalogue_entry as transition_catalogue_entry,
    execute_transition,
    validate_recursive,
    validate_string,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class ArtefactConvertRequest:
    COMMAND_ID: ClassVar[str] = "artefact.convert"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = ArtefactConvertPayload

    path: str
    target_type: str
    parent: str | None = None
    recursive: bool = False

    def __post_init__(self) -> None:
        validate_string(self.COMMAND_ID, "path", self.path)
        validate_string(self.COMMAND_ID, "target_type", self.target_type)
        if self.parent is not None:
            validate_string(self.COMMAND_ID, "parent", self.parent)
        validate_recursive(self.COMMAND_ID, self.recursive)


def execute(context: InvocationContext, request: ArtefactConvertRequest):
    import edit

    return execute_transition(
        context,
        request,
        operation=lambda root, router: edit.convert_artefact(
            root,
            router,
            request.path,
            request.target_type,
            parent=request.parent,
            recursive=request.recursive,
        ),
        payload_builder=_payload,
        effect_subject=lambda payload: payload.new_path,
    )


def decode(payload: Mapping[str, object]) -> ArtefactConvertRequest:
    reject_unexpected(payload, {"path", "target_type", "parent", "recursive"})
    for field in ("path", "target_type"):
        if field not in payload:
            raise ValueError(f"{field} is required")
        if not isinstance(payload[field], str):
            raise ValueError(f"{field} must be a string")
    parent = payload.get("parent")
    recursive = payload.get("recursive", False)
    if parent is not None and not isinstance(parent, str):
        raise ValueError("parent must be a string or null")
    if not isinstance(recursive, bool):
        raise ValueError("recursive must be a boolean")
    return ArtefactConvertRequest(
        payload["path"], payload["target_type"], parent, recursive
    )


def _payload(result: dict) -> ArtefactConvertPayload:
    moved = result.get("attachment_scope_moved")
    return ArtefactConvertPayload(
        old_path=result["old_path"],
        new_path=result["new_path"],
        type=result["type"],
        links_updated=result["links_updated"],
        attachment_scope_moved=(
            None if moved is None else PathChange(moved["from"], moved["to"])
        ),
        orphaned_attachment_scopes=tuple(
            result.get("orphaned_attachment_scopes") or ()
        ),
    )


def catalogue_entry():
    return transition_catalogue_entry(ArtefactConvertRequest, execute)
