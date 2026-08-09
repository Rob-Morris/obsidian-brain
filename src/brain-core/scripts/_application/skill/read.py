"""Typed ``skill.read`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
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


class SkillSource(str, Enum):
    CORE = "core"
    USER = "user"


@dataclass(frozen=True, slots=True)
class SkillReadPayload:
    name: str
    source: SkillSource
    content: str


@dataclass(frozen=True, slots=True)
class SkillReadRequest:
    COMMAND_ID: ClassVar[str] = "skill.read"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SkillReadPayload

    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("skill.read reference must be a non-empty string")


def execute(context: InvocationContext, request: SkillReadRequest):
    try:
        result = read_portable(context.selected_brain.vault_root, "skill", request.reference)
    except FileNotFoundError as exc:
        return command_error(SkillReadRequest, ErrorCode.CONFLICT, str(exc), None)
    if isinstance(result, dict):
        return command_error(
            SkillReadRequest,
            ErrorCode.NOT_FOUND,
            str(result["error"]),
            "reference",
        )
    metadata, content = result
    if not isinstance(content, str):
        return command_error(
            SkillReadRequest,
            ErrorCode.NOT_FOUND,
            content.message,
            "reference",
        )
    return Ok(
        SkillReadRequest.COMMAND_ID,
        SkillReadRequest.COMMAND_VERSION,
        SkillReadPayload(metadata["name"], SkillSource(metadata["source"]), content),
    )


def decode(payload: Mapping[str, object]) -> SkillReadRequest:
    return decode_reference(payload, SkillReadRequest)


def catalogue_entry():
    return _catalogue_entry(SkillReadRequest, execute)


def resolver_entry():
    return _resolver_entry(SkillReadRequest, decode)
