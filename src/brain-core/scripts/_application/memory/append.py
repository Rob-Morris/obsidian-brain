"""Typed ``memory.append`` owner."""

from .._document_edit import NamedStructuralRequest, named_structural_bindings


class MemoryAppendRequest(NamedStructuralRequest):
    __slots__ = ()
    COMMAND_ID = "memory.append"


execute, decode, catalogue_entry, resolver_entry = named_structural_bindings(
    MemoryAppendRequest, resource="memory", operation="append"
)
