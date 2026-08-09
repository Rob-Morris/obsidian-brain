"""Typed ``memory.prepend`` owner."""

from .._document_edit import NamedStructuralRequest, named_structural_bindings


class MemoryPrependRequest(NamedStructuralRequest):
    __slots__ = ()
    COMMAND_ID = "memory.prepend"


execute, decode, catalogue_entry, resolver_entry = named_structural_bindings(
    MemoryPrependRequest, resource="memory", operation="prepend"
)
