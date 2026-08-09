"""Typed ``template.delete-section`` owner."""

from .._document_edit import NamedDeleteSectionRequest, named_delete_bindings


class TemplateDeleteSectionRequest(NamedDeleteSectionRequest):
    __slots__ = ()
    COMMAND_ID = "template.delete-section"


execute, decode, catalogue_entry, resolver_entry = named_delete_bindings(
    TemplateDeleteSectionRequest, resource="template"
)
