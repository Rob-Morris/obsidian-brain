"""Typed ``memory.edit`` owner."""

from .._document_edit import NamedStructuralRequest, named_structural_bindings


class MemoryEditRequest(NamedStructuralRequest):
    __slots__ = ()
    COMMAND_ID = "memory.edit"


execute, decode, catalogue_entry, resolver_entry = named_structural_bindings(
    MemoryEditRequest, resource="memory", operation="edit"
)
