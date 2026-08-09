"""Typed ``skill.prepend`` owner."""

from .._document_edit import NamedStructuralRequest, named_structural_bindings


class SkillPrependRequest(NamedStructuralRequest):
    __slots__ = ()
    COMMAND_ID = "skill.prepend"


execute, decode, catalogue_entry, resolver_entry = named_structural_bindings(
    SkillPrependRequest, resource="skill", operation="prepend"
)
