"""Typed ``plugin.read`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._named_documents import (
    catalogue_entry as _catalogue_entry,
    command_error,
    decode_reference,
    read_portable,
    resolver_entry as _resolver_entry,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class PluginReadPayload:
    name: str
    content: str


@dataclass(frozen=True, slots=True)
class PluginReadRequest:
    COMMAND_ID: ClassVar[str] = "plugin.read"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = PluginReadPayload

    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("plugin.read reference must be a non-empty string")


def execute(context: InvocationContext, request: PluginReadRequest):
    try:
        result = read_portable(context.selected_brain.vault_root, "plugin", request.reference)
    except FileNotFoundError as exc:
        return command_error(PluginReadRequest, ErrorCode.CONFLICT, str(exc), None)
    if isinstance(result, dict):
        return command_error(
            PluginReadRequest,
            ErrorCode.NOT_FOUND,
            str(result["error"]),
            "reference",
        )
    metadata, content = result
    if not isinstance(content, str):
        return command_error(
            PluginReadRequest,
            ErrorCode.NOT_FOUND,
            content.message,
            "reference",
        )
    return Ok(
        PluginReadRequest.COMMAND_ID,
        PluginReadRequest.COMMAND_VERSION,
        PluginReadPayload(metadata["name"], content),
    )


def decode(payload: Mapping[str, object]) -> PluginReadRequest:
    return decode_reference(payload, PluginReadRequest)


def catalogue_entry():
    return _catalogue_entry(PluginReadRequest, execute)


def resolver_entry():
    return _resolver_entry(PluginReadRequest, decode)
