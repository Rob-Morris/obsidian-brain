"""Typed ``style.read`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._named_documents import read_portable
from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
    decode_reference,
    resolver_entry as _resolver_entry,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class StyleReadPayload:
    name: str
    content: str


@dataclass(frozen=True, slots=True)
class StyleReadRequest:
    COMMAND_ID: ClassVar[str] = "style.read"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = StyleReadPayload

    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("style.read reference must be a non-empty string")


def execute(context: InvocationContext, request: StyleReadRequest):
    try:
        result = read_portable(context.selected_brain.vault_root, "style", request.reference)
    except FileNotFoundError as exc:
        return command_error(StyleReadRequest, ErrorCode.CONFLICT, str(exc), None)
    if isinstance(result, dict):
        return command_error(
            StyleReadRequest,
            ErrorCode.NOT_FOUND,
            str(result["error"]),
            "reference",
        )
    metadata, content = result
    if not isinstance(content, str):
        return command_error(
            StyleReadRequest,
            ErrorCode.NOT_FOUND,
            content.message,
            "reference",
        )
    return Ok(
        StyleReadRequest.COMMAND_ID,
        StyleReadRequest.COMMAND_VERSION,
        StyleReadPayload(metadata["name"], content),
    )


def decode(payload: Mapping[str, object]) -> StyleReadRequest:
    return decode_reference(payload, StyleReadRequest)


def catalogue_entry():
    return _catalogue_entry(StyleReadRequest, execute)


def resolver_entry():
    return _resolver_entry(StyleReadRequest, decode)
