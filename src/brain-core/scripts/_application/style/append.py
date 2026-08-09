"""Typed ``style.append`` owner."""

from .._document_edit import NamedStructuralRequest, named_structural_bindings


class StyleAppendRequest(NamedStructuralRequest):
    __slots__ = ()
    COMMAND_ID = "style.append"


execute, decode, catalogue_entry, resolver_entry = named_structural_bindings(
    StyleAppendRequest, resource="style", operation="append"
)
