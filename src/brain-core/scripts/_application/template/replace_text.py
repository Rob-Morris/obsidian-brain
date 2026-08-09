"""Typed ``template.replace-text`` owner."""

from .._document_edit import NamedReplaceTextRequest, named_replace_bindings


class TemplateReplaceTextRequest(NamedReplaceTextRequest):
    __slots__ = ()
    COMMAND_ID = "template.replace-text"


execute, decode, catalogue_entry, resolver_entry = named_replace_bindings(
    TemplateReplaceTextRequest, resource="template"
)
