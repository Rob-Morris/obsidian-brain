"""Typed ``skill.edit`` owner."""

from .._document_edit import NamedStructuralRequest, named_structural_bindings


class SkillEditRequest(NamedStructuralRequest):
    __slots__ = ()
    COMMAND_ID = "skill.edit"


execute, decode, catalogue_entry, resolver_entry = named_structural_bindings(
    SkillEditRequest, resource="skill", operation="edit"
)
