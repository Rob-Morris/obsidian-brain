"""Lazy public façade for the Brain skill-library lifecycle domain."""

from __future__ import annotations

__all__ = [
    "SkillLibraryError",
    "SkillMutationOutcomeUncertain",
    "add_git_skill",
    "detach_skill",
    "list_skill_status",
    "materialise_core_skill_for_edit",
    "preview_core_override_reconciliation",
    "reconcile_core_overrides",
    "update_skill",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    from . import service

    return getattr(service, name)
