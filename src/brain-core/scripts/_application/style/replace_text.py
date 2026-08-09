"""Typed ``style.replace-text`` owner."""

from .._document_edit import NamedReplaceTextRequest, named_replace_bindings


class StyleReplaceTextRequest(NamedReplaceTextRequest):
    __slots__ = ()
    COMMAND_ID = "style.replace-text"


execute, decode, catalogue_entry, resolver_entry = named_replace_bindings(
    StyleReplaceTextRequest, resource="style"
)
