"""Typed ``memory.delete-section`` owner."""

from .._document_edit import NamedDeleteSectionRequest, named_delete_bindings


class MemoryDeleteSectionRequest(NamedDeleteSectionRequest):
    __slots__ = ()
    COMMAND_ID = "memory.delete-section"


execute, decode, catalogue_entry, resolver_entry = named_delete_bindings(
    MemoryDeleteSectionRequest, resource="memory"
)
