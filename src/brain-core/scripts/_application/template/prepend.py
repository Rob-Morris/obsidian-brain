"""Typed ``template.prepend`` owner."""

from .._document_edit import NamedStructuralRequest, named_structural_bindings


class TemplatePrependRequest(NamedStructuralRequest):
    __slots__ = ()
    COMMAND_ID = "template.prepend"


execute, decode, catalogue_entry, resolver_entry = named_structural_bindings(
    TemplatePrependRequest, resource="template", operation="prepend"
)
