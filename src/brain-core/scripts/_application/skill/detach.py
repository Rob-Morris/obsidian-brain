"""Typed ``skill.detach`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._decoding import reject_unexpected
from ..context import InvocationContext
from ._support import execute_mutation, mutation_catalogue_entry
from ._types import SkillMutationPayload


@dataclass(frozen=True, slots=True)
class SkillDetachRequest:
    COMMAND_ID: ClassVar[str] = "skill.detach"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SkillMutationPayload

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("skill.detach name must be a non-empty string")


def execute(context: InvocationContext, request: SkillDetachRequest):
    from _skill_library import detach_skill

    return execute_mutation(
        context,
        request,
        lambda: detach_skill(context.selected_brain.vault_root, name=request.name),
    )


def decode(payload: Mapping[str, object]) -> SkillDetachRequest:
    reject_unexpected(payload, {"name"})
    name = payload.get("name")
    if not isinstance(name, str):
        raise ValueError("name is required and must be a string")
    return SkillDetachRequest(name)


def catalogue_entry():
    return mutation_catalogue_entry(SkillDetachRequest, execute)
