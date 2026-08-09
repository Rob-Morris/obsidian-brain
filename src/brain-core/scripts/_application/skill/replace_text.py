"""Typed ``skill.replace-text`` owner."""

from .._document_edit import NamedReplaceTextRequest, named_replace_bindings


class SkillReplaceTextRequest(NamedReplaceTextRequest):
    __slots__ = ()
    COMMAND_ID = "skill.replace-text"


execute, decode, catalogue_entry, resolver_entry = named_replace_bindings(
    SkillReplaceTextRequest, resource="skill"
)
