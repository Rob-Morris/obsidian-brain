"""Typed exact-path ``vault.read-file`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,

)
from ..context import InvocationContext
from .._decoding import reject_unexpected
from .._response_budget import (ContentRange, TextCursor, bounded_text_result,
    decode_text_cursor, validate_text_window, DEFAULT_TEXT_CHARACTERS, TEXT_WINDOW_DESCRIPTIONS)
from ..results import ErrorCode


@dataclass(frozen=True, slots=True)
class VaultReadFilePayload:
    path: str
    content: str
    revision: str
    range: ContentRange


@dataclass(frozen=True, slots=True)
class VaultReadFileRequest:
    COMMAND_ID: ClassVar[str] = "vault.read-file"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = VaultReadFilePayload
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = TEXT_WINDOW_DESCRIPTIONS

    path: str
    cursor: TextCursor | None = None
    max_characters: int = DEFAULT_TEXT_CHARACTERS

    def __post_init__(self) -> None:
        validate_text_window(self.cursor, self.max_characters)
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("vault.read-file path must be a non-empty string")


def read_result(context: InvocationContext, request: VaultReadFileRequest):
    from _common import MissingFileResult
    from _portable.vault_files import read_vault_file

    result = read_vault_file(context.selected_brain.vault_root, request.path)
    if isinstance(result, MissingFileResult):
        return command_error(
            VaultReadFileRequest,
            ErrorCode.NOT_FOUND,
            result.message,
            "path",
        )
    if isinstance(result, dict):
        return command_error(
            VaultReadFileRequest,
            ErrorCode.INVALID_REQUEST,
            str(result["error"]),
            "path",
        )
    from ..preparation import observe_document_read

    bounded = bounded_text_result(
        VaultReadFileRequest, result, result.revision,
        cursor=request.cursor, max_characters=request.max_characters,
        payload=lambda content, window: VaultReadFilePayload(
            request.path, content, result.revision, window,
        ),
    )

    return observe_document_read(context, bounded, result)


def execute(context, request):
    from ..preparation import execute_prepared_read

    return execute_prepared_read(context, request, read_result)


def decode(payload: Mapping[str, object]) -> VaultReadFileRequest:
    reject_unexpected(payload, {"path", "cursor", "max_characters"})
    return VaultReadFileRequest(payload.get("path"),
        decode_text_cursor(payload.get("cursor")),
        payload.get("max_characters", DEFAULT_TEXT_CHARACTERS))


def _reader_entry():
    return _catalogue_entry(VaultReadFileRequest, execute)


def catalogue_entry():
    from dataclasses import replace
    from ..preparation import ResultReadPreparation

    return replace(_reader_entry(), preparation=ResultReadPreparation(read_result))
