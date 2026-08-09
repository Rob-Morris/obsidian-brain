"""Typed ``skill.delete-section`` owner."""

from .._document_edit import NamedDeleteSectionRequest, named_delete_bindings


class SkillDeleteSectionRequest(NamedDeleteSectionRequest):
    __slots__ = ()
    COMMAND_ID = "skill.delete-section"


execute, decode, catalogue_entry, resolver_entry = named_delete_bindings(
    SkillDeleteSectionRequest, resource="skill"
)
