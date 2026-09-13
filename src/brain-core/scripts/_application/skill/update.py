"""Typed ``skill.update`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._decoding import reject_unexpected
from ..context import InvocationContext
from ._support import execute_mutation, git_skill_catalogue_entry
from ._preparation import skill_execution_options
from ._types import SkillMutationPayload


@dataclass(frozen=True, slots=True)
class SkillUpdateRequest:
    COMMAND_ID: ClassVar[str] = "skill.update"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SkillMutationPayload

    name: str
    to_commit: str | None = None
    replace_conflict: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("skill.update name must be a non-empty string")
        if self.to_commit is not None and (
            not isinstance(self.to_commit, str) or not self.to_commit.strip()
        ):
            raise ValueError("skill.update to_commit must be non-empty or null")
        if not isinstance(self.replace_conflict, bool):
            raise ValueError("skill.update replace_conflict must be a boolean")


def execute(context: InvocationContext, request: SkillUpdateRequest):
    from _skill_library import update_skill

    return execute_mutation(
        context,
        request,
        lambda: update_skill(
            context.selected_brain.vault_root,
            name=request.name,
            to_commit=request.to_commit,
            replace_conflict=request.replace_conflict,
            **skill_execution_options(context, request),
        ),
    )


def decode(payload: Mapping[str, object]) -> SkillUpdateRequest:
    reject_unexpected(payload, {"name", "to_commit", "replace_conflict"})
    name = payload.get("name")
    to_commit = payload.get("to_commit")
    replace = payload.get("replace_conflict", False)
    if not isinstance(name, str):
        raise ValueError("name is required and must be a string")
    if to_commit is not None and not isinstance(to_commit, str):
        raise ValueError("to_commit must be a string or null")
    if not isinstance(replace, bool):
        raise ValueError("replace_conflict must be a boolean")
    return SkillUpdateRequest(name, to_commit, replace)


def catalogue_entry():
    return git_skill_catalogue_entry(SkillUpdateRequest, execute)
