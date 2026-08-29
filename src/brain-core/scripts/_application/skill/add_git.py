"""Typed ``skill.add-git`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ._support import execute_mutation, mutation_catalogue_entry
from ._types import SkillMutationPayload


@dataclass(frozen=True, slots=True)
class SkillAddGitRequest:
    COMMAND_ID: ClassVar[str] = "skill.add-git"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SkillMutationPayload

    repository: str
    skill_path: str
    configured_ref: str = "HEAD"

    def __post_init__(self) -> None:
        for field in ("repository", "skill_path", "configured_ref"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"skill.add-git {field} must be a non-empty string")


def execute(context: InvocationContext, request: SkillAddGitRequest):
    from _skill_library import add_git_skill

    return execute_mutation(
        context,
        request,
        lambda: add_git_skill(
            context.selected_brain.vault_root,
            repository=request.repository,
            skill_path=request.skill_path,
            configured_ref=request.configured_ref,
        ),
    )


def decode(payload: Mapping[str, object]) -> SkillAddGitRequest:
    from .._decoding import reject_unexpected

    reject_unexpected(payload, {"repository", "skill_path", "configured_ref"})
    repository = payload.get("repository")
    skill_path = payload.get("skill_path")
    configured_ref = payload.get("configured_ref", "HEAD")
    if not all(isinstance(item, str) for item in (repository, skill_path, configured_ref)):
        raise ValueError("repository, skill_path and configured_ref must be strings")
    return SkillAddGitRequest(repository, skill_path, configured_ref)


def catalogue_entry():
    return mutation_catalogue_entry(SkillAddGitRequest, execute)
