"""Typed exact-path ``vault.read-file`` command and internal executor."""

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
class VaultReadFilePayload:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class VaultReadFileRequest:
    COMMAND_ID: ClassVar[str] = "vault.read-file"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = VaultReadFilePayload

    path: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("vault.read-file path must be a non-empty string")


def execute(context: InvocationContext, request: VaultReadFileRequest):
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
    return Ok(
        VaultReadFileRequest.COMMAND_ID,
        VaultReadFileRequest.COMMAND_VERSION,
        VaultReadFilePayload(request.path, result),
    )


def decode(payload: Mapping[str, object]) -> VaultReadFileRequest:
    return decode_required_string(payload, "path", VaultReadFileRequest)


def catalogue_entry():
    return _catalogue_entry(VaultReadFileRequest, execute)


def resolver_entry():
    return _resolver_entry(VaultReadFileRequest, decode)
