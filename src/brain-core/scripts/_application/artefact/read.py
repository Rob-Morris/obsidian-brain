"""Typed ``artefact.read`` command and internal executor."""

from __future__ import annotations

from .._decoding import reject_unexpected
from .._read_support import catalogue_entry as portable_reader_entry

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import Error, ErrorCode, Ok, request_error


@dataclass(frozen=True, slots=True)
class ArtefactReadPayload:
    reference: str
    location: "ArtefactLocation"
    content: str


class ArtefactLocation(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class ArtefactReadRequest:
    COMMAND_ID: ClassVar[str] = "artefact.read"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = ArtefactReadPayload

    reference: str
    location: ArtefactLocation = ArtefactLocation.ACTIVE

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("artefact.read reference must be a non-empty string")
        if not isinstance(self.location, ArtefactLocation):
            raise ValueError("artefact.read location must use ArtefactLocation")


def execute(context: InvocationContext, request: ArtefactReadRequest):
    from _common import MissingFileResult
    from _portable.artefact_read import read_from_vault
    from _portable.vault_files import read_archived_artefact

    if request.location is ArtefactLocation.ARCHIVED:
        result = read_archived_artefact(
            context.selected_brain.vault_root,
            request.reference,
        )
    else:
        result = read_from_vault(
            context.selected_brain.vault_root,
            request.reference,
        )
    if isinstance(result, MissingFileResult):
        return _error(ErrorCode.NOT_FOUND, result.message)
    if isinstance(result, dict) and "error" in result:
        message = str(result["error"])
        if request.location is ArtefactLocation.ARCHIVED:
            return _error(ErrorCode.INVALID_REQUEST, message)
        if "escapes vault root" in message:
            return _error(ErrorCode.INVALID_REQUEST, message)
        if "router" in message.casefold():
            return _error(ErrorCode.CONFLICT, message)
        return _error(ErrorCode.NOT_FOUND, message)
    if not isinstance(result, str):
        raise TypeError("portable artefact reader returned a non-text result")
    return Ok(
        ArtefactReadRequest.COMMAND_ID,
        ArtefactReadRequest.COMMAND_VERSION,
        ArtefactReadPayload(request.reference, request.location, result),
    )


def _error(code: ErrorCode, message: str) -> Error:
    return request_error(ArtefactReadRequest, code, message, "reference")


def decode(payload: Mapping[str, object]) -> ArtefactReadRequest:
    reject_unexpected(payload, {"reference", "location"})
    reference = payload.get("reference")
    if not isinstance(reference, str):
        raise ValueError("reference must be a string")
    location = payload.get("location", ArtefactLocation.ACTIVE.value)
    if not isinstance(location, str):
        raise ValueError("location must be a string")
    try:
        return ArtefactReadRequest(reference, ArtefactLocation(location))
    except ValueError as exc:
        if location not in {item.value for item in ArtefactLocation}:
            raise ValueError("location must be active or archived") from exc
        raise


def catalogue_entry():
    return portable_reader_entry(ArtefactReadRequest, execute)
