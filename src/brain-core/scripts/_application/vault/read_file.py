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
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class VaultReadFilePayload:
    path: str
    content: str
    revision: str


@dataclass(frozen=True, slots=True)
class VaultReadFileRequest:
    COMMAND_ID: ClassVar[str] = "vault.read-file"
    COMMAND_VERSION: ClassVar[int] = 3
    RESULT_TYPE: ClassVar[type] = VaultReadFilePayload

    path: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("vault.read-file path must be a non-empty string")


def read_result(context: InvocationContext, request: VaultReadFileRequest):
    from _common import MissingFileResult
    from _portable.vault_files import read_vault_file

    result = read_vault_file(context.selected_brain.vault_root, request.path, convert_lossless=True)
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
    from .._text_warnings import read_conversion_warnings

    complete = Ok(
        request.COMMAND_ID, request.COMMAND_VERSION,
        VaultReadFilePayload(request.path, result, result.revision),
        warnings=read_conversion_warnings(context, result),
    )
    return observe_document_read(context, complete, result)


def execute(context, request):
    from ..preparation import execute_prepared_read

    return execute_prepared_read(context, request, read_result)


def decode(payload: Mapping[str, object]) -> VaultReadFileRequest:
    reject_unexpected(payload, {"path"})
    return VaultReadFileRequest(payload.get("path"))


def _reader_entry():
    return _catalogue_entry(VaultReadFileRequest, execute)


def catalogue_entry():
    from dataclasses import replace
    from ..preparation import ResultReadPreparation

    return replace(_reader_entry(), preparation=ResultReadPreparation(read_result))
