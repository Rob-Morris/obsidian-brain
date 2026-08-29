"""Typed refreshing ``skill.status`` owner."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import ClassVar, Mapping

from .._decoding import reject_unexpected
from .._mutation_support import contributor_mutation_entry, no_effect_error
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import ErrorCode, Ok
from ._types import SkillStatusPayload, status_payload


@dataclass(frozen=True, slots=True)
class SkillStatusRequest:
    COMMAND_ID: ClassVar[str] = "skill.status"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SkillStatusPayload

    name: str | None = None

    def __post_init__(self) -> None:
        if self.name is not None and (
            not isinstance(self.name, str) or not self.name.strip()
        ):
            raise ValueError("skill.status name must be a non-empty string or null")


def execute(context: InvocationContext, request: SkillStatusRequest):
    from _skill_library import SkillLibraryError, list_skill_status

    if context.dry_run:
        return no_effect_error(
            type(request), ErrorCode.INVALID_REQUEST, "skill.status does not support dry-run"
        )
    try:
        rows = list_skill_status(
            context.selected_brain.vault_root,
            name=request.name,
            refresh=True,
        )
    except (OSError, ValueError, SkillLibraryError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc), "name")
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        status_payload(rows, refreshed=True),
        (CommittedEffect(request.COMMAND_ID, ".brain/skill-sources.json"),),
    )


def decode(payload: Mapping[str, object]) -> SkillStatusRequest:
    reject_unexpected(payload, {"name"})
    name = payload.get("name")
    if name is not None and not isinstance(name, str):
        raise ValueError("name must be a string")
    return SkillStatusRequest(name)


def catalogue_entry():
    return replace(
        contributor_mutation_entry(SkillStatusRequest, execute),
        required_providers=("git_remote",),
        open_world=True,
    )
