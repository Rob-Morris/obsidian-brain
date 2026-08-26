"""Typed read-only ``skill.list`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._decoding import reject_unexpected
from .._read_support import catalogue_entry as read_catalogue_entry, command_error
from ..context import InvocationContext
from ..results import ErrorCode, Ok
from ._types import SkillStatusPayload, status_payload


@dataclass(frozen=True, slots=True)
class SkillListRequest:
    COMMAND_ID: ClassVar[str] = "skill.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SkillStatusPayload

    name: str | None = None

    def __post_init__(self) -> None:
        if self.name is not None and (
            not isinstance(self.name, str) or not self.name.strip()
        ):
            raise ValueError("skill.list name must be a non-empty string or null")


def execute(context: InvocationContext, request: SkillListRequest):
    from _skill_library import SkillLibraryError, list_skill_status

    try:
        rows = list_skill_status(
            context.selected_brain.vault_root,
            name=request.name,
            refresh=False,
        )
    except (OSError, ValueError, SkillLibraryError) as exc:
        return command_error(SkillListRequest, ErrorCode.CONFLICT, str(exc), "name")
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        status_payload(rows, refreshed=False),
    )


def decode(payload: Mapping[str, object]) -> SkillListRequest:
    reject_unexpected(payload, {"name"})
    name = payload.get("name")
    if name is not None and not isinstance(name, str):
        raise ValueError("name must be a string")
    return SkillListRequest(name)


def catalogue_entry():
    return read_catalogue_entry(SkillListRequest, execute)
