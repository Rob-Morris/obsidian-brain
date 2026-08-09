"""Typed ``template.append`` owner."""

from .._document_edit import NamedStructuralRequest, named_structural_bindings


class TemplateAppendRequest(NamedStructuralRequest):
    __slots__ = ()
    COMMAND_ID = "template.append"


execute, decode, catalogue_entry, resolver_entry = named_structural_bindings(
    TemplateAppendRequest, resource="template", operation="append"
)
