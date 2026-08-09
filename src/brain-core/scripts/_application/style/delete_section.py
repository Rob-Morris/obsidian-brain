"""Typed ``style.delete-section`` owner."""

from .._document_edit import NamedDeleteSectionRequest, named_delete_bindings


class StyleDeleteSectionRequest(NamedDeleteSectionRequest):
    __slots__ = ()
    COMMAND_ID = "style.delete-section"


execute, decode, catalogue_entry, resolver_entry = named_delete_bindings(
    StyleDeleteSectionRequest, resource="style"
)
