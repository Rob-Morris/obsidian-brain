"""Typed ``artefact.read`` command and internal executor."""

from __future__ import annotations

from .._decoding import reject_unexpected
from .._read_support import catalogue_entry as portable_reader_entry

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from ..context import InvocationContext
from .._response_budget import (ContentRange, TextCursor, bounded_text_result,
    decode_text_cursor, validate_text_window, DEFAULT_TEXT_CHARACTERS, TEXT_WINDOW_DESCRIPTIONS)
from ..results import Error, ErrorCode, request_error


@dataclass(frozen=True, slots=True)
class ArtefactReadPayload:
    reference: str
    location: "ArtefactLocation"
    content: str
    revision: str
    range: ContentRange


class ArtefactLocation(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class ArtefactReadRequest:
    COMMAND_ID: ClassVar[str] = "artefact.read"
    COMMAND_VERSION: ClassVar[int] = 4
    RESULT_TYPE: ClassVar[type] = ArtefactReadPayload
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = TEXT_WINDOW_DESCRIPTIONS

    reference: str
    location: ArtefactLocation = ArtefactLocation.ACTIVE
    cursor: TextCursor | None = None
    max_characters: int = DEFAULT_TEXT_CHARACTERS

    def __post_init__(self) -> None:
        validate_text_window(self.cursor, self.max_characters)
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("artefact.read reference must be a non-empty string")
        if not isinstance(self.location, ArtefactLocation):
            raise ValueError("artefact.read location must use ArtefactLocation")


def read_result(context: InvocationContext, request: ArtefactReadRequest):
    from _common import MissingFileResult, PersistedDocumentContent
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
    if not isinstance(result, PersistedDocumentContent):
        raise TypeError("portable artefact reader returned non-persisted document text")
    from ..preparation import observe_document_read

    bounded = bounded_text_result(
        ArtefactReadRequest, result, result.revision,
        cursor=request.cursor, max_characters=request.max_characters,
        payload=lambda content, window: ArtefactReadPayload(
            request.reference, request.location, content, result.revision, window,
        ),
    )

    return observe_document_read(context, bounded, result)


def execute(context, request):
    from ..preparation import execute_prepared_read

    return execute_prepared_read(context, request, read_result)


def _error(code: ErrorCode, message: str) -> Error:
    return request_error(ArtefactReadRequest, code, message, "reference")


def decode(payload: Mapping[str, object]) -> ArtefactReadRequest:
    reject_unexpected(payload, {"reference", "location", "cursor", "max_characters"})
    reference = payload.get("reference")
    if not isinstance(reference, str):
        raise ValueError("reference must be a string")
    location = payload.get("location", ArtefactLocation.ACTIVE.value)
    if not isinstance(location, str):
        raise ValueError("location must be a string")
    try:
        return ArtefactReadRequest(reference, ArtefactLocation(location),
            decode_text_cursor(payload.get("cursor")),
            payload.get("max_characters", DEFAULT_TEXT_CHARACTERS))
    except ValueError as exc:
        if location not in {item.value for item in ArtefactLocation}:
            raise ValueError("location must be active or archived") from exc
        raise


def _reader_entry():
    return portable_reader_entry(ArtefactReadRequest, execute)


def catalogue_entry():
    from dataclasses import replace
    from ..preparation import ResultReadPreparation

    return replace(_reader_entry(), preparation=ResultReadPreparation(read_result))
