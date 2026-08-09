"""Typed ``template.edit`` owner."""

from .._document_edit import NamedStructuralRequest, named_structural_bindings


class TemplateEditRequest(NamedStructuralRequest):
    __slots__ = ()
    COMMAND_ID = "template.edit"


execute, decode, catalogue_entry, resolver_entry = named_structural_bindings(
    TemplateEditRequest, resource="template", operation="edit"
)
