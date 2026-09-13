"""Typed ``artefact.repair`` owner with an explicit repair scope."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._repair_owners import (
    ArtefactRepairPayload,
    catalogue_entry as repair_catalogue_entry,
    execute_repair,
)
from ..context import InvocationContext


class ArtefactRepairScope(str, Enum):
    FRONTMATTER = "frontmatter"
    OWNERSHIP = "ownership"
    EMPTY_FOLDERS = "empty_folders"


_SCOPE_CHOICES = ", ".join(item.value for item in ArtefactRepairScope)


@dataclass(frozen=True, slots=True)
class ArtefactRepairRequest:
    COMMAND_ID: ClassVar[str] = "artefact.repair"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactRepairPayload

    scope: ArtefactRepairScope

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ArtefactRepairScope):
            raise ValueError("artefact.repair scope must use ArtefactRepairScope")


def execute(context: InvocationContext, request: ArtefactRepairRequest):
    import _repair_runtime

    operations = {
        ArtefactRepairScope.FRONTMATTER: _repair_runtime.repair_frontmatter_locked,
        ArtefactRepairScope.OWNERSHIP: _repair_runtime.repair_ownership_locked,
        ArtefactRepairScope.EMPTY_FOLDERS: _repair_runtime.repair_empty_folders_locked,
    }
    return execute_repair(context, request, operation=operations[request.scope])


def decode(payload: Mapping[str, object]) -> ArtefactRepairRequest:
    reject_unexpected(payload, {"scope"})
    scope = payload.get("scope")
    if not isinstance(scope, str):
        raise ValueError("scope must be a string")
    try:
        return ArtefactRepairRequest(ArtefactRepairScope(scope))
    except ValueError as exc:
        if scope not in {item.value for item in ArtefactRepairScope}:
            raise ValueError(f"scope must be one of: {_SCOPE_CHOICES}") from exc
        raise


def catalogue_entry():
    return repair_catalogue_entry(ArtefactRepairRequest, execute)
