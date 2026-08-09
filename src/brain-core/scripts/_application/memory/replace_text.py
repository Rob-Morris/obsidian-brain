"""Typed ``memory.replace-text`` owner."""

from .._document_edit import NamedReplaceTextRequest, named_replace_bindings


class MemoryReplaceTextRequest(NamedReplaceTextRequest):
    __slots__ = ()
    COMMAND_ID = "memory.replace-text"


execute, decode, catalogue_entry, resolver_entry = named_replace_bindings(
    MemoryReplaceTextRequest, resource="memory"
)
