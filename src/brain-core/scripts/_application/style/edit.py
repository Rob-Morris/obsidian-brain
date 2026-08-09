"""Typed ``style.edit`` owner."""

from .._document_edit import NamedStructuralRequest, named_structural_bindings


class StyleEditRequest(NamedStructuralRequest):
    __slots__ = ()
    COMMAND_ID = "style.edit"


execute, decode, catalogue_entry, resolver_entry = named_structural_bindings(
    StyleEditRequest, resource="style", operation="edit"
)
