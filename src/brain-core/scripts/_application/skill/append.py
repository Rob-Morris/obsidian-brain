"""Typed ``skill.append`` owner."""

from .._document_edit import NamedStructuralRequest, named_structural_bindings


class SkillAppendRequest(NamedStructuralRequest):
    __slots__ = ()
    COMMAND_ID = "skill.append"


execute, decode, catalogue_entry, resolver_entry = named_structural_bindings(
    SkillAppendRequest, resource="skill", operation="append"
)
