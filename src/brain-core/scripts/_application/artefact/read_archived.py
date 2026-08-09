"""Typed exact-path ``artefact.read-archived`` command owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
    decode_required_string,
    resolver_entry as _resolver_entry,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class ArtefactReadArchivedPayload:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class ArtefactReadArchivedRequest:
    COMMAND_ID: ClassVar[str] = "artefact.read-archived"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactReadArchivedPayload

    path: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("artefact.read-archived path must be a non-empty string")


def execute(context: InvocationContext, request: ArtefactReadArchivedRequest):
    from _common import MissingFileResult
    from _portable.vault_files import read_archived_artefact

    result = read_archived_artefact(context.selected_brain.vault_root, request.path)
    if isinstance(result, MissingFileResult):
        return command_error(
            ArtefactReadArchivedRequest,
            ErrorCode.NOT_FOUND,
            result.message,
            "path",
        )
    if isinstance(result, dict):
        return command_error(
            ArtefactReadArchivedRequest,
            ErrorCode.INVALID_REQUEST,
            str(result["error"]),
            "path",
        )
    return Ok(
        ArtefactReadArchivedRequest.COMMAND_ID,
        ArtefactReadArchivedRequest.COMMAND_VERSION,
        ArtefactReadArchivedPayload(request.path, result),
    )


def decode(payload: Mapping[str, object]) -> ArtefactReadArchivedRequest:
    return decode_required_string(payload, "path", ArtefactReadArchivedRequest)


def catalogue_entry():
    return _catalogue_entry(ArtefactReadArchivedRequest, execute)


def resolver_entry():
    return _resolver_entry(ArtefactReadArchivedRequest, decode)
