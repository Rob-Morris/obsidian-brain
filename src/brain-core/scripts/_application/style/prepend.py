"""Typed ``style.prepend`` owner."""

from .._document_edit import NamedStructuralRequest, named_structural_bindings


class StylePrependRequest(NamedStructuralRequest):
    __slots__ = ()
    COMMAND_ID = "style.prepend"


execute, decode, catalogue_entry, resolver_entry = named_structural_bindings(
    StylePrependRequest, resource="style", operation="prepend"
)
